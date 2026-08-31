"""LLM byline extraction, for evaluation against the deterministic parser.

Runs the prompt in ``prompts/byline_extraction/v1.md`` over the same cached pages
`research/byline.py` parses, so the two can be compared on identical input.

Cost is recorded at the call site: every request's token counts and price land in
``research/docs/byline_llm_cost.json`` as the run proceeds, never reconstructed
afterwards.

Usage:
    python research/llm_byline.py [--model claude-opus-5] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

ROOT = Path(__file__).parent.parent.parent
PROMPT = ROOT / "prompts" / "byline_extraction" / "v1.md"
CACHE = ROOT / "research" / "docs" / "contributor_cache"
GOLD = ROOT / "research" / "docs" / "anthropic_contributors.json"
OUT = ROOT / "research" / "docs" / "byline_llm.json"
COST = ROOT / "research" / "docs" / "byline_llm_cost.json"

# USD per million tokens (input, output), from the claude-api skill model table.
# Sonnet 5 list price is 3.00/15.00; the 2.00/10.00 introductory rate applies
# through 2026-08-31, so a run dated on or before that bills at the lower rate.
PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-5-list": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# The byline sits at the top of the document. Sending 480KB of appendix per page
# would cost ~100x for content that cannot contain the answer, so the page is cut
# after this many characters -- and the cut is recorded per page, so a miss caused
# by truncation is never mistaken for a model error.
HTML_BUDGET = 60_000

SCHEMA = {
    "type": "object",
    "properties": {
        "authors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "affiliations": {"type": "array", "items": {"type": "string"}},
                    "marks": {"type": "array", "items": {"type": "string"}},
                    "is_fellow": {"type": "boolean"},
                    "is_anthropic": {"type": "boolean"},
                },
                "required": ["name", "affiliations", "marks", "is_fellow", "is_anthropic"],
                "additionalProperties": False,
            },
        },
        "date": {"type": ["string", "null"]},
        "star_means": {"type": ["string", "null"]},
        "order_meaningful": {"type": "boolean"},
        "no_byline": {"type": "boolean"},
    },
    "required": ["authors", "date", "star_means", "order_meaningful", "no_byline"],
    "additionalProperties": False,
}


def load_env() -> None:
    """Load .env into the process environment without overriding what is set."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def extract_page(raw_html: str, model: str = "claude-sonnet-5") -> tuple[dict, dict]:
    """Extract a byline from one raw page, for use as a parser fallback.

    Args:
        raw_html: Raw HTML of the article.
        model: Model id.

    Returns:
        Tuple of (parsed byline, cost record).
    """
    load_env()
    html, _ = prepare_html(raw_html)
    return extract(anthropic.Anthropic(), model, html)


def cache_path(url: str) -> Path:
    """Map an article URL to its cached HTML file.

    Args:
        url: Article URL as recorded in the gold file.

    Returns:
        Path to the cached page.
    """
    key = re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_")[:150] + ".html"
    return CACHE / key


def prepare_html(raw: str) -> tuple[str, bool]:
    """Strip non-content markup and cut the page to the prompt budget.

    Args:
        raw: Raw page HTML.

    Returns:
        Tuple of (prepared HTML, whether the page was truncated).
    """
    body = re.sub(r"(?is)<(script|style|svg|noscript).*?</\1>", " ", raw)
    body = re.sub(r"(?is)<!--.*?-->", " ", body)
    body = re.sub(r"[ \t]+", " ", body)
    return body[:HTML_BUDGET], len(body) > HTML_BUDGET


def build_prompt(html: str) -> tuple[str, str]:
    """Split the versioned prompt file into its system and user halves.

    Args:
        html: Prepared article HTML to interpolate.

    Returns:
        Tuple of (system prompt, user prompt).
    """
    text = PROMPT.read_text(encoding="utf-8")
    system = text.split("## System", 1)[1].split("## User", 1)[0].strip()
    user = text.split("## User", 1)[1].strip()
    return system, user.replace("{html}", html)


def extract(client: anthropic.Anthropic, model: str, html: str) -> tuple[dict, dict]:
    """Extract one byline and return the result alongside its cost record.

    Args:
        client: Anthropic client.
        model: Model id.
        html: Prepared article HTML.

    Returns:
        Tuple of (parsed byline, cost record for this single call).
    """
    system, user = build_prompt(html)
    started = time.time()
    with client.messages.stream(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError(f"refused: {response.stop_details}")

    text = next(b.text for b in response.content if b.type == "text")
    usage = response.usage
    in_rate, out_rate = PRICES[model]
    cost = {
        "model": model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "usd": round(
            usage.input_tokens / 1e6 * in_rate + usage.output_tokens / 1e6 * out_rate, 6
        ),
        "seconds": round(time.time() - started, 2),
        "stop_reason": response.stop_reason,
    }
    return json.loads(text), cost


def main() -> None:
    """Run extraction over every gold article and write results plus the cost log."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    load_env()

    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    articles = gold["articles"][: args.limit or None]
    client = anthropic.Anthropic()

    results, costs = [], []
    for i, art in enumerate(articles, 1):
        path = cache_path(art["url"])
        if not path.exists():
            print(f"  ! not cached, skipping: {art['url']}")
            continue
        html, truncated = prepare_html(path.read_text(encoding="utf-8"))
        try:
            parsed, cost = extract(client, args.model, html)
        except Exception as exc:  # recorded, never silently dropped
            print(f"  ! {type(exc).__name__} on {art['url']}: {exc}")
            results.append({"url": art["url"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        parsed.update(url=art["url"], title=art["title"], truncated=truncated)
        results.append(parsed)
        cost["url"] = art["url"]
        costs.append(cost)
        # Written every iteration: a crash must not lose the spend already incurred.
        COST.write_text(json.dumps(costs, indent=2), encoding="utf-8")
        print(
            f"  [{i}/{len(articles)}] {len(parsed['authors']):2d} authors  "
            f"${cost['usd']:.4f}  {art['title'][:52]}"
        )

    OUT.write_text(
        json.dumps(
            {
                "generated": datetime.now(timezone.utc).isoformat(),
                "model": args.model,
                "prompt": "prompts/byline_extraction/v1.md",
                "html_budget_chars": HTML_BUDGET,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    total = sum(c["usd"] for c in costs)
    tin = sum(c["input_tokens"] for c in costs)
    tout = sum(c["output_tokens"] for c in costs)
    print(f"\n{len(costs)} calls  {tin:,} in / {tout:,} out tokens  ${total:.4f} -> {COST}")


if __name__ == "__main__":
    main()
