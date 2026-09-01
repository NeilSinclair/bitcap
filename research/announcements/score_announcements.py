"""Classify lab announcements and compute their transmission score.

The split matters and is deliberate. The LLM reads one announcement and reports
what it is plus which mechanisms and categories it touches, each tag carrying a
verbatim quote. It never emits a score. The score is computed here from
``config/scoring.yaml``, deterministically, so every number can be argued line
by line and changed without touching code.

Cost is recorded at the call site: each request's tokens and price are written
to disk as the run proceeds, never reconstructed afterwards. Results are cached
per URL so a re-run is idempotent and free.

Usage:
    python research/announcements/score_announcements.py [--limit N] [--model M]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "research" / "papers"))
from llm_byline import PRICES, load_env  # noqa: E402

PROMPT_VERSION = "v3"
PROMPT = ROOT / "prompts" / "announcement_scoring" / f"{PROMPT_VERSION}.md"
MECHANISMS = ROOT / "config" / "mechanisms.yaml"
CATEGORIES = ROOT / "config" / "categories.yaml"
SCORING = ROOT / "config" / "scoring.yaml"
ARTICLES = ROOT / "research" / "docs" / "announcements.json"
CACHE = ROOT / "research" / "docs" / "announcement_scores" / PROMPT_VERSION
OUT = ROOT / "research" / "docs" / f"scored_announcements_{PROMPT_VERSION}.json"
COST = ROOT / "research" / "docs" / "announcement_cost.json"

TAG = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "sign": {"type": "string", "enum": ["positive", "negative", "mixed"]},
        "magnitude": {"type": "string", "enum": ["high", "medium", "low"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["id", "sign", "magnitude", "confidence", "reason", "quote"],
    "additionalProperties": False,
}

CATEGORY_TAG = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "sign": {"type": "string", "enum": ["positive", "negative", "mixed"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["id", "sign", "confidence", "reason", "quote"],
    "additionalProperties": False,
}

def build_schema(include_is_signal: bool = True) -> dict:
    """Build the output schema.

    Args:
        include_is_signal: Whether to ask for the `is_signal` flag at all.
            It does not affect the score, and a stability probe found the model
            dropping its mechanism tags in 86% of the runs where it set the flag
            false -- so the field is suspected of acting as an escape hatch from
            the tagging work rather than as a report on it.

    Returns:
        JSON schema for the structured output.
    """
    properties = {
        "event_type": {"type": "string"},
        "summary": {"type": "string"},
        "mechanisms": {"type": "array", "items": TAG},
        "categories": {"type": "array", "items": CATEGORY_TAG},
        "notable": {"type": "boolean"},
        "notable_reason": {"type": "string"},
    }
    if include_is_signal:
        properties = {"is_signal": {"type": "boolean"}, **properties}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


SCHEMA = build_schema()


def vocabularies() -> tuple[str, str, set[str], set[str]]:
    """Render the mechanism and category vocabularies for the prompt.

    Returns:
        Tuple of (mechanism block, category block, valid mechanism ids, valid
        category ids). The id sets are used to reject hallucinated tags.
    """
    mech = yaml.safe_load(MECHANISMS.read_text())["mechanisms"]
    cats = yaml.safe_load(CATEGORIES.read_text())["categories"]

    mech_block = "\n".join(
        f"- `{m['id']}` — {m['label']}. {' '.join(m['description'].split())}"
        for m in mech
    )
    cat_block = "\n".join(
        f"- `{c['id']}` — {c['label']}. {' '.join(c['definition'].split())}"
        for c in cats
        if c.get("lab_signal_routable", True)
    )
    return mech_block, cat_block, {m["id"] for m in mech}, {c["id"] for c in cats}


def build_prompt(article: dict) -> tuple[str, str]:
    """Assemble the system and user prompts for one announcement.

    Args:
        article: Article record from announcements.json.

    Returns:
        Tuple of (system prompt, user prompt).
    """
    mech_block, cat_block, _, _ = vocabularies()
    system = (
        PROMPT.read_text(encoding="utf-8")
        .replace("{mechanisms}", mech_block)
        .replace("{categories}", cat_block)
    )
    user = (
        f"lab: {article['lab']}\n"
        f"date: {article['date']}\n"
        f"url: {article['url']}\n"
        f"text_source: {article['text_source']}\n\n"
        f"---\n{article['text']}\n---"
    )
    return system, user


def score_of(result: dict, rules: dict) -> tuple[float, str]:
    """Compute the transmission score from the model's tags.

    Everything multiplies. Within a tag, magnitude times confidence is an
    expected magnitude. Across the two axes, event weight times mechanism
    strength reads as "importance of this kind of event, times the strength of
    the evidence found" -- and, critically, makes a non-zero score impossible
    without at least one quote-backed mechanism tag.

    The model's own `is_signal` flag is ignored here; see the note in the body.

    Args:
        result: Parsed model output.
        rules: Parsed config/scoring.yaml.

    Returns:
        Tuple of (score out of 100, band label).
    """
    # `is_signal` is deliberately NOT consulted. It was a hard veto until a
    # stability check found it flipping on 9% of re-classified items, zeroing
    # articles that carried maximum-strength, quote-backed tags. A single
    # unstable boolean must not override the evidence; an item with no tags
    # scores zero through the arithmetic anyway.
    event = rules["event_weight"].get(result["event_type"], 0)

    strongest = 0
    for tag in result["mechanisms"]:
        strongest = max(
            strongest,
            rules["magnitude"][tag["magnitude"]] * rules["confidence"][tag["confidence"]],
        )

    score = round(
        100
        * (event / rules["max_event_weight"])
        * (strongest / rules["max_mechanism"]),
        1,
    )
    band = next(b["label"] for b in rules["bands"] if score >= b["min"])
    return score, band


def drop_unknown_tags(result: dict, mech_ids: set[str], cat_ids: set[str]) -> list[str]:
    """Remove tags naming ids that do not exist, reporting what was dropped.

    A hallucinated id would otherwise propagate into the register as though it
    were a real transmission path.

    Args:
        result: Parsed model output, modified in place.
        mech_ids: Valid mechanism ids.
        cat_ids: Valid category ids.

    Returns:
        The invalid ids that were removed.
    """
    dropped = []
    for key, valid in (("mechanisms", mech_ids), ("categories", cat_ids)):
        kept = []
        for tag in result[key]:
            if tag["id"] in valid:
                kept.append(tag)
            else:
                dropped.append(f"{key}:{tag['id']}")
        result[key] = kept
    return dropped


def classify(
    client: anthropic.Anthropic, model: str, article: dict
) -> tuple[dict, dict]:
    """Classify one announcement, returning the result and its cost record.

    Args:
        client: Anthropic client.
        model: Model id.
        article: Article record.

    Returns:
        Tuple of (parsed result, cost record for this single call).

    Raises:
        RuntimeError: On refusal or truncated output, rather than returning a
            partial classification that would look like a real one.
    """
    system, user = build_prompt(article)
    started = time.time()
    with client.messages.stream(
        model=model,
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": user}],
        # NOTE: there is no temperature to set. `temperature` is deprecated on
        # the Claude 5 family -- absent from the SDK signature, and rejected by
        # the API with "`temperature` is deprecated for this model". The
        # run-to-run variance measured on this task is therefore inherent to the
        # model as exposed, not a sampling parameter left at a bad default, and
        # it has to be handled architecturally (repeat and vote) rather than
        # configured away. See research/docs/variance_v3.json.
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError(f"refused: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("truncated output; raise max_tokens")

    text = next(b.text for b in response.content if b.type == "text")
    usage = response.usage
    in_rate, out_rate = PRICES[model]
    cost = {
        "url": article["url"],
        "model": model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "usd": round(
            usage.input_tokens / 1e6 * in_rate + usage.output_tokens / 1e6 * out_rate,
            6,
        ),
        "seconds": round(time.time() - started, 2),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    return json.loads(text), cost


def classify_one(
    client: anthropic.Anthropic,
    model: str,
    article: dict,
    mech_ids: set[str],
    cat_ids: set[str],
) -> tuple[dict, dict | None, dict | None]:
    """Classify one article, or load it from cache.

    Safe to call from a worker thread: it touches only this article's own cache
    file and returns everything else for the caller to record under a lock.

    Args:
        client: Anthropic client (thread-safe).
        model: Model id.
        article: Article record.
        mech_ids: Valid mechanism ids.
        cat_ids: Valid category ids.

    Returns:
        Tuple of (result, cost record or None if cached, failure or None).
    """
    key = re.sub(r"[^A-Za-z0-9]+", "_", article["url"])[:140] + ".json"
    cached = CACHE / key
    if cached.exists():
        return json.loads(cached.read_text()), None, None

    try:
        result, cost = classify(client, model, article)
    except Exception as exc:  # network, refusal, malformed JSON
        return {}, None, {"url": article["url"], "error": str(exc)}

    result["dropped_tags"] = drop_unknown_tags(result, mech_ids, cat_ids)
    cached.write_text(json.dumps(result))
    return result, cost, None


def main() -> None:
    """Score every fetched announcement and write the register."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--workers",
        type=int,
        default=12,
        help="concurrent requests; the work is network-bound, not CPU-bound",
    )
    parser.add_argument(
        "--only",
        help="path to a JSON list of URLs; classify only these",
    )
    args = parser.parse_args()

    load_env()
    CACHE.mkdir(parents=True, exist_ok=True)
    rules = yaml.safe_load(SCORING.read_text())
    _, _, mech_ids, cat_ids = vocabularies()

    articles = json.loads(ARTICLES.read_text())
    if args.only:
        wanted = set(json.loads(Path(args.only).read_text()))
        articles = [a for a in articles if a["url"] in wanted]
        print(f"restricted to {len(articles)} of the requested {len(wanted)} urls")
    if args.limit:
        articles = articles[: args.limit]

    client = anthropic.Anthropic()
    costs = json.loads(COST.read_text()) if COST.exists() else []
    results: dict[str, dict] = {}
    failures: list[dict] = []
    lock = threading.Lock()
    done = 0
    started = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                classify_one, client, args.model, article, mech_ids, cat_ids
            ): article
            for article in articles
        }
        for future in as_completed(futures):
            article = futures[future]
            result, cost, failure = future.result()

            # One writer at a time: the cost log is rewritten whole, and a
            # concurrent write would truncate it.
            with lock:
                done += 1
                if failure:
                    failures.append(failure)
                    print(f"  FAIL {failure['url']}: {failure['error']}", flush=True)
                else:
                    results[article["url"]] = result
                if cost:
                    costs.append(cost)
                    COST.write_text(json.dumps(costs, indent=2))
                if done % 50 == 0 or done == len(articles):
                    spent = sum(c["usd"] for c in costs)
                    rate = done / max(time.time() - started, 1e-6)
                    print(
                        f"  [{done}/{len(articles)}]  ${spent:.3f}  "
                        f"{rate:.1f}/s",
                        flush=True,
                    )

    scored = []
    for article in articles:
        result = results.get(article["url"])
        if not result:
            continue
        result["score"], result["band"] = score_of(result, rules)
        scored.append({**article, **result})
    scored.sort(key=lambda a: -a["score"])

    OUT.write_text(json.dumps({"scored": scored, "failures": failures}, indent=2))

    spent = sum(c["usd"] for c in costs)
    elapsed = time.time() - started
    print(
        f"\nscored {len(scored)}, failed {len(failures)}, "
        f"${spent:.4f}, {elapsed:.0f}s wall"
    )
    for band in ("high", "medium", "low", "none"):
        print(f"  {band:<7} {sum(1 for s in scored if s['band'] == band)}")


if __name__ == "__main__":
    main()
