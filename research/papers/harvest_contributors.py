"""Derive a lab's contributor register from the author lists on its own publications.

Spike (n=1, Anthropic). The question: if you count who signs a lab's research
output over a rolling window, do you get a defensible "key people" list without
ever touching an org chart or LinkedIn?

Two channels carry full author lists:
  * alignment.anthropic.com  - byline with numbered affiliations (external
    collaborators and Anthropic Fellows are distinguishable).
  * transformer-circuits.pub - byline with an explicit ``*`` core-contributor
    marker, which is a lab-authored seniority signal.

anthropic.com/research is deliberately excluded: those pages are summaries with
no bylines.

Usage:
    python research/harvest_contributors.py [--months 3]
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from byline import parse_page

ALIGNMENT_INDEX = "https://alignment.anthropic.com/"
TC_INDEX = "https://transformer-circuits.pub/"
CACHE = Path(__file__).parent.parent / "docs" / "contributor_cache"
OUT = Path(__file__).parent.parent / "docs" / "anthropic_contributors.json"
FALLBACK_COST = Path(__file__).parent.parent / "docs" / "fallback_cost.json"
FALLBACK_CACHE = Path(__file__).parent.parent / "docs" / "fallback_bylines.json"
UA = "bitcap-case-study research spike (contact: neilaf4@gmail.com)"

MONTHS = {
    m: i + 1
    for i, m in enumerate(
        "January February March April May June July August September "
        "October November December".split()
    )
}


@dataclass
class Article:
    """One publication with its byline.

    Attributes:
        url: Resolvable primary source.
        title: Article title as published.
        date: ISO date; day precision on transformer-circuits, month precision
            (day 1) on the alignment blog, which publishes no day.
        channel: Source channel id.
        authors: Author records in published order.
    """

    url: str
    title: str
    date: str
    channel: str
    authors: list[dict] = field(default_factory=list)
    star_means: str | None = None
    order_meaningful: bool = True
    byline_source: str = "parser"


def fetch(url: str) -> str:
    """Fetch a URL, caching to disk so re-runs are free and idempotent.

    Args:
        url: Absolute URL to fetch.

    Returns:
        Decoded response body.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_")[:150] + ".html"
    path = CACHE / key
    if path.exists():
        return path.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    body = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    path.write_text(body, encoding="utf-8")
    time.sleep(1.0)  # politeness: one request per second per host
    return body


def strip_tags(fragment: str) -> str:
    """Reduce an HTML fragment to collapsed plain text."""
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", text).strip()


def list_alignment_index(index_html: str) -> list[dict]:
    """Extract (url, title, date) for every alignment-blog article.

    The index groups articles under ``<div class="date">Month YYYY</div>``
    headers and publishes no day, so dates land on the first of the month.

    Args:
        index_html: Raw HTML of the alignment blog index.

    Returns:
        Article stubs in document order.
    """
    out: list[dict] = []
    current = None
    pattern = re.compile(
        r'<div class="date">\s*([A-Z][a-z]+)\s+(\d{4})\s*</div>'
        r'|<a href="([^"]+)" class="note">\s*<h3>(.*?)</h3>',
        re.S,
    )
    for m in pattern.finditer(index_html):
        if m.group(1):
            current = f"{m.group(2)}-{MONTHS[m.group(1)]:02d}-01"
        elif current:
            out.append(
                {
                    "url": ALIGNMENT_INDEX + m.group(3).lstrip("/"),
                    "title": strip_tags(m.group(4)),
                    "date": current,
                    "channel": "alignment.anthropic.com",
                }
            )
    return out


def list_tc_index(index_html: str) -> list[dict]:
    """Extract (url, title, date) for every transformer-circuits article.

    Args:
        index_html: Raw HTML of the transformer-circuits index.

    Returns:
        Article stubs in document order. Cross-posts to other hosts are skipped;
        they are collected from their own channel.
    """
    out: list[dict] = []
    pattern = re.compile(
        r'href=[\'"]([^\'"]+)[\'"]\s+data-date="(\d{4}-\d{2}-\d{2})"[^>]*>(.*?)</a>', re.S
    )
    for href, dt, body in pattern.findall(index_html):
        if href.startswith("http"):
            continue
        title = strip_tags(re.sub(r"(?is)<div class=.description.*", "", body))
        out.append(
            {
                "url": TC_INDEX + href,
                "title": title.split("  ")[0].strip(),
                "date": dt,
                "channel": "transformer-circuits.pub",
            }
        )
    return out


