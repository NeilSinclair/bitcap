"""Contributor register for DeepSeek, from arXiv metadata and paper author lists.

DeepSeek's publishing shape is the opposite of Anthropic's: a handful of papers a
year, each with 40-320 authors, filed to arXiv under the collective byline
"DeepSeek-AI" rather than to a research blog. Two consequences drive this module:

  * **Author order carries no information.** The papers state that authors are
    listed alphabetically by first name, so first/last-author scoring is invalid.
  * **The papers publish their own role structure.** An "Author List" appendix
    groups authors by contribution role and marks departures with an asterisk
    ("individuals who have departed from our team"), which is a personnel signal
    the lab prints itself.

Usage:
    python research/deepseek_harvest.py
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import fetch_cache

ROOT = Path(__file__).parent.parent.parent
CACHE = ROOT / "research" / "docs" / "deepseek_cache"
OUT = ROOT / "research" / "docs" / "deepseek_contributors.json"
ARXIV = "https://export.arxiv.org/api/query"
UA = "bitcap-case-study research spike (contact: neilaf4@gmail.com)"

# Papers DeepSeek published without the collective byline, so an author-string
# query misses them. Found by title; extend as more are identified.
EXTRA_TITLES = ["DeepSeekMath-V2"]


@dataclass
class Paper:
    """One DeepSeek paper and its byline.

    Attributes:
        arxiv_id: Versioned arXiv id.
        title: Paper title.
        date: ISO publication date.
        url: Resolvable primary source.
        authors: Author records in published (alphabetical) order.
        roles_source: Where role information came from - "author_list" when the
            paper's own appendix was parsed, "metadata_only" otherwise.
    """

    arxiv_id: str
    title: str
    date: str
    url: str
    authors: list[dict] = field(default_factory=list)
    roles_source: str = "metadata_only"
    order_meaningful: bool = False  # papers state the order is alphabetical


def fetch(url: str, retries: int | None = None) -> str:
    """Fetch a URL through the shared cache, throttle and retry policy.

    This function had no retry at all, which is why DeepSeek and OpenAI (which
    imports it) both died on a single 429 on 2026-09-04 while the two
    harvesters that did retry survived three attempts each. Retry, backoff,
    `Retry-After` and the shared arXiv throttle now live in `fetch_cache.py`
    (docs/decisions.md D53).

    Args:
        url: Absolute URL.
        retries: Attempts before giving up. None takes the configured value.

    Returns:
        Decoded response body.

    Raises:
        RuntimeError: If every attempt fails. One dead source is a partial run,
            not a dead one (D27).
    """
    return fetch_cache.fetch(
        url, cache_dir=CACHE, suffix=".txt", user_agent=UA, retries=retries
    )


def arxiv_query(search: str, max_results: int = 60) -> list[dict]:
    """Run an arXiv API query and return entry metadata.

    Args:
        search: arXiv search_query expression.
        max_results: Result cap.

    Returns:
        Entry dicts with id, title, date and metadata author names.
    """
    url = f"{ARXIV}?" + urllib.parse.urlencode(
        {
            "search_query": search,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    xml = fetch(url)
    out = []
    for entry in re.findall(r"(?s)<entry>(.*?)</entry>", xml):
        aid = re.search(r"<id>(.*?)</id>", entry).group(1).rsplit("/", 1)[-1]
        title = re.sub(r"\s+", " ", re.search(r"(?s)<title>(.*?)</title>", entry).group(1)).strip()
        published = re.search(r"<published>(.*?)</published>", entry).group(1)[:10]
        names = [html_mod.unescape(n).strip() for n in re.findall(r"<name>(.*?)</name>", entry)]
        out.append(
            {
                "arxiv_id": aid,
                "title": html_mod.unescape(title),
                "date": published,
                "authors": [n for n in names if n and n not in {":", "DeepSeek-AI"}],
            }
        )
    return out


LOWER_WORD = re.compile(r"\b[a-z]{2,}\b")


def looks_like_a_name(text: str) -> bool:
    """Whether a comma-separated chunk is a person's name rather than prose.

    The R1 appendix follows its author list with narrative contribution notes in
    the same markup ("Junxiao Song proposed the GRPO algorithm, implemented the
    initial version, ..."), so splitting on commas alone turns sentence fragments
    into authors. Names are short and have no lowercase words.

    Args:
        text: Candidate chunk, already stripped.

    Returns:
        True if the chunk has a name's shape.
    """
    if not text or len(text) > 40:
        return False
    tokens = text.split()
    if not 1 <= len(tokens) <= 4:
        return False
    if LOWER_WORD.search(text):
        return False
    if len(tokens) == 1 and text.isupper():
        return False  # acronyms from the prose: PPO, GRPO, STEM
    return bool(re.match(r"^[A-Z]", text))


def normalise_name(name: str) -> str:
    """Collapse initial spacing so "Z. F. Wu" and "Z.F. Wu" compare equal."""
    return re.sub(r"\s+", " ", re.sub(r"(?<=\.)\s+(?=[A-Z]\.)", "", name)).strip()


def parse_author_list(page_html: str) -> tuple[list[dict], bool]:
    """Parse a paper's "Author List" appendix into roles and departure flags.

    The appendix reads e.g. ``Research & Engineering: Anyi Xu, Bingxuan Wang*, ...``
    where ``*`` marks someone who has left the team. Role labels vary between
    papers, so they are taken verbatim rather than mapped to a fixed enum.

    Args:
        page_html: arXiv HTML rendering of the paper.

    Returns:
        Tuple of (author records, whether an author list was found).
    """
    idx = page_html.rfind("Author List")
    if idx < 0:
        return [], False
    tail = page_html[idx + len("Author List") :]
    end = min(
        (m.start() for m in [re.search(r"<h2", tail), re.search(r"Acknowledgment", tail)] if m),
        default=len(tail),
    )
    section = tail[:end] if end > 500 else tail[:120_000]

    authors: list[dict] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'<span[^>]*ltx_font_bold[^>]*>([^<]{3,60}?):?\s*</span>(.*?)(?=<span[^>]*ltx_font_bold|</section>|$)',
        re.S,
    )
    for label, block in pattern.findall(section):
        role = html_mod.unescape(label).strip().strip(":")
        text = html_mod.unescape(re.sub(r"<[^>]+>", " ", block))
        for chunk in text.split(","):
            name = re.sub(r"\s+", " ", chunk).strip().lstrip(":").strip()
            departed = name.endswith("*")
            name = name.rstrip("*").strip(" ;.")
            if not looks_like_a_name(name):
                continue
            if name in seen:
                continue
            seen.add(name)
            authors.append({"name": name, "role": role, "departed": departed})
    return authors, bool(authors)


def collect() -> list[Paper]:
    """Collect DeepSeek papers with the richest byline available for each.

    Returns:
        Papers, oldest first.
    """
    entries = arxiv_query('au:"DeepSeek-AI"')
    have = {e["arxiv_id"].split("v")[0] for e in entries}
    for title in EXTRA_TITLES:
        for e in arxiv_query(f'ti:"{title}"', 5):
            if e["arxiv_id"].split("v")[0] not in have and title.lower() in e["title"].lower():
                entries.append(e)

    papers: list[Paper] = []
    for e in sorted(entries, key=lambda x: x["date"]):
        url = f"https://arxiv.org/abs/{e['arxiv_id']}"
        paper = Paper(e["arxiv_id"], e["title"], e["date"], url)
        try:
            page = fetch(f"https://arxiv.org/html/{e['arxiv_id']}")
        except Exception as exc:
            print(f"  ! html unavailable ({type(exc).__name__}): {e['arxiv_id']}")
            page = ""

        listed, found = parse_author_list(page) if page else ([], False)
        by_name = {normalise_name(a["name"]): a for a in listed}
        if found:
            paper.roles_source = "author_list"
            # arXiv metadata is the authoritative author set; the appendix adds
            # role and departure status where the names line up.
            for name in e["authors"]:
                extra = by_name.get(normalise_name(name), {})
                paper.authors.append(
                    {
                        "name": name,
                        "role": extra.get("role"),
                        "departed": extra.get("departed", False),
                        "in_author_list": normalise_name(name) in by_name,
                    }
                )
            meta = {normalise_name(n) for n in e["authors"]}
            for name, rec in by_name.items():
                if name not in meta:
                    paper.authors.append(
                        {
                            "name": name,
                            "role": rec["role"],
                            "departed": rec["departed"],
                            "in_author_list": True,
                            "metadata_missing": True,
                        }
                    )
        else:
            paper.authors = [
                {"name": n, "role": None, "departed": False, "in_author_list": False}
                for n in e["authors"]
            ]
        for i, a in enumerate(paper.authors):
            a["position"] = i + 1
            a["n_authors"] = len(paper.authors)
            a["is_deepseek"] = True  # collective byline; no per-author affiliation
        papers.append(paper)
    return papers


def aggregate(papers: list[Paper]) -> list[dict]:
    """Build the per-person table.

    Args:
        papers: Papers with parsed bylines.

    Returns:
        Person records, most papers first.
    """
    people: dict[str, dict] = defaultdict(
        lambda: {
            "appearances": 0,
            "core": 0,
            "roles": set(),
            "ever_departed": False,
            "departed_on": [],
            "papers": [],
        }
    )
    for paper in papers:
        for a in paper.authors:
            p = people[a["name"]]
            p["appearances"] += 1
            if a["role"]:
                p["roles"].add(a["role"])
            if re.search(r"core", a["role"] or "", re.I):
                p["core"] += 1
            if a["departed"]:
                p["ever_departed"] = True
                p["departed_on"].append(paper.title)
            p["papers"].append({"title": paper.title, "url": paper.url, "date": paper.date})

    rows = []
    for name, p in people.items():
        rows.append(
            dict(
                p,
                name=name,
                roles=sorted(p["roles"]),
                first_seen=min(x["date"] for x in p["papers"]),
                last_seen=max(x["date"] for x in p["papers"]),
            )
        )
    rows.sort(key=lambda r: (-r["appearances"], r["name"]))
    return rows


def main() -> None:
    """Run the harvest and write the register."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()

    papers = collect()
    rows = aggregate(papers)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "generated": date.today().isoformat(),
                "lab": "DeepSeek",
                "papers": [p.__dict__ for p in papers],
                "people": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{len(papers)} papers, {len(rows)} distinct authors -> {OUT}")
    for p in papers:
        dep = sum(1 for a in p.authors if a["departed"])
        print(
            f"  {p.date}  {len(p.authors):4d} authors  roles={p.roles_source:14} "
            f"departed={dep:3d}  {p.title[:46]}"
        )
    print(f"\nrepeat contributors (>=2 papers): {sum(1 for r in rows if r['appearances'] >= 2)}")
    print(f"ever marked departed: {sum(1 for r in rows if r['ever_departed'])}")


if __name__ == "__main__":
    main()
