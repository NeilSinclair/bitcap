"""Contributor register for Google DeepMind, from its own publications sitemap.

DeepMind's publishing shape has no deterministic-parser path at all, unlike
Anthropic (consistent HTML byline markup) or DeepSeek (a lab-printed "Author
List" appendix). Confirmed live: arXiv HTML for DeepMind papers says
"Google", not "Google DeepMind", on some papers, and carries no affiliation
markup at all on others. So every paper here goes through the LLM
(`prompts/byline_extraction/v2.md`, via `llm_byline.extract_page`) as the
primary extractor, not a rare fallback the way it was for Anthropic.

Corpus enumeration reuses deepmind.google/sitemap.xml, already fetched for
the announcements leg: it lists every individual
`/research/publications/<id>/` page with a `<lastmod>`, which sidesteps the
publications *listing* page being JS-rendered (confirmed unreadable by a
plain fetch -- see the discovery notes in docs/decisions.md).

Usage:
    python research/papers/deepmind_harvest.py [--months 3] [--limit N]
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "research" / "announcements"))
from fetch_announcements import date_from_page, strip_html  # noqa: E402

CACHE = ROOT / "research" / "docs" / "deepmind_cache"
OUT = ROOT / "research" / "docs" / "deepmind_contributors.json"
COST = ROOT / "research" / "docs" / "deepmind_llm_cost.json"
# Extraction results keyed by DeepMind publication URL, so a re-run (e.g.
# raising --limit or re-running after a code fix) never re-bills a paper
# already extracted -- same idempotency guarantee as fetch()'s disk cache,
# one layer up.
EXTRACTION_CACHE = ROOT / "research" / "docs" / "deepmind_extraction_cache.json"
SITEMAP = "https://deepmind.google/sitemap.xml"
LAB_LABEL = "Google DeepMind"
UA = "bitcap-case-study research spike (contact: neilaf4@gmail.com)"

# DeepMind's rendered pages omit attribute quotes wherever the value has no
# whitespace ("href=https://... target=_blank", not href="..."). That is
# valid HTML5, but a naive href="..." regex silently matches nothing on
# every single publication page here -- caught by hand-checking the first
# proving run, which returned zero authors on all three papers tried.
ARXIV_LINK = re.compile(
    r'href=["\']?(https?://arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?)["\'\s>]'
)
OPENREVIEW_LINK = re.compile(
    r'href=["\']?(https?://openreview\.net/(?:pdf|forum)\?id=[\w-]+)["\'\s>]'
)
# The page's own JSON-LD carries the same arXiv id in a `sameAs` field with
# normal JSON quoting, which is more robust than href-scraping and is tried
# first.
JSONLD_ARXIV = re.compile(r'"sameAs":\s*"(https?://arxiv\.org/abs/(\d{4}\.\d{4,5}))(?:v\d+)?"')
# `[^>]*` after `title` is load-bearing, not decorative -- the same exact
# pattern without it silently found nothing on every Meta AI article, whose
# <title> tags carry an id attribute. DeepMind's own pages happen to have no
# attributes on <title>, so this was untested here, not confirmed safe.
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


@dataclass
class Paper:
    """One DeepMind publication and its byline.

    Attributes:
        url: DeepMind's own publication page -- always resolvable, always
            the primary citation regardless of where the byline came from.
        source_url: The page the byline was actually extracted from -- the
            paper's arXiv HTML page when one exists, else an OpenReview
            link, else `url` itself. Not every publication links out.
        title: Paper title, read from DeepMind's own page.
        date: ISO publication date, read from the detail page's own text.
        authors: Author records from the LLM extractor.
        order_meaningful: Whether author order carries seniority information.
        star_means: What the page's own footnote says '*' denotes, if any.
        truncated: Whether the source page had to be cut to fit
            HTML_BUDGET. A byline extracted from a truncated page cannot be
            distinguished from a complete one without this -- see
            llm_byline.py::prepare_html.
    """

    url: str
    source_url: str
    title: str
    date: str | None
    authors: list[dict] = field(default_factory=list)
    order_meaningful: bool = True
    star_means: str | None = None
    truncated: bool = False


def fetch(url: str, pause: float = 1.5, retries: int = 3) -> str:
    """Fetch a URL, caching to disk so re-runs are free and idempotent.

    Retries with exponential backoff on transport failure, same shape as
    `fetch_announcements.py::fetch` -- a live run against arXiv's `/html/`
    endpoint hit a 403 mid-batch (a rate-limit response, not a real 404;
    DeepSeek's own arXiv harvester paces requests at 3s apiece for the same
    reason), which previously crashed the whole run instead of being one
    paper's problem.

    Args:
        url: Absolute URL.
        pause: Seconds to wait after a live fetch.
        retries: Attempts before giving up.

    Returns:
        Decoded response body.

    Raises:
        RuntimeError: If every attempt fails. The caller decides whether one
            dead source aborts the whole run (it does not, here).
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_")[:150] + ".html"
    path = CACHE / key
    if path.exists():
        return path.read_text(encoding="utf-8")

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            body = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
            path.write_text(body, encoding="utf-8")
            time.sleep(pause)
            return body
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"fetch failed: {url}: {exc}") from exc
            time.sleep(2**attempt)
    raise RuntimeError(f"fetch failed: {url}")