def collect(months: int, llm_fallback: bool = False, model: str = "claude-sonnet-5") -> list[Article]:
    """Collect in-window articles with bylines from both channels.

    Pages whose markup the deterministic parser cannot read are the register's
    blind spot -- 8 of 54 over a 12-month window. With ``llm_fallback`` set, those
    pages only are sent to a model, and every author it returns is tagged
    ``source: "llm"`` so a model-asserted affiliation is never mistaken for one
    the page stated. Cost is appended per call to ``fallback_cost.json``.

    Args:
        months: Window length in months, ending today.
        llm_fallback: Whether to call the model on pages with no parseable byline.
        model: Model id used for the fallback.

    Returns:
        Articles inside the window, oldest first.
    """
    today = date.today()
    m = today.month - months
    y = today.year + (m - 1) // 12
    cutoff = f"{y:04d}-{(m - 1) % 12 + 1:02d}-01"

    stubs = list_alignment_index(fetch(ALIGNMENT_INDEX)) + list_tc_index(fetch(TC_INDEX))
    stubs = [s for s in stubs if s["date"] >= cutoff]

    articles: list[Article] = []
    for s in sorted(stubs, key=lambda s: s["date"]):
        parsed = parse_page(fetch(s["url"]))
        art = Article(
            s["url"],
            s["title"],
            parsed["date"] or s["date"],
            s["channel"],
            parsed["authors"],
        )
        art.star_means = parsed["star_means"]
        art.order_meaningful = parsed["order_meaningful"]
        if not art.authors:
            if llm_fallback:
                art = _llm_fallback(art, fetch(s["url"]), model)
            else:
                print(f"  ! no byline parsed: {art.url}")
        articles.append(art)
    return articles


def _llm_fallback(art: Article, raw_html: str, model: str) -> Article:
    """Fill an unparseable byline with a model call, tagging every field as such.

    Args:
        art: Article whose deterministic parse returned no authors.
        raw_html: Raw page HTML.
        model: Model id.

    Returns:
        The article, with LLM-sourced authors if the call succeeded.
    """
    cache = json.loads(FALLBACK_CACHE.read_text()) if FALLBACK_CACHE.exists() else {}
    if art.url in cache:
        # Re-runs must be idempotent and must not re-spend on a page already read.
        parsed = cache[art.url]
        art.authors = parsed["authors"]
        art.date = parsed.get("date") or art.date
        art.star_means = parsed.get("star_means")
        art.order_meaningful = parsed.get("order_meaningful", True)
        art.byline_source = "llm-cached"
        return art

    from llm_byline import extract_page

    try:
        parsed, cost, _truncated = extract_page(raw_html, model)
    except Exception as exc:  # a fallback failure must not abort the harvest
        print(f"  ! no byline parsed, fallback failed ({type(exc).__name__}): {art.url}")
        return art

    for author in parsed["authors"]:
        author["source"] = "llm"
        author.setdefault("marks", [])
        author.setdefault("role", "author")
        author["is_core"] = None
    for i, author in enumerate(parsed["authors"]):
        author["position"] = i + 1
        author["n_authors"] = len(parsed["authors"])

    art.authors = parsed["authors"]
    art.date = parsed.get("date") or art.date
    art.star_means = parsed.get("star_means")
    art.order_meaningful = parsed.get("order_meaningful", True)
    art.byline_source = "llm"

    cache[art.url] = {
        "authors": parsed["authors"],
        "date": parsed.get("date"),
        "star_means": parsed.get("star_means"),
        "order_meaningful": parsed.get("order_meaningful", True),
    }
    FALLBACK_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    cost["url"] = art.url
    history = json.loads(FALLBACK_COST.read_text()) if FALLBACK_COST.exists() else []
    history.append(cost)
    FALLBACK_COST.write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"  ~ llm fallback: {len(art.authors)} authors, ${cost['usd']:.4f}  {art.url}")
    return art


