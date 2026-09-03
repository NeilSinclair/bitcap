"""Contributor register for Mistral AI, from its own announcements as the candidate list.

Mistral publishes no publications listing page at all -- confirmed live,
every path tried under `mistral.ai/research` and similar 404s. There is
also no reliable arXiv query that enumerates the lab's output: `all:Mistral`
collides with the "MISTRAL" acronym used in unrelated astronomy/plasma
physics papers, and author-affiliation search is not supported by arXiv at
all (confirmed live against its own API docs -- see docs/decisions.md D16's
discussion of why a lab can't be searched for directly).

What Mistral does reliably do is announce every named model release on its
own blog -- already fetched for the announcements leg
(`research/docs/announcements.json`), zero additional cost to reuse. Two of
the four most recent releases in the current window ("Shieldstral",
"Robostral Navigate") turned out to have real arXiv papers; the older two
("Leanstral 1.5", "Mistral OCR 4") do not, confirmed live -- so this script
over-generates candidates from every Mistral announcement title in the
window and lets arXiv resolution (`arxiv_resolve.py`, shared with
`meta_harvest.py`) be the real filter, rather than maintaining a hand-curated
list of "known model names" that would already be stale (Shieldstral and
Robostral were not on the list this project had compiled before this
session -- checking the live announcements found them, the list would not
have).

**Disambiguation is exact-match only, unlike Meta's.** Meta's detail pages
carry a real meta-description usable as a disambiguation signal for
`arxiv_resolve.py`'s relaxed-match fallback. Mistral's announcement pages
have no equivalent -- their extracted `text` is the full page including nav
chrome, not a clean summary -- so no `desc` is passed, which makes the
relaxed fallback correctly always reject (an empty description scores 0.0
overlap against everything). Every paper here is therefore either an exact
title match or unresolved; nothing is guessed at from a weak signal. Both
real papers in the current window resolve on exact match, so this has not
yet cost anything in practice -- noted as a real limitation, not silently
assumed away, in case a future title diverges the way Meta's did.

Usage:
    python research/papers/mistral_harvest.py [--months 3] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from arxiv_resolve import resolve_title

ROOT = Path(__file__).parent.parent.parent

ANNOUNCEMENTS = ROOT / "research" / "docs" / "announcements.json"
CACHE = ROOT / "research" / "docs" / "mistral_cache"
OUT = ROOT / "research" / "docs" / "mistral_contributors.json"
COST = ROOT / "research" / "docs" / "mistral_llm_cost.json"
UNRESOLVED = ROOT / "research" / "docs" / "mistral_unresolved.json"
# Extraction results keyed by the announcement URL, so a re-run never
# re-bills a paper already extracted -- same idempotency guarantee as
# fetch()'s disk cache, one layer up.
EXTRACTION_CACHE = ROOT / "research" / "docs" / "mistral_extraction_cache.json"

LAB_LABEL = "Mistral AI"
UA = "bitcap-case-study research spike (contact: neilaf4@gmail.com)"

# Mistral's own announcement titles carry phrasing the paper's own title
# drops -- confirmed live: "Introducing Shieldstral." on the blog is
# "Shieldstral" on arXiv. Same site-vs-paper title drift already found for
# Meta AI (D16), a prefix here instead of a suffix.
TITLE_NOISE = re.compile(r"^(Introducing|Announcing)\s+", re.I)


@dataclass
class Paper:
    """One Mistral AI publication and its byline.

    Attributes:
        announcement_url: Mistral's own blog post -- always resolvable, the
            primary citation regardless of arXiv-resolution confidence.
        arxiv_id: Resolved arXiv id.
        source_url: The arXiv HTML page the byline was extracted from.
        title: Paper title, cleaned of announcement phrasing.
        date: ISO publication date, read from the resolved arXiv entry.
        resolution: How the arXiv id was found -- always "exact_title" in
            practice so far; see the module docstring for why the relaxed
            fallback is effectively unavailable here.
        authors: Author records from the LLM extractor.
        order_meaningful: Whether author order carries seniority information.
        star_means: What the page's own footnote says '*' denotes, if any.
    """

    announcement_url: str
    arxiv_id: str
    source_url: str
    title: str
    date: str
    resolution: str
    authors: list[dict] = field(default_factory=list)
    order_meaningful: bool = True
    star_means: str | None = None


def fetch(url: str, pause: float = 1.5, retries: int = 3) -> str:
    """Fetch a URL, caching to disk so re-runs are free and idempotent.

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


