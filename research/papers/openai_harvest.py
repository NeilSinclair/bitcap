"""Contributor register for OpenAI, from arXiv metadata and credit sections.

Third lab through the §11 procedure. OpenAI's shape sits between the other two:
like DeepSeek it publishes few, very large papers to arXiv (25-486 authors) under
a collective identity; unlike DeepSeek its credit sections are two-level, naming a
team ("Reasoning Research") and then a role within it ("Foundational
Contributors").

Two constraints found at step 0 and worth stating:

  * **openai.com is unreachable.** Its CDN returns 403 to non-browser clients even
    though robots.txt allows crawling, so the research blog cannot be ingested and
    arXiv is the only usable channel.
  * **Corpus enumeration is manual.** Papers are filed under assorted author
    strings, so no single query returns them; the set below was assembled by
    title search. Same failure as DeepSeek, and the reason §11 gained a step 0.

Usage:
    python research/openai_harvest.py
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from deepseek_harvest import arxiv_query, fetch, looks_like_a_name, normalise_name

ROOT = Path(__file__).parent.parent
OUT = ROOT / "research" / "docs" / "openai_contributors.json"

# Enumerated by hand; see module docstring. Title fragments, matched case-insensitively.
CORPUS = [
    "GPT-4 Technical Report",
    "GPT-4o System Card",
    "OpenAI o1 System Card",
    "Competitive Programming with Large Reasoning Models",
    "gpt-oss-120b",
    "GPT-5 System Card",
    "Model Card for OpenAI Privacy Filter",
]

CREDIT_HEADINGS = re.compile(
    r"(?i)(Authorship,\s*credit|Contributors\b|Contributions\b|Author List)"
)


@dataclass
class Paper:
    """One OpenAI paper and its byline."""

    arxiv_id: str
    title: str
    date: str
    url: str
    authors: list[dict] = field(default_factory=list)
    roles_source: str = "metadata_only"
    order_meaningful: bool = False


def parse_credit_section(page_html: str) -> tuple[list[dict], bool]:
    """Parse a credit section into names with their team and role.

    OpenAI's cards nest two levels of bold label - a team, then a role within it -
    before each name list. Both are captured verbatim rather than mapped to an
    enum, because the vocabulary changes between papers.

    Args:
        page_html: arXiv HTML rendering of the paper.

    Returns:
        Tuple of (author records, whether a credit section was found).
    """
    match = None
    for m in CREDIT_HEADINGS.finditer(page_html):
        # The real section is a heading, not a footer link or a citation.
        window = page_html[max(0, m.start() - 200) : m.start()]
        if "ltx_title" in window or "ltx_section" in window:
            match = m
    if not match:
        return [], False

    # Start after the heading element itself, so "Authorship, credit attribution,
    # and acknowledgments" is not comma-split into three "names".
    after = page_html.find("</h2>", match.start())
    start = after + 5 if 0 < after < match.start() + 400 else match.start()
    section = page_html[start : start + 200_000]
    end = re.search(r"(?i)<h2[^>]*>\s*(References|Appendix)", section)
    if end:
        section = section[: end.start()]

    authors: list[dict] = []
    seen: set[str] = set()
    label = None
    parts = re.split(r'(<span[^>]*ltx_font_bold[^>]*>.*?</span>)', section, flags=re.S)
    for part in parts:
        text = html_mod.unescape(re.sub(r"<[^>]+>", " ", part))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if "ltx_font_bold" in part:
            cleaned = re.sub(r"\d+\s*footnotemark:?\s*\d*", "", text)
            cleaned = re.sub(r"[\s\d]+$", "", cleaned).strip(" :·*†")
            if cleaned and not looks_like_a_name(cleaned):
                label = cleaned  # a heading, not a person
            continue
        for chunk in text.split(","):
            name = chunk.strip(" .;·").rstrip("*†‡").strip()
            # Section headings ("Cybersecurity", "Persuasion", "Pricing") survive the
            # name-shape filter as single words; a credited person has a full name.
            if len(name.split()) < 2 or not looks_like_a_name(name):
                continue
            key = normalise_name(name)
            if key in seen:
                continue
            seen.add(key)
            authors.append({"name": name, "role": label, "departed": False})
    return authors, bool(authors)


def collect() -> list[Paper]:
    """Collect the enumerated OpenAI papers with the best byline available."""
    entries: dict[str, dict] = {}
    for title in CORPUS:
        for e in arxiv_query(f'ti:"{title}"', 6):
            if title.lower().split()[0] in e["title"].lower() and title.lower()[:12] in (
                e["title"].lower()
            ):
                entries.setdefault(e["arxiv_id"].split("v")[0], e)

    papers: list[Paper] = []
    for e in sorted(entries.values(), key=lambda x: x["date"]):
        url = f"https://arxiv.org/abs/{e['arxiv_id']}"
        paper = Paper(e["arxiv_id"], e["title"], e["date"], url)
        try:
            page = fetch(f"https://arxiv.org/html/{e['arxiv_id']}")
        except Exception as exc:
            print(f"  ! html unavailable ({type(exc).__name__}): {e['arxiv_id']}")
            page = ""

        listed, found = parse_credit_section(page) if page else ([], False)
        by_name = {normalise_name(a["name"]): a for a in listed}
        meta = {normalise_name(n) for n in e["authors"]}
        if found:
            paper.roles_source = "credit_section"
            for name in e["authors"]:
                extra = by_name.get(normalise_name(name), {})
                paper.authors.append(
                    {
                        "name": name,
                        "role": extra.get("role"),
                        "departed": False,
                        "in_credit_section": normalise_name(name) in by_name,
                    }
                )
            for key, rec in by_name.items():
                if key not in meta:
                    paper.authors.append(
                        {
                            "name": rec["name"],
                            "role": rec["role"],
                            "departed": False,
                            "in_credit_section": True,
                            "metadata_missing": True,
                        }
                    )
        else:
            paper.authors = [
                {"name": n, "role": None, "departed": False, "in_credit_section": False}
                for n in e["authors"]
            ]
        for i, a in enumerate(paper.authors):
            a["position"] = i + 1
            a["n_authors"] = len(paper.authors)
            a["is_openai"] = True
        papers.append(paper)
    return papers


def aggregate(papers: list[Paper]) -> list[dict]:
    """Build the per-person table."""
    people: dict[str, dict] = defaultdict(
        lambda: {"appearances": 0, "roles": set(), "papers": []}
    )
    for paper in papers:
        for a in paper.authors:
            p = people[a["name"]]
            p["appearances"] += 1
            if a["role"]:
                p["roles"].add(a["role"])
            p["papers"].append({"title": paper.title, "url": paper.url, "date": paper.date})
    rows = [
        dict(
            p,
            name=n,
            roles=sorted(p["roles"]),
            first_seen=min(x["date"] for x in p["papers"]),
            last_seen=max(x["date"] for x in p["papers"]),
        )
        for n, p in people.items()
    ]
    rows.sort(key=lambda r: (-r["appearances"], r["name"]))
    return rows


def main() -> None:
    """Run the harvest and write the register."""
    argparse.ArgumentParser(description=__doc__).parse_args()
    papers = collect()
    rows = aggregate(papers)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generated": date.today().isoformat(),
                "lab": "OpenAI",
                "papers": [p.__dict__ for p in papers],
                "people": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{len(papers)} papers, {len(rows)} distinct authors -> {OUT}")
    for p in papers:
        roled = sum(1 for a in p.authors if a["role"])
        print(
            f"  {p.date}  {len(p.authors):4d} authors  {p.roles_source:15} "
            f"with-role={roled:4d}  {p.title[:40]}"
        )
    print(f"\nrepeat contributors (>=2): {sum(1 for r in rows if r['appearances'] >= 2)}")


if __name__ == "__main__":
    main()