def aggregate(articles: list[Article]) -> list[dict]:
    """Build the per-person frequency table.

    Confirmed aliases from config/aliases.yaml are applied here and only here:
    the per-article bylines keep exactly what each page published, so a merge is
    reversible by editing config rather than re-running extraction.

    Args:
        articles: In-window articles with parsed bylines.

    Returns:
        Person records sorted by core-contributor count, then appearances.
    """
    from alias_candidates import load_confirmed

    aliases = load_confirmed()
    people: dict[str, dict] = defaultdict(
        lambda: {
            "appearances": 0,
            "core": 0,
            "fellow": 0,
            "external": 0,
            "llm_sourced": 0,
            "first_author": 0,
            "last_author": 0,
            "affiliations": set(),
            "anthropic_bylines": 0,
            "articles": [],
            "aliases": [],
        }
    )
    for art in articles:
        for a in art.authors:
            canonical = aliases.get(a["name"], a["name"])
            p = people[canonical]
            p["aliases"] = sorted(set(p.get("aliases", [])) | ({a["name"]} if canonical != a["name"] else set()))
            p["appearances"] += 1
            p["core"] += 1 if a.get("is_core") else 0
            p["fellow"] += 1 if a.get("is_fellow") else 0
            p["external"] += 0 if a["is_anthropic"] else 1
            p["llm_sourced"] += 1 if a.get("source") == "llm" else 0
            if art.order_meaningful:
                p["first_author"] += 1 if a["position"] == 1 else 0
                p["last_author"] += (
                    1 if a["position"] == a["n_authors"] and a["n_authors"] > 1 else 0
                )
            p["anthropic_bylines"] += 1 if a["is_anthropic"] else 0
            p["affiliations"].update(a["affiliations"])
            if art.url not in [x["url"] for x in p["articles"]]:
                p["articles"].append({"title": art.title, "url": art.url, "date": art.date})

    rows = []
    for name, p in people.items():
        p = dict(p, name=name, affiliations=sorted(p["affiliations"]))
        rows.append(p)
    rows.sort(key=lambda r: (-r["core"], -r["appearances"], -r["last_author"], r["name"]))
    return rows


def collaborator_orgs(articles: list[Article]) -> list[dict]:
    """Register the outside organisations a lab co-publishes with.

    Organisations only, never their people: per docs/decisions.md, external
    individuals are recorded in the byline data but never enriched. This is the
    lab profile's future-watchlist section, and it falls out of the bylines we
    already parse at no extra cost.

    Args:
        articles: In-window articles with parsed bylines.

    Returns:
        One record per non-lab organisation, most-collaborated first.
    """
    orgs: dict[str, dict] = {}
    for art in articles:
        for author in art.authors:
            for aff in author["affiliations"]:
                if aff == "Anthropic":
                    continue
                rec = orgs.setdefault(
                    aff,
                    {"organisation": aff, "people": set(), "articles": [], "is_fellowship": False},
                )
                rec["people"].add(author["name"])
                rec["is_fellow" "ship"] = rec["is_fellowship"] or bool(author.get("is_fellow"))
                if art.url not in [a["url"] for a in rec["articles"]]:
                    rec["articles"].append(
                        {"title": art.title, "url": art.url, "date": art.date}
                    )
    rows = [
        {
            "organisation": r["organisation"],
            "n_people": len(r["people"]),
            "n_articles": len(r["articles"]),
            "is_fellowship": r["is_fellowship"],
            "articles": r["articles"],
        }
        for r in orgs.values()
    ]
    rows.sort(key=lambda r: (-r["n_articles"], -r["n_people"], r["organisation"]))
    return rows


def main() -> None:
    """Run the harvest and write the JSON register plus a console summary."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument(
        "--llm-fallback",
        action="store_true",
        help="send pages with no parseable byline to a model (costs money)",
    )
    ap.add_argument("--model", default="claude-sonnet-5")
    args = ap.parse_args()

    articles = collect(args.months, args.llm_fallback, args.model)
    rows = aggregate(articles)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generated": date.today().isoformat(),
                "window_months": args.months,
                "articles": [a.__dict__ for a in articles],
                "people": rows,
                "collaborator_orgs": collaborator_orgs(articles),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    orgs = collaborator_orgs(articles)
    print(f"\n{len(articles)} articles, {len(rows)} distinct authors -> {OUT}")
    print(f"\ncollaborator organisations (watchlist, orgs only):")
    for o in orgs:
        tag = " [fellowship]" if o["is_fellowship"] else ""
        print(f"  {o['organisation'][:38]:38} {o['n_articles']} articles, "
              f"{o['n_people']} people{tag}")
    for a in articles:
        print(f"  {a.date}  {len(a.authors):3d} authors  {a.title[:70]}")
    print(f"\n{'name':28} app core 1st last  affiliations")
    for r in rows[:45]:
        print(
            f"{r['name'][:28]:28} {r['appearances']:3d} {r['core']:4d} "
            f"{r['first_author']:3d} {r['last_author']:4d}  {', '.join(r['affiliations'])[:44]}"
        )


if __name__ == "__main__":
    main()