def clean_title(raw: str) -> str:
    """Strip announcement phrasing that isn't part of the paper's own title.

    Args:
        raw: Mistral's own announcement title.

    Returns:
        Title with a leading "Introducing"/"Announcing" and a trailing
        period stripped.
    """
    return TITLE_NOISE.sub("", raw).rstrip(".").strip()


def list_candidate_titles(months: int) -> list[dict]:
    """Read Mistral's own in-window announcements as candidate paper titles.

    Over-generates: most Mistral announcements are product/feature posts,
    not model releases, and will not resolve to a paper -- confirmed live
    for two of four real 2026 titles checked. Resolution
    (`arxiv_resolve.resolve_title`) is the real filter, not this step; see
    the module docstring for why a maintained "known model names" list was
    rejected in favour of this.

    Args:
        months: Window length in months.

    Returns:
        List of dicts with `title` (cleaned), `date`, `announcement_url`.
    """
    cutoff = (date.today() - timedelta(days=months * 30.5)).isoformat()
    articles = json.loads(ANNOUNCEMENTS.read_text())
    out = []
    for a in articles:
        if a.get("lab") != "mistral" or a["date"] < cutoff:
            continue
        out.append(
            {
                "title": clean_title(a["title"]),
                "date": a["date"],
                "announcement_url": a["url"],
            }
        )
    return out


def collect(months: int, model: str, limit: int | None = None) -> tuple[list[Paper], list[dict]]:
    """Collect in-window Mistral AI papers with LLM-extracted bylines.

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
    candidates = list_candidate_titles(months)
    if limit:
        candidates = candidates[:limit]

    extraction_cache = (
        json.loads(EXTRACTION_CACHE.read_text()) if EXTRACTION_CACHE.exists() else {}
    )

    papers: list[Paper] = []
    unresolved: list[dict] = []
    for c in candidates:
        # No description signal available here (see module docstring) --
        # relaxed matching is effectively disabled, only an exact title hit
        # is ever accepted.
        match = resolve_title(c["title"], "")
        if not match:
            unresolved.append(
                {
                    "title": c["title"],
                    "announcement_url": c["announcement_url"],
                    "reason": "no_arxiv_match",
                }
            )
            print(f"  ! no arXiv match, skipping: {c['title'][:60]}")
            continue

        source_url = f"https://arxiv.org/html/{match['arxiv_id']}"
        try:
            target_html = fetch(source_url)
        except RuntimeError as exc:
            print(f"  ! arXiv HTML unavailable, skipping ({type(exc).__name__}): {c['title'][:60]}")
            unresolved.append(
                {
                    "title": c["title"],
                    "announcement_url": c["announcement_url"],
                    "reason": "arxiv_html_unavailable",
                }
            )
            continue

        cost = None
        cache_key = c["announcement_url"]
        if cache_key in extraction_cache:
            parsed = extraction_cache[cache_key]
        else:
            try:
                parsed, cost = extract_page(target_html, model, lab_label=LAB_LABEL)
            except Exception as exc:  # refusal, malformed JSON, network error
                print(f"  ! extraction failed, skipping ({type(exc).__name__}): {c['title'][:60]}")
                failed_cost = getattr(exc, "cost", None)
                if failed_cost:  # billed by the API even though extraction failed
                    failed_cost["url"] = cache_key
                    history = json.loads(COST.read_text()) if COST.exists() else []
                    history.append(failed_cost)
                    COST.write_text(json.dumps(history, indent=2))
                unresolved.append(
                    {
                        "title": c["title"],
                        "announcement_url": c["announcement_url"],
                        "reason": "extraction_failed",
                    }
                )
                continue

            cost["url"] = cache_key
            history = json.loads(COST.read_text()) if COST.exists() else []
            history.append(cost)
            COST.write_text(json.dumps(history, indent=2))

            extraction_cache[cache_key] = parsed
            EXTRACTION_CACHE.write_text(json.dumps(extraction_cache, indent=2))

        paper = Paper(
            announcement_url=c["announcement_url"],
            arxiv_id=match["arxiv_id"],
            source_url=source_url,
            title=c["title"],
            date=match["date"],
            resolution=match["resolution"],
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
            p["papers"].append(
                {"title": paper.title, "url": paper.announcement_url, "date": paper.date}
            )

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
