"""Abstracts for the papers corpus: fetch the citation page, take its abstract.

The scored text and the citation are the same document, deliberately. A quote
must resolve on the page the reader clicks, so nothing here concatenates an
arXiv abstract onto a lab announcement -- see `config/papers_sources.yaml`,
where each lab's `url_field` already names the page that is its citation.

Why the abstract rather than the paper: measured, arXiv `/html/` full texts
average ~183,000 visible characters (~59,200 tokens), roughly 43x the mean
article in this corpus, and every claim that made these papers worth scoring
was stated in the abstract. Sending the ablations and the bibliography to find
a number in the first paragraph is the trade `llm_byline.HTML_BUDGET` already
refuses for bylines. Recorded in docs/decisions.md; the honest cost is that a
figure stated only in a results table is invisible to us.

Three extraction strategies, named per lab in config because the labs publish
differently and a page shape that changes should be a config edit:

  blockquote_abstract  arXiv `/abs/` pages -- `<blockquote class="abstract">`.
  heading_section      an "Abstract" heading, taken to the next heading.
  lead_section         no abstract element at all; the opening prose of the
                       page's main content, cut at a heading boundary.

`lead_section` is last because it is the weakest: it returns whatever the page
opens with, which is the authors' framing rather than a labelled abstract. That
difference is carried into the prompt as `text_source`, not hidden here.
"""

from __future__ import annotations

import re
from datetime import date

# Elements whose text is furniture, never content.
_DROP = re.compile(
    r"(?is)<(script|style|noscript|svg|nav|header|footer|form|template)\b.*?</\1>"
)
_TAG = re.compile(r"(?s)<[^>]+>")
_HEADING = re.compile(r"(?i)<h[1-6]\b")

_BLOCKQUOTE_ABSTRACT = re.compile(
    r'(?is)<blockquote[^>]*class="[^"]*\babstract\b[^"]*"[^>]*>(.*?)</blockquote>'
)
_ABSTRACT_HEADING = re.compile(r"(?is)<(h[1-6])\b[^>]*>\s*abstract\b[^<]*</\1>")
_MAIN = re.compile(r"(?is)<(article|main)\b[^>]*>(.*?)</\1>")

# arXiv prefixes its blockquote with a bold "Abstract:" label that is part of
# the element, not of the abstract.
_LABEL = re.compile(r"(?i)^\s*abstract[:.]?\s*")

STRATEGIES = ("blockquote_abstract", "heading_section", "lead_section")


def visible_text(html: str) -> str:
    """Strip markup and furniture, returning collapsed visible text.

    Args:
        html: Raw HTML fragment or document.

    Returns:
        Whitespace-collapsed text with script, style and navigation removed.
    """
    return re.sub(r"\s+", " ", _TAG.sub(" ", _DROP.sub(" ", html))).strip()


def _blockquote_abstract(html: str) -> str | None:
    """The arXiv `<blockquote class="abstract">` body, label removed."""
    match = _BLOCKQUOTE_ABSTRACT.search(html)
    return _LABEL.sub("", visible_text(match.group(1))) if match else None


def _heading_section(html: str) -> str | None:
    """Text between an "Abstract" heading and the next heading of any level."""
    match = _ABSTRACT_HEADING.search(html)
    if not match:
        return None
    rest = html[match.end():]
    end = _HEADING.search(rest)
    return visible_text(rest[: end.start()] if end else rest) or None


def _lead_section(html: str, max_chars: int) -> str | None:
    """The opening prose of the page's main content.

    Cut at the first heading that falls beyond a quarter of the budget, so a
    subheading in the first paragraph does not truncate the lead to nothing,
    and a long unbroken introduction is still bounded.

    Args:
        html: Raw page HTML.
        max_chars: Ceiling on returned characters.

    Returns:
        The lead text, or None if the page yields nothing usable.
    """
    match = _MAIN.search(html)
    body = match.group(2) if match else html
    body = _DROP.sub(" ", body)

    floor = max_chars // 4
    for heading in _HEADING.finditer(body):
        if len(visible_text(body[: heading.start()])) >= floor:
            body = body[: heading.start()]
            break

    text = visible_text(body)
    return text[:max_chars].rstrip() or None


