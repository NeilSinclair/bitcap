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
PROMPT_V2 = ROOT / "prompts" / "byline_extraction" / "v2.md"
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
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}

# The byline sits at the top of the document. Sending 480KB of appendix per page
# would cost ~100x for content that cannot contain the answer, so the page is cut
# after this many characters -- and the cut is recorded per page, so a miss caused
# by truncation is never mistaken for a model error.
HTML_BUDGET = 60_000

# Section headings that carry an author/contributor list positioned well
# into the document rather than at the top -- confirmed live on two real
# Mistral AI papers (docs/decisions.md D17): a "Contributors" heading sat
# 70KB-135KB past <article>, itself well before the document's end (a large
# references/bibliography section follows it), so neither a head-only nor a
# head+tail budget reliably reaches it. A blind head+tail split was tried
# first and still missed both real cases, because the tail it grabbed was
# bibliography, not the contributor list. `parse_author_list` in
# deepseek_harvest.py already special-cases the same "Author List" heading
# shape with an unbounded regex search, for the same underlying reason.
AUTHOR_SECTION = re.compile(r"(?:Contributors|Author List)\s*</h[1-6]>", re.I)

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

# v2: generalizes SCHEMA away from Anthropic. `is_anthropic` -> `is_lab_staff`, and the
# prompt's default-affiliation rule interpolates `{lab_label}` from config instead of a
# hardcoded `["Anthropic"]` -- see prompts/byline_extraction/v2.md for the full diff and
# why (DeepMind, Meta and Mistral all lack reliable affiliation markup, so this path is
# their primary extractor, not a rare fallback, and it has to work for any lab).
SCHEMA_V2 = {
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
                    "is_lab_staff": {"type": "boolean"},
                },
                "required": ["name", "affiliations", "marks", "is_fellow", "is_lab_staff"],
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


class ExtractionError(RuntimeError):
    """A byline call that was billed but whose output could not be parsed.

    Carries `cost` so a caller can still record the genuine API spend on a
    failed extraction, rather than losing it -- see `extract()`.
    """

    def __init__(self, message: str, cost: dict):
        super().__init__(message)
        self.cost = cost


