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
import hashlib
import json
import os
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

# Moved to app/scoring.py so the deterministic scorer has one home; the
# functions are unchanged and everything here keeps calling them by name.
from app.scoring import ai_score_of, score_of  # noqa: E402,F401
from verbatim import enforce as enforce_quotes  # noqa: E402

PROMPT_VERSION = "v7"
PROMPT = ROOT / "prompts" / "announcement_scoring" / f"{PROMPT_VERSION}.md"
MECHANISMS = ROOT / "config" / "mechanisms.yaml"
PRACTICES = ROOT / "config" / "practices.yaml"
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

PRACTICE_TAG = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "action": {"type": "string", "enum": ["adopt", "investigate", "watch"]},
        "impact": {"type": "string", "enum": ["high", "medium", "low"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        # Required by the schema but meaningful only for model_capability, which
        # is enforced in drop_unknown_tags rather than here: a conditional
        # requirement is not expressible in the strict subset the API accepts.
        "dimensions": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["id", "action", "impact", "confidence", "dimensions", "reason", "quote"],
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
        "practices": {"type": "array", "items": PRACTICE_TAG},
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


def dimension_cap() -> int | None:
    """Maximum dimensions allowed on one practice tag.

    Returns:
        The `max_dimensions` value from `config/practices.yaml`, or None if the
        config does not set one.
    """
    return yaml.safe_load(PRACTICES.read_text()).get("max_dimensions")


def practice_dimensions() -> dict[str, set[str]]:
    """Valid dimension names per practice id.

    Returns:
        Mapping of practice id -> allowed dimension names. Practices with no
        `dimensions` block map to an empty set, meaning the field must be empty.
    """
    prac = yaml.safe_load(PRACTICES.read_text())["practices"]
    return {p["id"]: set(p.get("dimensions") or {}) for p in prac}


def vocabularies() -> tuple[str, str, str, set[str], set[str], set[str]]:
    """Render the mechanism, category and practice vocabularies for the prompt.

    Returns:
        Tuple of (mechanism block, category block, practice block, valid
        mechanism ids, valid category ids, valid practice ids). The id sets are
        used to reject hallucinated tags.
    """
    mech = yaml.safe_load(MECHANISMS.read_text())["mechanisms"]
    cats = yaml.safe_load(CATEGORIES.read_text())["categories"]
    prac = yaml.safe_load(PRACTICES.read_text())["practices"]

    mech_block = "\n".join(
        f"- `{m['id']}` — {m['label']}. {' '.join(m['description'].split())}"
        for m in mech
    )
    cat_block = "\n".join(
        f"- `{c['id']}` — {c['label']}. {' '.join(c['definition'].split())}"
        for c in cats
        if c.get("lab_signal_routable", True)
    )
    prac_lines = []
    for p in prac:
        prac_lines.append(
            f"- `{p['id']}` — {p['label']}. {' '.join(p['description'].split())}"
        )
        # The per-practice action guidance was written for the classifier but
        # was never rendered into the prompt; action agreement ran at 68% while
        # the model chose among adopt/investigate/watch without it.
        if p.get("adopt_note"):
            prac_lines.append(f"    action guide: {' '.join(p['adopt_note'].split())}")
        for name, meaning in (p.get("dimensions") or {}).items():
            prac_lines.append(f"    - dimension `{name}` — {' '.join(meaning.split())}")
    prac_block = "\n".join(prac_lines)

    return (
        mech_block,
        cat_block,
        prac_block,
        {m["id"] for m in mech},
        {c["id"] for c in cats},
        {p["id"] for p in prac},
    )


def build_prompt(article: dict) -> tuple[str, str]:
    """Assemble the system and user prompts for one announcement.

    Args:
        article: Article record from announcements.json.

    Returns:
        Tuple of (system prompt, user prompt).
    """
    mech_block, cat_block, prac_block, _, _, _ = vocabularies()
    system = (
        PROMPT.read_text(encoding="utf-8")
        .replace("{mechanisms}", mech_block)
        .replace("{categories}", cat_block)
        .replace("{practices}", prac_block)
    )
    user = (
        f"lab: {article['lab']}\n"
        f"date: {article['date']}\n"
        f"url: {article['url']}\n"
        f"text_source: {article['text_source']}\n\n"
        f"---\n{article['text']}\n---"
    )
    return system, user


def drop_unknown_tags(
    result: dict,
    mech_ids: set[str],
    cat_ids: set[str],
    prac_ids: set[str] | None = None,
    dimensions: dict[str, set[str]] | None = None,
    max_dimensions: int | None = None,
) -> list[str]:
    """Remove tags naming ids that do not exist, reporting what was dropped.

    A hallucinated id would otherwise propagate into the register as though it
    were a real transmission path.

    Practice dimensions are filtered here rather than in the JSON schema: the
    schema cannot express "required for model_capability, empty otherwise" in
    the strict subset the API accepts. A model_capability tag left with no valid
    dimension is dropped entirely -- an undifferentiated "it got better" is the
    exact failure the dimension list exists to prevent.

    Args:
        result: Parsed model output, modified in place.
        mech_ids: Valid mechanism ids.
        cat_ids: Valid category ids.
        prac_ids: Valid practice ids. Omit to skip the practice axis.
        dimensions: Valid dimension names per practice id.
        max_dimensions: Cap on dimensions per tag. The prompt asks for this and
            did not get it on the first run -- three big launches named 8, 7 and
            7 of 9 dimensions -- so it is enforced here as well. Excess
            dimensions are reported in the return value, not silently dropped.

    Returns:
        The invalid ids that were removed.
    """
    dropped = []
    for key, valid in (("mechanisms", mech_ids), ("categories", cat_ids)):
        kept = []
        for tag in result.get(key, []):
            if tag["id"] in valid:
                kept.append(tag)
            else:
                dropped.append(f"{key}:{tag['id']}")
        result[key] = kept

    if prac_ids is None:
        return dropped

    dimensions = dimensions or {}
    kept = []
    for tag in result.get("practices", []):
        if tag["id"] not in prac_ids:
            dropped.append(f"practices:{tag['id']}")
            continue
        allowed = dimensions.get(tag["id"], set())
        bad = [d for d in tag.get("dimensions", []) if d not in allowed]
        tag["dimensions"] = [d for d in tag.get("dimensions", []) if d in allowed]
        dropped += [f"practices:{tag['id']}/dimension:{d}" for d in bad]
        if allowed and not tag["dimensions"]:
            dropped.append(f"practices:{tag['id']}:no valid dimension")
            continue
        if max_dimensions and len(tag["dimensions"]) > max_dimensions:
            # The prompt asks for the three the document leads with, and the
            # model lists what it leads with first, so keeping the head of the
            # list keeps its own ranking. Recorded, not silently truncated.
            over = tag["dimensions"][max_dimensions:]
            tag["dimensions"] = tag["dimensions"][:max_dimensions]
            dropped += [f"practices:{tag['id']}/over-cap:{d}" for d in over]
        kept.append(tag)
    result["practices"] = kept
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
        # 12k: two gold articles hit the old 8000 cap under the v7 prompt.
        # An output cap only bills what is generated, so headroom is free.
        max_tokens=12000,
        # The system prompt (vocab + instructions, ~7.3k tokens) is identical
        # for every article and dominates input cost, so it is cached. The
        # summarisation experiment (docs/decisions.md 2026-09-02) found caching
        # saves more than summarise-first did, at zero quality cost. 5-minute
        # ephemeral TTL: a corpus run refreshes it on every call.
        system=[{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }],
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
    return json.loads(text), call_cost(model, article["url"], response.usage, started)


# Anthropic bills ephemeral cache writes at 1.25x the input rate and cache
# reads at 0.10x. `usage.input_tokens` excludes both, so billing them at the
# plain rate would misreport cost in both directions.
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10

# The Batches API bills every token at half the interactive rate. Applied on
# top of the cache multipliers above, not instead of them -- a batched,
# cache-read token is still 0.10x input, then halved again.
BATCH_MULT = 0.5


def call_cost(model: str, url: str, usage, started: float, mode: str = "interactive") -> dict:
    """Build a cost record from provider-reported usage, cache-aware.

    Args:
        model: Model id (a key in PRICES).
        url: Article URL, so spend is attributable per item.
        usage: Anthropic usage object; cache fields may be absent or None on
            responses that touched no cache.
        started: Wall time the call began.
        mode: "interactive" or "batch" -- recorded on every row so a run's cost
            is never averaged across the two rates without the mode being
            visible (planning.md 4a: "the cost log records which mode a run
            used, since the same workflow costs twice as much interactively as
            batched").

    Returns:
        Cost record, with cache write/read tokens broken out.
    """
    in_rate, out_rate = PRICES[model]
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    usd = (
        usage.input_tokens * in_rate
        + cache_write * in_rate * CACHE_WRITE_MULT
        + cache_read * in_rate * CACHE_READ_MULT
        + usage.output_tokens * out_rate
    ) / 1e6
    if mode == "batch":
        usd *= BATCH_MULT
    return {
        "url": url,
        "model": model,
        "mode": mode,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_write_tokens": cache_write,
        "cache_read_tokens": cache_read,
        "usd": round(usd, 6),
        "seconds": round(time.time() - started, 2),
        "at": datetime.now(timezone.utc).isoformat(),
    }


def classify_one(
    client: anthropic.Anthropic,
    model: str,
    article: dict,
    mech_ids: set[str],
    cat_ids: set[str],
    prac_ids: set[str] | None = None,
    dimensions: dict[str, set[str]] | None = None,
    max_dimensions: int | None = None,
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
        prac_ids: Valid practice ids. Omit to leave the practice axis unchecked,
            which is what the pre-v5 callers (variance, vote) want.
        dimensions: Valid dimension names per practice id.
        max_dimensions: Cap on dimensions per tag.

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

    result["dropped_tags"] = drop_unknown_tags(
        result, mech_ids, cat_ids, prac_ids, dimensions, max_dimensions
    )
    # No quote, no tag. Runs after the id check so a tag is never dropped twice.
    result["dropped_tags"] += enforce_quotes(result, article["text"])
    cached.write_text(json.dumps(result))
    return result, cost, None


def run_batch(
    client: anthropic.Anthropic,
    model: str,
    articles: list[dict],
    mech_ids: set[str],
    cat_ids: set[str],
    prac_ids: set[str] | None,
    dimensions: dict[str, set[str]] | None,
    max_dimensions: int | None,
    poll_seconds: int = 30,
) -> tuple[dict[str, dict], list[dict], list[dict]]:
    """Classify every uncached article in one Message Batches job.

    Half the interactive price (planning.md 4a: "the Batches API for
    backfills ... latency is irrelevant when seeding history"), at the cost
    of the batch's own latency -- most finish within an hour, the API's own
    ceiling is 24h. The right tradeoff for a scheduled run nobody is waiting
    on. Cached articles are skipped exactly as `classify_one` skips them, so
    a re-run only ever bills the genuinely new items, same idempotency
    guarantee as the interactive path.

    Args:
        client: Anthropic client.
        model: Model id.
        articles: Full article list; already-cached ones are skipped.
        mech_ids: Valid mechanism ids.
        cat_ids: Valid category ids.
        prac_ids: Valid practice ids.
        dimensions: Valid dimension names per practice id.
        max_dimensions: Cap on dimensions per tag.
        poll_seconds: Delay between batch status checks.

    Returns:
        Tuple of (results keyed by url, failures, cost records).
    """
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    results: dict[str, dict] = {}
    failures: list[dict] = []
    costs: list[dict] = []
    by_custom_id: dict[str, dict] = {}
    requests = []

    def bill(url: str, usage) -> None:
        # Written to disk immediately, one item at a time -- the same
        # incremental-write guarantee the interactive path's `record()`
        # closure already has. A batch can be hundreds of items; without
        # this, a crash partway through the results loop below would lose
        # every already-billed item's cost record, not just the one that
        # crashed it.
        cost = call_cost(model, url, usage, started, mode="batch")
        costs.append(cost)
        history = json.loads(COST.read_text()) if COST.exists() else []
        history.append(cost)
        COST.write_text(json.dumps(history, indent=2))

    for article in articles:
        key = re.sub(r"[^A-Za-z0-9]+", "_", article["url"])[:140] + ".json"
        cached = CACHE / key
        if cached.exists():
            results[article["url"]] = json.loads(cached.read_text())
            continue
        system, user = build_prompt(article)
        # A hash, not the truncated-slug cache key: two distinct URLs can
        # share a truncated slug, and custom_id is what results() uses to
        # route an answer back to its article -- a collision here would
        # silently misattribute a classification.
        custom_id = hashlib.sha1(article["url"].encode()).hexdigest()
        by_custom_id[custom_id] = article
        requests.append(
            Request(
                custom_id=custom_id,
                params=MessageCreateParamsNonStreaming(
                    model=model,
                    max_tokens=12000,
                    system=[{
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=[{"role": "user", "content": user}],
                    output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                ),
            )
        )

    if not requests:
        print("  batch: nothing new to score, all articles cached")
        return results, failures, costs

    print(f"  batch: submitting {len(requests)} requests", flush=True)
    started = time.time()
    batch = client.messages.batches.create(requests=requests)
    print(f"  batch: {batch.id}, status {batch.processing_status}", flush=True)

    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        print(
            f"  batch: {batch.processing_status}, "
            f"processing={batch.request_counts.processing}",
            flush=True,
        )
        time.sleep(poll_seconds)

    print(
        f"  batch: ended after {time.time() - started:.0f}s, "
        f"succeeded={batch.request_counts.succeeded} "
        f"errored={batch.request_counts.errored}",
        flush=True,
    )

    for item in client.messages.batches.results(batch.id):
        article = by_custom_id[item.custom_id]
        if item.result.type != "succeeded":
            # Batch-level failure (expired/errored/canceled) -- no `message`
            # exists at all here, so nothing was billed to record.
            failures.append({"url": article["url"], "error": f"batch {item.result.type}"})
            continue
        message = item.result.message
        if message.stop_reason == "refusal":
            # Still billed: `message.usage` reflects real tokens the API
            # charged for before it refused.
            bill(article["url"], message.usage)
            failures.append(
                {"url": article["url"], "error": f"refused: {message.stop_details}"}
            )
            continue
        if message.stop_reason == "max_tokens":
            bill(article["url"], message.usage)
            failures.append(
                {"url": article["url"], "error": "truncated output; raise max_tokens"}
            )
            continue

        try:
            text = next(b.text for b in message.content if b.type == "text")
            result = json.loads(text)
            result["dropped_tags"] = drop_unknown_tags(
                result, mech_ids, cat_ids, prac_ids, dimensions, max_dimensions
            )
            result["dropped_tags"] += enforce_quotes(result, article["text"])
        except (StopIteration, json.JSONDecodeError) as exc:
            # Also billed -- the model produced *some* output, it just
            # wasn't parseable JSON. One bad item must not abort the whole
            # batch loop and take every already-billed item's cost with it.
            bill(article["url"], message.usage)
            failures.append({"url": article["url"], "error": f"{type(exc).__name__}: {exc}"})
            continue

        key = re.sub(r"[^A-Za-z0-9]+", "_", article["url"])[:140] + ".json"
        (CACHE / key).write_text(json.dumps(result))
        results[article["url"]] = result
        bill(article["url"], message.usage)

    return results, failures, costs


def run(
    articles: list[dict],
    model: str = "claude-sonnet-5",
    workers: int = 12,
    batch: bool = False,
    budget=None,
) -> dict:
    """Classify the given articles, score them, and write the register.

    Extracted from `main()` so the scheduled pipeline and a human at a terminal
    take the same path. Duplicating this loop would mean duplicating the
    cache-warming rule and the locked cost-log write, both of which are subtle
    and both of which cost real money to get wrong.

    Args:
        articles: Article records to classify. Already-cached ones cost nothing.
        model: Model id.
        workers: Concurrent requests; the work is network-bound.
        batch: Use the Message Batches API (half price, polls to completion).
        budget: Optional object with `.exhausted`, `.spend(usd)` and
            `.snapshot()`. Checked before each item is started, so a fan-out
            already in flight stops taking new work rather than being killed
            mid-call — a cancelled call is billed and produces nothing.

    Returns:
        ``{scored, failures, cost_usd, bands, classified, skipped_for_budget}``.
        `skipped_for_budget` is non-zero only when the ceiling was reached, and
        is what distinguishes "nothing left to classify" from "stopped early".
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    rules = yaml.safe_load(SCORING.read_text())
    _, _, _, mech_ids, cat_ids, prac_ids = vocabularies()
    dims, cap = practice_dimensions(), dimension_cap()

    client = anthropic.Anthropic()

    if batch:
        # A batch is submitted as one job, so the ceiling cannot be applied
        # mid-flight the way it is interactively. It is applied to the *size of
        # the submission* instead: submit only as many articles as the remaining
        # budget can pay for at the observed per-call rate. Submitting the whole
        # set and calling `spend()` afterwards enforced nothing while still
        # recording a budget snapshot on the run -- a control that reported
        # itself as working and was not.
        if budget is not None:
            affordable = int(budget.remaining / max(budget.expected_per_call, 1e-9))
            # Batch is half price (BATCH_MULT), so the same money buys twice as
            # many items; still a floor, never an estimate used to bill.
            affordable = int(affordable / BATCH_MULT)
            if affordable < len(articles):
                print(f"budget allows {affordable} of {len(articles)} articles this run",
                      flush=True)
            submitted = articles[:max(0, affordable)]
        else:
            submitted = articles
        skipped = len(articles) - len(submitted)

        # run_batch writes each item's cost to COST as it processes results
        # (see `bill()` inside it) -- nothing left to persist here, and
        # re-reading + re-appending batch_costs on top would duplicate every
        # entry it already wrote.
        results, failures, batch_costs = run_batch(
            client, model, submitted, mech_ids, cat_ids, prac_ids, dims, cap
        ) if submitted else ({}, [], [])
        for cost in batch_costs:
            if budget is not None:
                budget.spend(cost["usd"])
        spent = sum(c["usd"] for c in batch_costs)
    else:
        results, failures, spent, skipped = _run_interactive(
            client, model, articles, workers, budget,
            mech_ids, cat_ids, prac_ids, dims, cap,
        )

    scored = []
    for article in articles:
        result = results.get(article["url"])
        if not result:
            continue
        result["score"], result["band"] = score_of(result, rules)
        scored.append({**article, **result})

    # Merge into the register rather than replace it. A full sweep writes every
    # article and merging is then a no-op, but a partial run -- the scheduled
    # pipeline classifying only what is new, or a human passing --limit -- would
    # otherwise truncate the committed register to whatever subset it happened
    # to be given. `failures` is this run's, deliberately: it describes this
    # attempt, where `scored` describes the corpus.
    merged = {}
    if OUT.exists():
        merged = {r["url"]: r for r in json.loads(OUT.read_text()).get("scored", [])}
    merged.update({r["url"]: r for r in scored})
    register = sorted(merged.values(), key=lambda a: -a["score"])

    OUT.write_text(json.dumps({"scored": register, "failures": failures}, indent=2))

    return {
        "scored": scored,
        "failures": failures,
        "cost_usd": round(spent, 6),
        "classified": len(scored),
        "skipped_for_budget": skipped,
        "bands": {b: sum(1 for s in scored if s["band"] == b)
                  for b in ("high", "medium", "low", "none")},
    }


def _run_interactive(
    client, model, articles, workers, budget,
    mech_ids, cat_ids, prac_ids, dims, cap,
):
    """The concurrent path: warm the prompt cache, then fan out.

    Returns:
        Tuple of (results by url, failures, usd spent this call, items skipped
        because the budget ran out).
    """
    results: dict[str, dict] = {}
    failures: list[dict] = []
    costs = json.loads(COST.read_text()) if COST.exists() else []
    baseline = sum(c["usd"] for c in costs)
    lock = threading.Lock()
    done = 0
    skipped = 0
    started = time.time()

    def record(article: dict, result: dict, cost: dict | None, failure: dict | None) -> None:
        # One writer at a time: the cost log is rewritten whole, and a
        # concurrent write would truncate it.
        nonlocal done
        with lock:
            done += 1
            if failure:
                failures.append(failure)
                print(f"  FAIL {failure['url']}: {failure['error']}", flush=True)
            elif result:
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

    def classify_guarded(article):
        """Skip rather than start once the ceiling is reached.

        `begin_call`/`end_call` bracket the call so the budget can count work in
        flight. Checking a plain running total instead lets a fan-out of N
        workers overshoot by up to N calls, since none of them can see what the
        others are about to spend.
        """
        nonlocal skipped
        if budget is None:
            return classify_one(
                client, model, article, mech_ids, cat_ids, prac_ids, dims, cap
            )
        if not budget.begin_call():
            with lock:
                skipped += 1
            return {}, None, None
        cost = None
        try:
            out = classify_one(
                client, model, article, mech_ids, cat_ids, prac_ids, dims, cap
            )
            cost = out[1]
            return out
        finally:
            budget.end_call(cost["usd"] if cost else 0.0)

    # Warm the prompt cache before fanning out: classify() caches the shared
    # system prompt, and a cold parallel start would make every first-wave call
    # a 1.25x cache write instead of a 0.1x read. Serial until the first call
    # that actually pays (disk-cached articles are free and warm nothing).
    warm = 0
    for article in articles:
        result, cost, failure = classify_guarded(article)
        record(article, result, cost, failure)
        warm += 1
        if cost is not None:
            break

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(classify_guarded, article): article
            for article in articles[warm:]
        }
        for future in as_completed(futures):
            article = futures[future]
            result, cost, failure = future.result()
            record(article, result, cost, failure)

    return results, failures, sum(c["usd"] for c in costs) - baseline, skipped


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
    parser.add_argument(
        "--batch",
        action="store_true",
        help="use the Message Batches API (half price, async) instead of "
             "interactive concurrent calls",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run even if SCORING_ENABLED is unset or false",
    )
    args = parser.parse_args()

    # Deactivation switch. Extraction (announcements/papers/github) runs on
    # its own; scoring is a separate, later phase and stays off until this is
    # explicitly flipped on -- see docs/decisions.md for the rationale. A
    # human running the script directly can still force a single run with
    # --force without changing the default for anything scheduled.
    if os.environ.get("SCORING_ENABLED", "").lower() not in ("1", "true", "yes") and not args.force:
        print("SCORING_ENABLED is not set -- scoring is deactivated. "
              "Set SCORING_ENABLED=true or pass --force to run anyway.")
        return

    load_env()

    articles = json.loads(ARTICLES.read_text())
    if args.only:
        wanted = set(json.loads(Path(args.only).read_text()))
        articles = [a for a in articles if a["url"] in wanted]
        print(f"restricted to {len(articles)} of the requested {len(wanted)} urls")
    if args.limit:
        articles = articles[: args.limit]

    summary = run(
        articles,
        model=args.model,
        workers=args.workers,
        batch=args.batch,
    )

    mode = " (batch)" if args.batch else ""
    print(
        f"\nscored {summary['classified']}, failed {len(summary['failures'])}, "
        f"${summary['cost_usd']:.4f}{mode}"
    )
    for band, n in summary["bands"].items():
        print(f"  {band:<7} {n}")


if __name__ == "__main__":
    main()
