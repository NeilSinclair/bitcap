"""Contributor register for Meta AI, from its own publications search endpoint.

Meta's publishing shape needed a proving run (docs/decisions.md D16) before
building this, because two assumptions from the initial discovery pass turned
out wrong: the main `/research/publications/` listing page 500s on a plain
fetch, and once the paginated search endpoint was found instead
(`ai.meta.com/results/?content_types[0]=publication&years[0]=<YEAR>`), its
detail pages turned out to carry **no resolvable arXiv link at all** -- the
"Read the Paper" button is JS-populated, confirmed live on a real page's raw
HTML -- and **no publication date either**. Both citation and date have to be
resolved externally, via arXiv, for every paper.

Two consequences that do not apply to DeepMind (`deepmind_harvest.py`), whose
detail pages link out to arXiv/OpenReview directly:

  * **Title-based arXiv resolution, not link-scraping.** Exact title match
    (after stripping Meta's own " | Research - AI at Meta" site suffix)
    resolves most papers, but not all -- Meta sometimes prepends a system
    name to the title that arXiv's own title drops (confirmed live:
    "AutoformBot: Formalizing Mathematics at Scale" on Meta's site is
    "Formalizing Mathematics at Scale" on arXiv). Resolution itself lives in
    `arxiv_resolve.py`, shared with `mistral_harvest.py` (docs/decisions.md
    D16, D17) since both labs need it. A paper that resolves neither exactly
    nor with enough disambiguation confidence is not guessed at -- it is
    recorded in `meta_unresolved.json`, not silently dropped and not
    force-fitted to a citation that might be wrong (CLAUDE.md: "no citation,
    no insight").
  * **Publication date comes from the arXiv entry, not the Meta page.**
    Confirmed live: no date, in any format, appears anywhere in a Meta
    detail page's raw HTML. `years[0]=<YEAR>` on the search endpoint is only
    a coarse, year-level prefilter as a result -- month-window filtering
    happens after arXiv resolution, using arXiv's own `published` date. A
    paper that never resolves therefore has no reliable date and cannot be
    placed in or out of the window; it is excluded from `papers` and
    reported separately rather than assumed in-window.

Usage:
    python research/papers/meta_harvest.py [--months 3] [--limit N]
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import fetch_cache
from arxiv_resolve import resolve_title

ROOT = Path(__file__).parent.parent.parent

CACHE = ROOT / "research" / "docs" / "meta_cache"
OUT = ROOT / "research" / "docs" / "meta_contributors.json"
COST = ROOT / "research" / "docs" / "meta_llm_cost.json"
UNRESOLVED = ROOT / "research" / "docs" / "meta_unresolved.json"
# Extraction results keyed by Meta publication URL, so a re-run never re-bills
# a paper already extracted -- same idempotency guarantee as fetch()'s disk
# cache, one layer up.
EXTRACTION_CACHE = ROOT / "research" / "docs" / "meta_extraction_cache.json"

SEARCH_URL = "https://ai.meta.com/results/"
LAB_LABEL = "Meta AI"
UA = "bitcap-case-study research spike (contact: neilaf4@gmail.com)"

TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
META_DESC = re.compile(
    r'<meta\s+(?:property|name)="(?:og:description|description)"\s+content="([^"]*)"', re.I
)
DETAIL_LINK = re.compile(r"/research/publications/([a-z0-9-]+)/", re.I)
# Meta appends this to every detail page's <title>; stripped so the stored
# title is the paper's own, matching how deepmind_harvest.py strips
# " -- Google DeepMind".
SITE_SUFFIX = re.compile(r"\s*\|\s*Research - AI at Meta\s*$", re.I)


@dataclass
class Paper:
    """One Meta AI publication and its byline.

    Attributes:
        meta_url: Meta's own publication page -- always resolvable, the
            primary citation regardless of arXiv-resolution confidence.
        arxiv_id: Resolved arXiv id.
        source_url: The arXiv HTML page the byline was extracted from.
        title: Paper title, read from Meta's own page.
        date: ISO publication date, read from the resolved arXiv entry --
            Meta's own detail pages carry no date at all (see module
            docstring).
        resolution: How the arXiv id was found -- "exact_title" or
            "relaxed_title". Surfaced so a consumer can weight confidence;
            not hidden inside a uniform citation.
        authors: Author records from the LLM extractor.
        order_meaningful: Whether author order carries seniority information.
        star_means: What the page's own footnote says '*' denotes, if any.
        truncated: Whether the source page had to be cut to fit
            HTML_BUDGET. A byline extracted from a truncated page cannot be
            distinguished from a complete one without this -- see
            llm_byline.py::prepare_html.
    """

    meta_url: str
    arxiv_id: str
    source_url: str
    title: str
    date: str
    resolution: str
    authors: list[dict] = field(default_factory=list)
    order_meaningful: bool = True
    star_means: str | None = None
    truncated: bool = False


def fetch(url: str, retries: int | None = None, user_agent: str | None = UA) -> str:
    """Fetch a URL through the shared cache, throttle and retry policy.

    This harvester fetches `arxiv.org/html/` as well as `ai.meta.com`, so its
    private 1.5s pause was undercutting arXiv's 3s guidance whenever it went
    for a paper. The throttle is now shared and keyed on host
    (`fetch_cache.py`, docs/decisions.md D53).

    Args:
        url: Absolute URL.
        retries: Attempts before giving up. None takes the configured value.
        user_agent: UA string to send. `None` sends no override -- confirmed
            live (and already relied on by `fetch_announcements.py`) that
            `ai.meta.com` blocks any browser-style UA with a 400 and accepts
            urllib's own bare default.

    Returns:
        Decoded response body.

    Raises:
        RuntimeError: If every attempt fails. The caller decides whether one
            dead source aborts the whole run (it does not, here).
    """
    return fetch_cache.fetch(
        url, cache_dir=CACHE, suffix=".html", user_agent=user_agent, retries=retries
    )


def list_paper_candidates(years: list[int], max_pages: int = 20) -> list[tuple[str, str]]:
    """Enumerate (slug, detail_url) pairs via Meta's own paginated search endpoint.

    `years[0]=<YEAR>` is the only filter Meta's endpoint offers; there is no
    month-level filter, so this is a coarse prefilter, narrowed to the real
    window later using each paper's resolved arXiv date. Confirmed live that
    even within one year, page 2 returns entirely new slugs, not a repeat --
    so pagination is not optional just because a year filter is applied.

    Stops a year's pagination on an empty page, or defensively on a page
    whose slugs are all already seen (the endpoint's ordering is not
    documented, so a wraparound repeat is treated as the same signal as
    an empty page rather than looping to `max_pages` on a false read).

    Args:
        years: Calendar years to search.
        max_pages: Safety cap per year.

    Returns:
        List of (slug, detail_url) pairs, deduplicated across years.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for year in years:
        for page in range(1, max_pages + 1):
            url = (
                f"{SEARCH_URL}?content_types%5B0%5D=publication"
                f"&years%5B0%5D={year}&page={page}"
            )
            body = fetch(url, user_agent=None)
            slugs = sorted(set(DETAIL_LINK.findall(body)))
            new = [s for s in slugs if s not in seen]
            if not slugs or not new:
                break
            for s in new:
                seen.add(s)
                out.append((s, f"https://ai.meta.com/research/publications/{s}/"))
    return out