def load_env() -> None:
    """Load .env into the process environment without overriding what is set."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def extract_page(
    raw_html: str, model: str = "claude-sonnet-5", lab_label: str | None = None
) -> tuple[dict, dict]:
    """Extract a byline from one raw page.

    Args:
        raw_html: Raw HTML of the article.
        model: Model id.
        lab_label: When set, uses the generalized v2 prompt and schema
            (``is_lab_staff``, affiliation defaults to this label) instead of
            the Anthropic-specific v1 prompt. Anthropic's own harvest keeps
            calling this with `lab_label` unset, so v1 stays exactly what it
            was -- v2 is additive, not a replacement.

    Returns:
        Tuple of (parsed byline, cost record).
    """
    load_env()
    html, _ = prepare_html(raw_html)
    return extract(anthropic.Anthropic(), model, html, lab_label=lab_label)


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
    # arXiv's HTML template ships a large amount of site chrome (search
    # modal, "Report Issue" widget, license banner) before the article's own
    # content. Confirmed live on a real Meta AI paper (research/meta_harvest
    # via research/docs/decisions.md D16): 66KB of chrome preceded
    # `<article`, past the entire HTML_BUDGET, so the byline -- which
    # LaTeXML places right at the top of `<article>` -- was never sent to the
    # model at all, and came back as a false `no_byline`. Starting the
    # window at `<article>` when present spends the budget on the document,
    # not the wrapper around it. Checked live against every DeepMind arXiv
    # page already cached: none had `<article>` past this offset, so this is
    # additive, not a correction to prior DeepMind results. Pages with no
    # `<article>` tag (lab blog pages, not arXiv) are unaffected.
    article_start = body.find("<article")
    if article_start > 0:
        body = body[article_start:]
    if len(body) <= HTML_BUDGET:
        return body, False
    head_budget = HTML_BUDGET // 2
    tail_budget = HTML_BUDGET - head_budget
    # Confirmed live on two real Mistral AI papers (docs/decisions.md D17):
    # arXiv's LaTeXML rendering can put a large flat "Contributors" list,
    # not a `<div class="ltx_authors">` byline, well into the document --
    # 70KB-135KB past `<article>`. A blind head+tail split was tried first
    # and still missed both real cases: the tail it grabbed was a
    # references/bibliography section following the contributor list, not
    # the list itself, which sits closer to the middle of the document than
    # either end. Locating the heading first and windowing around it (same
    # heading shape `deepseek_harvest.py::parse_author_list` already
    # special-cases, there via an unbounded regex search rather than an LLM
    # budget) finds it directly instead of guessing where it might be.
    section = AUTHOR_SECTION.search(body)
    if section:
        window_end = section.start() + tail_budget
        # The section itself is usually short (a few thousand characters);
        # a flat tail_budget window run against a real Shieldstral paper
        # (docs/decisions.md D17) ran straight past it into the references
        # section that followed, which has its own dense comma-separated
        # author names per citation -- the model tried to enumerate all of
        # it as the byline and produced truncated, invalid JSON. Stopping at
        # the next heading or section close, when one appears inside the
        # window, keeps the extraction scoped to the section actually named.
        boundary = re.search(r"<h[1-3][ >]|</section>", body[section.end() : window_end])
        if boundary:
            window_end = section.end() + boundary.start()
        return body[:head_budget] + body[section.start() : window_end], True
    # No named section found -- fall back to a blind tail. Harmless for the
    # common case (DeepMind's and Meta's papers, confirmed unaffected since
    # their byline sits within the first few hundred characters of
    # `<article>`, well inside head_budget already) and better than nothing
    # for an unknown future document shape.
    return body[:head_budget] + body[-tail_budget:], True


def build_prompt(html: str, lab_label: str | None = None) -> tuple[str, str]:
    """Split the versioned prompt file into its system and user halves.

    Args:
        html: Prepared article HTML to interpolate.
        lab_label: When set, loads v2 and interpolates it into the user
            prompt's `{lab_label}` placeholders; when unset, loads v1
            unchanged.

    Returns:
        Tuple of (system prompt, user prompt).
    """
    path = PROMPT_V2 if lab_label else PROMPT
    text = path.read_text(encoding="utf-8")
    system = text.split("## System", 1)[1].split("## User", 1)[0].strip()
    user = text.split("## User", 1)[1].strip().replace("{html}", html)
    if lab_label:
        user = user.replace("{lab_label}", lab_label)
    return system, user


def extract(
    client: anthropic.Anthropic, model: str, html: str, lab_label: str | None = None
) -> tuple[dict, dict]:
    """Extract one byline and return the result alongside its cost record.

    Args:
        client: Anthropic client.
        model: Model id.
        html: Prepared article HTML.
        lab_label: When set, uses the generalized v2 prompt and schema; see
            `extract_page`.

    Returns:
        Tuple of (parsed byline, cost record for this single call).
    """
    system, user = build_prompt(html, lab_label)
    schema = SCHEMA_V2 if lab_label else SCHEMA
    started = time.time()
    with client.messages.stream(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError(f"refused: {response.stop_details}")

    text = next(b.text for b in response.content if b.type == "text")
    usage = response.usage
    in_rate, out_rate = PRICES[model]
    cost = {
        "model": model,
        "prompt_version": "v2" if lab_label else "v1",
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
    try:
        return json.loads(text), cost
    except json.JSONDecodeError as exc:
        # The call was genuinely billed by the API before the parse failed --
        # confirmed live (docs/decisions.md D17) that a plain re-raise here
        # silently lost that cost record, since `cost` never reached the
        # caller when json.loads() raised inside the return expression.
        # Carrying it on the exception lets a caller record real spend even
        # on a failed extraction, instead of a gap in "cost instrumented at
        # the call site."
        raise ExtractionError(f"malformed JSON from model: {exc}", cost) from exc


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
