"""Title-based arXiv resolution, shared across labs whose own pages carry no
resolvable citation.

Extracted from `meta_harvest.py` (docs/decisions.md D16) once
`mistral_harvest.py` needed the identical logic: a lab's own page names a
paper but does not link to it, so the paper has to be found on arXiv by
title match instead. Kept as one module, not duplicated per lab, because the
matching logic has already needed two real bug fixes (D16) -- an
exact-match query that misses on site-vs-arXiv title drift, and a
relaxed-match fallback that has to score every candidate, not just the
top-ranked one, since arXiv's own relevance ranking put the correct paper
second behind an unrelated result sharing only an acronym. A second copy of
this logic would need every future fix applied twice.
"""

from __future__ import annotations

import html as html_mod
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
CACHE = ROOT / "research" / "docs" / "arxiv_cache"
ARXIV_API = "https://export.arxiv.org/api/query"
UA = "bitcap-case-study research spike (contact: neilaf4@gmail.com)"

# Calibrated live on real 2026 papers during the Meta AI proving run
# (docs/decisions.md D16): the correct relaxed-search match scored 1.00
# overlap against the lab's own description every time checked so far, the
# closest false positive scored 0.19. 0.3 leaves margin without requiring an
# exact match, on a genuinely small calibration set -- worth revisiting as
# more labs go through this path.
OVERLAP_THRESHOLD = 0.3
_STOPWORDS = {
    "a", "an", "the", "of", "for", "to", "in", "on", "and", "or", "with",
    "is", "are", "be", "this", "that", "from", "as", "by", "we", "present",
    "system", "via", "using", "based", "into", "at", "its", "their",
}


def fetch(url: str, pause: float = 3.0, retries: int = 3) -> str:
    """Fetch a URL, caching to disk so re-runs are free and idempotent.

    Args:
        url: Absolute URL.
        pause: Seconds to wait after a live fetch -- defaults to arXiv's own
            rate guidance, since every caller of this function is an arXiv
            URL.
        retries: Attempts before giving up.

    Returns:
        Decoded response body.

    Raises:
        RuntimeError: If every attempt fails. The caller decides whether one
            dead source aborts the whole run.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_")[:150] + ".xml"
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


def arxiv_query(query_expr: str, max_results: int = 5) -> list[dict]:
    """Run an arXiv API query and return entry metadata.

    Args:
        query_expr: A `search_query` expression, e.g. `ti:"Some Title"`.
        max_results: Result cap.

    Returns:
        Entry dicts with `arxiv_id`, `title`, `date` and `summary`.
    """
    url = f"{ARXIV_API}?search_query={urllib.parse.quote(query_expr)}&max_results={max_results}"
    xml = fetch(url)
    out = []
    for entry in re.findall(r"(?s)<entry>(.*?)</entry>", xml):
        idm = re.search(r"<id>(.*?)</id>", entry)
        t = re.search(r"(?s)<title>(.*?)</title>", entry)
        pub = re.search(r"<published>(.*?)</published>", entry)
        summ = re.search(r"(?s)<summary>(.*?)</summary>", entry)
        if not (idm and t and pub):
            continue
        out.append(
            {
                "arxiv_id": idm.group(1).rsplit("/", 1)[-1],
                "title": html_mod.unescape(re.sub(r"\s+", " ", t.group(1))).strip(),
                "date": pub.group(1)[:10],
                "summary": html_mod.unescape(re.sub(r"\s+", " ", summ.group(1))).strip()
                if summ
                else "",
            }
        )
    return out


def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace/punctuation for title comparison."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _content_words(text: str) -> set[str]:
    """Lowercase content words, stopwords and short tokens dropped."""
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def overlap(a: str, b: str) -> float:
    """Fraction of `a`'s content words also present in `b`."""
    wa = _content_words(a)
    if not wa:
        return 0.0
    return len(wa & _content_words(b)) / len(wa)


def resolve_title(title: str, desc: str) -> dict | None:
    """Resolve a publication title to its arXiv entry.

    Tries an exact-title query first; falls back to a relaxed all-fields
    query, accepted only when the best-scoring candidate clears
    `OVERLAP_THRESHOLD` against the caller's own description of the paper.
    Scores every candidate rather than just the top-ranked one -- confirmed
    live that arXiv's own relevance ranking is not reliable enough to trust
    position alone (docs/decisions.md D16).

    Args:
        title: The publication's title, as named by the lab's own page
            (site-specific suffixes already stripped by the caller).
        desc: A short description of the paper (e.g. a meta-description or
            abstract snippet), used only for disambiguation, never stored.

    Returns:
        The matched arXiv entry dict, tagged with `resolution`
        ("exact_title" or "relaxed_title"), or None if neither pass
        produced a trustworthy match.
    """
    exact = arxiv_query(f'ti:"{title}"', max_results=3)
    for e in exact:
        if _normalise(e["title"]) == _normalise(title):
            return dict(e, resolution="exact_title")
    if len(exact) == 1:
        # A single ti: hit that doesn't string-match exactly is still a
        # narrow field-scoped match (whitespace/punctuation only) -- trust it
        # rather than falling through to the noisier relaxed pass.
        return dict(exact[0], resolution="exact_title")

    relaxed = arxiv_query(f"all:{title}", max_results=5)
    if not relaxed:
        return None
    best = max(relaxed, key=lambda r: overlap(desc, r["summary"]))
    if overlap(desc, best["summary"]) >= OVERLAP_THRESHOLD:
        return dict(best, resolution="relaxed_title")
    return None