def detail_page_info(page_html: str) -> dict:
    """Pull title and meta-description off one Meta detail page.

    Args:
        page_html: Raw HTML of an ai.meta.com publication detail page.

    Returns:
        Dict with `title` (site suffix stripped) and `desc` (used only as a
        disambiguation signal for relaxed arXiv matching, not stored).
    """
    title_match = TITLE_TAG.search(page_html)
    title = html_mod.unescape(title_match.group(1)).strip() if title_match else ""
    title = SITE_SUFFIX.sub("", re.sub(r"\s+", " ", title)).strip()[:200]

    desc_match = META_DESC.search(page_html)
    desc = html_mod.unescape(desc_match.group(1)).strip() if desc_match else ""
    return {"title": title, "desc": desc}


def collect(months: int, model: str, limit: int | None = None) -> tuple[list[Paper], list[dict]]:
    """Collect in-window Meta AI papers with LLM-extracted bylines.

    Args:
        months: Window length in months.
        model: Model id for byline extraction.
        limit: Cap on candidates processed, for a small proving run.

    Returns:
        Tuple of (papers with a parsed byline, unresolved candidates -- each
        with a `reason`, never silently dropped).
    """
    from llm_byline import extract_page, load_env

    load_env()
    cutoff_date = date.today() - timedelta(days=months * 30.5)
    years = list(range(cutoff_date.year, date.today().year + 1))
    candidates = list_paper_candidates(years)
    if limit:
        candidates = candidates[:limit]

    extraction_cache = (
        json.loads(EXTRACTION_CACHE.read_text()) if EXTRACTION_CACHE.exists() else {}
    )

    papers: list[Paper] = []
    unresolved: list[dict] = []
    for slug, meta_url in candidates:
        try:
            detail_html = fetch(meta_url, user_agent=None)
        except RuntimeError as exc:
            print(f"  ! fetch failed, skipping ({type(exc).__name__}): {meta_url}: {exc}")
            unresolved.append({"slug": slug, "meta_url": meta_url, "reason": "fetch_failed"})
            continue

        info = detail_page_info(detail_html)
        match = resolve_title(info["title"], info["desc"])
        if not match:
            unresolved.append(
                {"slug": slug, "meta_url": meta_url, "title": info["title"], "reason": "no_arxiv_match"}
            )
            print(f"  ! no arXiv match, skipping: {info['title'][:60]}")
            continue
        if match["date"] < cutoff_date.isoformat():
            continue  # resolved, but genuinely outside the window

        source_url = f"https://arxiv.org/html/{match['arxiv_id']}"
        try:
            target_html = fetch(source_url)
        except RuntimeError as exc:
            print(f"  ! arXiv HTML unavailable, skipping ({type(exc).__name__}): {meta_url}")
            unresolved.append(
                {"slug": slug, "meta_url": meta_url, "title": info["title"], "reason": "arxiv_html_unavailable"}
            )
            continue

        cost = None
        if meta_url in extraction_cache:
            parsed = extraction_cache[meta_url]
        else:
            try:
                parsed, cost, truncated = extract_page(target_html, model, lab_label=LAB_LABEL)
            except Exception as exc:  # refusal, malformed JSON, network error
                print(f"  ! extraction failed, skipping ({type(exc).__name__}): {meta_url}")
                failed_cost = getattr(exc, "cost", None)
                if failed_cost:  # billed by the API even though extraction failed
                    failed_cost["url"] = meta_url
                    history = json.loads(COST.read_text()) if COST.exists() else []
                    history.append(failed_cost)
                    COST.write_text(json.dumps(history, indent=2))
                unresolved.append(
                    {"slug": slug, "meta_url": meta_url, "title": info["title"], "reason": "extraction_failed"}
                )
                continue

            cost["url"] = meta_url
            history = json.loads(COST.read_text()) if COST.exists() else []
            history.append(cost)
            COST.write_text(json.dumps(history, indent=2))

            parsed["truncated"] = truncated  # round-tripped through the cache below
            extraction_cache[meta_url] = parsed
            EXTRACTION_CACHE.write_text(json.dumps(extraction_cache, indent=2))

        paper = Paper(
            meta_url=meta_url,
            arxiv_id=match["arxiv_id"],
            source_url=source_url,
            title=info["title"],
            date=match["date"],
            resolution=match["resolution"],
            truncated=parsed.get("truncated", False),
            authors=parsed["authors"],
            order_meaningful=parsed.get("order_meaningful", True),
            star_means=parsed.get("star_means"),
        )
        papers.append(paper)
        usd = f"${cost['usd']:.4f}" if cost else "cached "
        print(
            f"  {paper.date}  {len(paper.authors):3d} authors  {usd}  "
            f"[{paper.resolution:14}]  {paper.title[:50]}",
            flush=True,
        )

    if unresolved:
        UNRESOLVED.write_text(json.dumps(unresolved, indent=2))
    return papers, unresolved


