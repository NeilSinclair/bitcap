"""Fetch lab announcements published within the configured window.

Sources are declared in config/sources.yaml; this module implements the two
discovery methods those entries name (``sitemap`` and ``rss``) and refuses an
unknown one rather than guessing. Every fetched page is cached on disk so
re-runs are idempotent and cost no requests.

Dates are the awkward part and are handled per source: a sitemap ``lastmod`` is
a modification date and is only ever used as a coarse prefilter, with the real
publication date read from the page or the slug.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request

import yaml

ROOT = Path(__file__).parent.parent.parent
CONFIG = ROOT / "config" / "sources.yaml"
CACHE = ROOT / "research" / "docs" / "announcement_cache"
OUT = ROOT / "research" / "docs" / "announcements.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

MONTHS = {
    m: i
    for i, m in enumerate(
        "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1
    )
}


def fetch(url: str, retries: int = 3) -> str:
    """Fetch a URL as text, with backoff, caching the result on disk.

    Args:
        url: Absolute URL.
        retries: Attempts before giving up.

    Returns:
        Response body as text.

    Raises:
        RuntimeError: If every attempt fails. Callers decide whether one dead
            source aborts the run.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:140]
    cached = CACHE / f"{key}.html"
    if cached.exists():
        return cached.read_text(errors="replace")

    for attempt in range(retries):
        try:
            req = request.Request(url, headers={"User-Agent": UA})
            with request.urlopen(req, timeout=45) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            cached.write_text(body)
            return body
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"fetch failed: {url}: {exc}") from exc
            time.sleep(2**attempt)
    raise RuntimeError(f"fetch failed: {url}")


def strip_html(page: str) -> str:
    """Reduce an HTML page to readable text.

    Args:
        page: Raw HTML.

    Returns:
        Whitespace-collapsed text with script, style and chrome removed.
    """
    body = re.sub(r"(?s)<(script|style|noscript|svg).*?</\1>", " ", page)
    return " ".join(re.sub(r"<[^>]+>", " ", body).split())


def date_from_page(text: str) -> str | None:
    """Read a publication date printed in the page body.

    Anthropic prints the date next to the headline rather than exposing it in
    metadata, so the first "Mon D, YYYY" in the text is taken.

    Args:
        text: Stripped page text.

    Returns:
        ISO date string, or None if no date is present.
    """
    m = re.search(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s+(20\d{2})\b", text)
    if not m:
        return None
    return f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


def date_from_slug(url: str) -> str | None:
    """Derive a date from a DeepSeek news slug.

    Slugs are ``newsYYMMDD`` from 2025 onward and ``newsMMDD`` before that.
    Only the six-digit form is dated confidently; the short form is treated as
    undated rather than guessed at, since guessing a year would silently place
    an old release inside the window.

    Args:
        url: Article URL.

    Returns:
        ISO date string, or None.
    """
    m = re.search(r"/news(\d{6})$", url)
    if not m:
        return None
    yy, mm, dd = m.group(1)[:2], m.group(1)[2:4], m.group(1)[4:]
    return f"20{yy}-{mm}-{dd}"


def from_sitemap(lab: dict, cutoff: datetime) -> list[dict]:
    """Discover in-window articles from a sitemap.

    Args:
        lab: Lab entry from sources.yaml.
        cutoff: Earliest publication date to keep.

    Returns:
        List of article dicts with url, date, title and text.
    """
    xml = fetch(lab["index_url"])
    entries = re.findall(r"<url>(.*?)</url>", xml, re.S)

    candidates = []
    for entry in entries:
        loc = re.search(r"<loc>(.*?)</loc>", entry)
        if not loc or lab["url_contains"] not in loc.group(1):
            continue
        url = loc.group(1).rstrip("/")
        lastmod = re.search(r"<lastmod>(\d{4}-\d{2}-\d{2})", entry)

        if lab["date_from"] == "slug":
            date = date_from_slug(url)
            if date and date >= cutoff.strftime("%Y-%m-%d"):
                candidates.append((url, date))
            continue

        # lastmod is a modification date, so it can only exclude, never date.
        if lastmod and lastmod.group(1) < cutoff.strftime("%Y-%m-%d"):
            continue
        candidates.append((url, None))

    out = []
    for url, known_date in candidates:
        try:
            text = strip_html(fetch(url))
        except RuntimeError as exc:
            print(f"    SKIP {url}: {exc}", flush=True)
            continue

        date = known_date or date_from_page(text)
        if not date or date < cutoff.strftime("%Y-%m-%d"):
            continue

        title = text.split(" \\ ")[0].split(" | ")[0].strip()[:200]
        out.append(
            {
                "lab": lab["id"],
                "url": url,
                "date": date,
                "title": title,
                "text": text[:24000],
                "text_source": lab["text_source"],
                "feed_category": None,
            }
        )
    return out


def from_rss(lab: dict, cutoff: datetime) -> list[dict]:
    """Discover in-window articles from an RSS feed.

    Used where the site itself blocks automated fetching, so the feed's title
    and summary are the only text available.

    Args:
        lab: Lab entry from sources.yaml.
        cutoff: Earliest publication date to keep.

    Returns:
        List of article dicts.
    """
    xml = fetch(lab["index_url"])
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):

        def field(name):
            m = re.search(
                rf"<{name}>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</{name}>",
                item,
                re.S,
            )
            return m.group(1).strip() if m else None

        pub = field("pubDate")
        if not pub:
            continue
        try:
            when = datetime.strptime(pub[:16].strip(), "%a, %d %b %Y")
        except ValueError:
            continue
        if when < cutoff.replace(tzinfo=None):
            continue

        title = field("title") or ""
        summary = field("description") or ""
        out.append(
            {
                "lab": lab["id"],
                "url": field("link"),
                "date": when.strftime("%Y-%m-%d"),
                "title": title,
                "text": f"{title}. {summary}",
                "text_source": lab["text_source"],
                "feed_category": field("category"),
            }
        )
    return out


METHODS = {"sitemap": from_sitemap, "rss": from_rss}


def collect() -> list[dict]:
    """Fetch every configured lab's in-window announcements.

    Returns:
        All articles, newest first.
    """
    config = yaml.safe_load(CONFIG.read_text())
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=config["window_months"] * 30.5
    )
    print(f"window: {cutoff:%Y-%m-%d} onward\n")

    articles = []
    for lab in config["labs"]:
        method = METHODS.get(lab["method"])
        if not method:
            sys.exit(f"{lab['id']}: unknown method {lab['method']!r}")
        found = method(lab, cutoff)
        print(f"{lab['label']:<12} {len(found)} announcements")
        articles.extend(found)

    articles.sort(key=lambda a: a["date"], reverse=True)
    return articles


if __name__ == "__main__":
    found = collect()
    OUT.write_text(json.dumps(found, indent=2))
    print(f"\n{len(found)} total -> {OUT.name}")