def extract(html: str, strategy: str, max_chars: int = 6000) -> tuple[str, str] | None:
    """Pull an abstract out of a page, falling back down the strategy list.

    The configured strategy is tried first and the weaker ones after it, so a
    lab whose page shape changes degrades to a lead section rather than
    vanishing from the corpus. Which strategy actually produced the text is
    returned, because it is the difference between a labelled abstract and the
    opening paragraphs, and the prompt is told which it is reading.

    Args:
        html: Raw page HTML.
        strategy: Preferred strategy; must be one of :data:`STRATEGIES`.
        max_chars: Ceiling on returned characters.

    Returns:
        Tuple of (text, strategy that produced it), or None if every strategy
        failed or the result was too short to be an abstract.

    Raises:
        ValueError: If `strategy` is not a known strategy.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown abstract strategy: {strategy}")

    order = [strategy] + [s for s in STRATEGIES if s != strategy]
    for name in order:
        if name == "blockquote_abstract":
            text = _blockquote_abstract(html)
        elif name == "heading_section":
            text = _heading_section(html)
        else:
            text = _lead_section(html, max_chars)
        # A 40-character "abstract" is a navigation label or a cookie notice
        # that happened to sit under the right element, not a paper's summary.
        # Failing loudly here sends the paper to `unresolved_items`, which is
        # the recoverable outcome; passing it on would put a tag-free row in
        # the register that looks like a paper we judged unimportant.
        if text and len(text) >= 200:
            return text[:max_chars].rstrip(), name
    return None


# A labelled abstract and the opening paragraphs of a page are different
# evidence, so they are different `text_source` values and the prompt is told
# which it has. Collapsing them would let a lead section that happens to open
# with marketing copy be read with an abstract's authority.
TEXT_SOURCE = {
    "blockquote_abstract": "paper_abstract",
    "heading_section": "paper_abstract",
    "lead_section": "paper_lead",
}

# The corpus name lives in app/pipeline/registry.py (PAPERS_CORPUS): it is
# provenance for a shared table, and a second copy here would eventually
# disagree with the one the kill switch matches on.


def collect(session, labs: dict, limit: int | None = None) -> tuple[list[dict], list[dict]]:
    """Fetch each paper's citation page and pull its abstract.

    The URL fetched is `raw_papers.url` -- the lab's own page, which is already
    the citation (`url_field` in config/papers_sources.yaml). Fetching anything
    else would score text the reader cannot reach from the link we show them.

    Args:
        session: Open session, read-only here.
        labs: Parsed `labs` block of config/papers_sources.yaml, keyed by lab id.
        limit: Stop after N papers (the n=1 proving path).

    Returns:
        Tuple of (article-shaped records, unresolved). A paper whose abstract
        cannot be extracted goes to `unresolved` with a reason rather than being
        dropped -- a paper missing from the register and a paper we judged
        unimportant must not look the same.
    """
    import fetch_cache
    from sqlalchemy import select

    from app import models as m
    from app.pipeline.registry import PAPERS_CORPUS

    records, unresolved = [], []
    rows = session.scalars(select(m.RawPaper).order_by(m.RawPaper.url)).all()[:limit]

    # Some labs' papers ARE announcements. Mistral has no publications page at
    # all, so its papers leg sources candidate titles from its own announcements
    # corpus (D17) and the citation is `mistral.ai/news/<slug>` -- a URL the
    # announcements leg already holds, already fetched in full and already
    # scored under the announcement prompt. `raw_articles` is keyed on URL, so
    # landing the paper on top would overwrite that full text with a lead
    # section and score the same document twice under two prompts.
    #
    # The announcement wins: it is the complete document, and the abstract here
    # is a shorter view of it. Recorded rather than dropped, because "we
    # deliberately did not add this" and "this paper is missing" must not look
    # the same in the register.
    covered = {
        url for (url,) in session.execute(
            select(m.RawArticle.url).where(
                m.RawArticle.source_file != PAPERS_CORPUS))
    }

    for row in rows:
        if row.url in covered:
            unresolved.append({
                "url": row.url, "lab": row.lab, "kind": "paper",
                "reason": "already in the corpus as an announcement, at full "
                          "text; not re-added as an abstract",
            })
            continue
        rules = labs.get(row.lab, {}).get("abstract")
        if not rules:
            unresolved.append({"url": row.url, "lab": row.lab,
                               "reason": "no abstract config for lab"})
            continue
        # ai.meta.com rejects a browser-style UA with a 400 and accepts
        # urllib's own default -- documented on fetch_cache.fetch.
        agent = None if "ai.meta.com" in row.url else fetch_cache.UA
        try:
            html = fetch_cache.fetch(row.url, user_agent=agent)
        except Exception as exc:  # one dead page does not abort the leg (D27)
            unresolved.append({"url": row.url, "lab": row.lab,
                               "reason": f"fetch failed: {type(exc).__name__}"})
            continue

        got = extract(html, rules["strategy"], rules["max_chars"])
        if got is None:
            unresolved.append({"url": row.url, "lab": row.lab,
                               "reason": f"no abstract found ({rules['strategy']})"})
            continue

        text, how = got
        payload = row.payload or {}

        # A missing or unparseable date is a POISON PILL, not a cosmetic gap.
        # `transform` calls `date.fromisoformat(p["date"])`, which raises
        # TypeError on None -- inside `_etl`'s single transaction. The run fails,
        # the bad row stays in bronze, and every subsequent firing and every
        # `bitcap-db load` fails at the same line until someone deletes it by
        # hand. Two of the six harvesters can legitimately return no date:
        # `deepmind_harvest.detail_page_info` documents its date as "ISO or
        # None", and an LLM byline extraction can return null.
        try:
            published = date.fromisoformat(str(payload.get("date")))
        except (TypeError, ValueError):
            unresolved.append({
                "url": row.url, "lab": row.lab, "kind": "paper",
                "reason": f"no usable publication date ({payload.get('date')!r})",
            })
            continue

        records.append({
            "lab": row.lab,
            # Both, deliberately. `extraction` alone cannot show a downgrade,
            # because two strategies map to the same `text_source` -- so if arXiv
            # renamed its abstract blockquote, every DeepSeek paper would quietly
            # arrive as furniture-filled lead text with `unresolved` still zero.
            "extraction": how,
            "extraction_configured": rules["strategy"],
            "url": row.url,
            "title": payload.get("title") or row.url,
            "date": published.isoformat(),
            "text": text,
            "text_source": TEXT_SOURCE[how],
        })

    return records, unresolved
