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
CACHE = Path(__file__).parent / "docs" / "contributor_cache"
OUT = Path(__file__).parent / "docs" / "anthropic_contributors.json"
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


def collect(months: int) -> list[Article]:
    """Collect in-window articles with bylines from both channels.

    Args:
        months: Window length in months, ending today.

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
            print(f"  ! no byline parsed: {art.url}")
        articles.append(art)
    return articles


def aggregate(articles: list[Article]) -> list[dict]:
    """Build the per-person frequency table.

    Args:
        articles: In-window articles with parsed bylines.

    Returns:
        Person records sorted by core-contributor count, then appearances.
    """
    people: dict[str, dict] = defaultdict(
        lambda: {
            "appearances": 0,
            "core": 0,
            "fellow": 0,
            "external": 0,
            "first_author": 0,
            "last_author": 0,
            "affiliations": set(),
            "anthropic_bylines": 0,
            "articles": [],
        }
    )
    for art in articles:
        for a in art.authors:
            p = people[a["name"]]
            p["appearances"] += 1
            p["core"] += 1 if a.get("is_core") else 0
            p["fellow"] += 1 if a.get("is_fellow") else 0
            p["external"] += 0 if a["is_anthropic"] else 1
            if art.order_meaningful:
                p["first_author"] += 1 if a["position"] == 1 else 0
                p["last_author"] += (
                    1 if a["position"] == a["n_authors"] and a["n_authors"] > 1 else 0
                )
            p["anthropic_bylines"] += 1 if a["is_anthropic"] else 0
            p["affiliations"].update(a["affiliations"])
            p["articles"].append({"title": art.title, "url": art.url, "date": art.date})

    rows = []
    for name, p in people.items():
        p = dict(p, name=name, affiliations=sorted(p["affiliations"]))
        rows.append(p)
    rows.sort(key=lambda r: (-r["core"], -r["appearances"], -r["last_author"], r["name"]))
    return rows


def main() -> None:
    """Run the harvest and write the JSON register plus a console summary."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=3)
    args = ap.parse_args()

    articles = collect(args.months)
    rows = aggregate(articles)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generated": date.today().isoformat(),
                "window_months": args.months,
                "articles": [a.__dict__ for a in articles],
                "people": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\n{len(articles)} articles, {len(rows)} distinct authors -> {OUT}")
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