def list_publication_urls(months: int) -> list[tuple[str, str]]:
    """Enumerate in-window publication pages from DeepMind's own sitemap.

    The publications *listing* page is JS-rendered and unreadable by a plain
    fetch; the sitemap lists every individual publication URL with a
    `<lastmod>` instead, sidestepping that entirely. `<lastmod>` is a
    modification date, not a publication date -- used here only as a coarse
    prefilter, the same caveat the announcements leg already carries for
    this exact sitemap.

    Args:
        months: Window length in months, ending today.

    Returns:
        List of (url, lastmod) tuples, most recently modified first.
    """
    xml = fetch(SITEMAP)
    cutoff = (datetime.now() - timedelta(days=months * 30.5)).strftime("%Y-%m-%d")
    out = []
    for entry in re.findall(r"<url>(.*?)</url>", xml, re.S):
        m = re.search(
            r"<loc>(https://deepmind\.google/research/publications/\d+/)</loc>"
            r"<lastmod>([\d-]+)</lastmod>",
            entry,
        )
        if m and m.group(2) >= cutoff:
            out.append((m.group(1), m.group(2)))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def detail_page_info(page_html: str) -> dict:
    """Pull title, date and the best external source link off one detail page.

    Args:
        page_html: Raw HTML of a deepmind.google publication detail page.

    Returns:
        Dict with `title`, `date` (ISO or None), and `source_url` (an arXiv
        HTML page when an arXiv id is found, else an OpenReview link, else
        None -- confirmed live that not every publication links out).
    """
    title_match = TITLE_TAG.search(page_html)
    title = html_mod.unescape(title_match.group(1)).strip() if title_match else ""
    # DeepMind appends " — Google DeepMind" to every <title>; strip it so the
    # stored title is the paper's own, not the site's.
    title = re.sub(r"\s*[—|]\s*Google DeepMind\s*$", "", title)[:200]

    iso_date = date_from_page(strip_html(page_html))

    # JSON-LD's `sameAs` is tried first: normal JSON quoting, more robust
    # than href-scraping. Falls back to the rendered links, which may be
    # unquoted attributes (see ARXIV_LINK's comment).
    jsonld = JSONLD_ARXIV.search(page_html)
    if jsonld:
        return {
            "title": title,
            "date": iso_date,
            "source_url": f"https://arxiv.org/html/{jsonld.group(2)}",
        }
    arxiv = ARXIV_LINK.search(page_html)
    if arxiv:
        return {
            "title": title,
            "date": iso_date,
            "source_url": f"https://arxiv.org/html/{arxiv.group(2)}",
        }
    openreview = OPENREVIEW_LINK.search(page_html)
    if openreview:
        return {"title": title, "date": iso_date, "source_url": openreview.group(1)}
    return {"title": title, "date": iso_date, "source_url": None}


def collect(months: int, model: str, limit: int | None = None) -> list[Paper]:
    """Collect in-window DeepMind papers with LLM-extracted bylines.

    Every paper goes through the LLM -- see the module docstring for why no
    deterministic path exists here. A single extraction failure (refusal,
    malformed JSON, network error) is recorded and skipped rather than
    aborting the run, matching the project's "expected agent failure modes
    handled deliberately" requirement.

    Args:
        months: Window length in months.
        model: Model id for byline extraction.
        limit: Cap on papers processed, for a small proving run.

    Returns:
        Papers with a parsed byline, most recently modified first. Papers
        whose extraction failed are omitted, not silently zero-authored.
    """
    from llm_byline import extract_page, load_env

    load_env()
    urls = list_publication_urls(months)
    if limit:
        urls = urls[:limit]

    extraction_cache = (
        json.loads(EXTRACTION_CACHE.read_text()) if EXTRACTION_CACHE.exists() else {}
    )

    papers: list[Paper] = []
    for url, _ in urls:
        try:
            detail_html = fetch(url)
            info = detail_page_info(detail_html)
            target_url = info["source_url"] or url
            target_html = detail_html if target_url == url else fetch(target_url)
        except RuntimeError as exc:  # source fetch failure -- one paper's problem, not the run's
            print(f"  ! fetch failed, skipping ({type(exc).__name__}): {url}: {exc}")
            continue

        cost = None
        if url in extraction_cache:
            parsed = extraction_cache[url]
        else:
            try:
                parsed, cost, truncated = extract_page(target_html, model, lab_label=LAB_LABEL)
            except Exception as exc:  # refusal, malformed JSON, network error
                print(f"  ! extraction failed, skipping ({type(exc).__name__}): {url}")
                failed_cost = getattr(exc, "cost", None)
                if failed_cost:  # billed by the API even though extraction failed
                    failed_cost["url"] = url
                    history = json.loads(COST.read_text()) if COST.exists() else []
                    history.append(failed_cost)
                    COST.write_text(json.dumps(history, indent=2))
                continue

            cost["url"] = url
            history = json.loads(COST.read_text()) if COST.exists() else []
            history.append(cost)
            COST.write_text(json.dumps(history, indent=2))

            parsed["truncated"] = truncated  # round-tripped through the cache below
            extraction_cache[url] = parsed
            EXTRACTION_CACHE.write_text(json.dumps(extraction_cache, indent=2))

        paper = Paper(
            url=url,
            source_url=target_url,
            title=info["title"],
            date=info["date"] or parsed.get("date"),
            authors=parsed["authors"],
            order_meaningful=parsed.get("order_meaningful", True),
            star_means=parsed.get("star_means"),
            truncated=parsed.get("truncated", False),
        )
        papers.append(paper)
        usd = f"${cost['usd']:.4f}" if cost else "cached "
        print(
            f"  {paper.date}  {len(paper.authors):3d} authors  "
            f"{usd}  {paper.title[:60]}",
            flush=True,
        )
    return papers


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
            p["papers"].append({"title": paper.title, "url": paper.url, "date": paper.date})

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
    ap.add_argument("--limit", type=int, help="cap papers processed (proving runs)")
    args = ap.parse_args()

    papers = collect(args.months, args.model, args.limit)
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
    print(f"\n{len(papers)} papers, {len(rows)} distinct authors, ${spent:.4f} -> {OUT}")


if __name__ == "__main__":
    main()