def aggregate(papers: list[Paper]) -> list[dict]:
    """Build the per-person frequency table.

    Args:
        papers: Papers with parsed bylines.

    Returns:
        Person records, most papers first.
    """
    people: dict[str, dict] = defaultdict(
        lambda: {
            "appearances": 0,
            "is_lab_staff": False,
            "is_fellow": False,
            "affiliations": set(),
            "papers": [],
        }
    )
    for paper in papers:
        for a in paper.authors:
            p = people[a["name"]]
            p["appearances"] += 1
            p["is_lab_staff"] = p["is_lab_staff"] or bool(a.get("is_lab_staff"))
            p["is_fellow"] = p["is_fellow"] or bool(a.get("is_fellow"))
            p["affiliations"].update(a.get("affiliations", []))
            p["papers"].append({"title": paper.title, "url": paper.meta_url, "date": paper.date})

    rows = []
    for name, p in people.items():
        rows.append(dict(p, name=name, affiliations=sorted(p["affiliations"])))
    rows.sort(key=lambda r: -r["appearances"])
    return rows


def main() -> None:
    """Run the harvest and write the register."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--limit", type=int, help="cap candidates processed (proving runs)")
    args = ap.parse_args()

    papers, unresolved = collect(args.months, args.model, args.limit)
    rows = aggregate(papers)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generated": date.today().isoformat(),
                "lab": LAB_LABEL,
                "papers": [p.__dict__ for p in papers],
                "people": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    costs = json.loads(COST.read_text()) if COST.exists() else []
    spent = sum(c["usd"] for c in costs)
    print(
        f"\n{len(papers)} papers, {len(rows)} distinct authors, "
        f"{len(unresolved)} unresolved, ${spent:.4f} -> {OUT}"
    )


if __name__ == "__main__":
    main()
