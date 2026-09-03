"""Fetch lab announcements published within the configured window.

Sources are declared in config/sources.yaml; this module implements the
discovery methods those entries name (``sitemap``, ``rss``,
``listing_pagination``, ``wayback_cdx``) and refuses an unknown one rather
than guessing. Every fetched page is cached on disk so re-runs are
idempotent and cost no requests.

Dates are the awkward part and are handled per source: a sitemap ``lastmod`` is
a modification date and is only ever used as a coarse prefilter, with the real
publication date read from the page or the slug.
"""

from __future__ import annotations

import gzip
import io
import json
import html
import re
import sys
import time
import urllib.parse
import zlib
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

# Anthropic prints the article's own date in full ("Date October 16, 2025") and
# the "Related posts" footer in abbreviated form ("Aug 28, 2026"). Matching only
# abbreviations therefore skipped the real date and took a footer link's date
# instead -- see date_from_page. `Oct` must not match the first three letters of
# `October`, so the remainder of each full name is an optional group.
_MONTH_TAIL = {
    "Jan": "uary", "Feb": "ruary", "Mar": "ch", "Apr": "il", "May": "",
    "Jun": "e", "Jul": "y", "Aug": "ust", "Sep": "t?(?:ember)?", "Oct": "ober",
    "Nov": "ember", "Dec": "ember",
}
MONTH_PATTERN = "|".join(
    f"({abbr})(?:{tail})?" if tail else f"({abbr})" for abbr, tail in _MONTH_TAIL.items()
)


def fetch(url: str, retries: int = 3, user_agent: str | None = UA) -> str:
    """Fetch a URL as text, with backoff, caching the result on disk.

    Args:
        url: Absolute URL.
        retries: Attempts before giving up.
        user_agent: UA string to send. Defaults to the module's browser-style
            `UA`, which every existing source expects. Pass `None` or `""`
            for a source that blocks browser-style UAs specifically --
            confirmed live for `ai.meta.com`: a plain curl with no custom UA
            gets 200, any browser-style UA gets 400, the opposite of every
            other lab configured so far. An empty headers dict lets urllib
            send its own default (`Python-urllib/x.y`, not browser-shaped),
            which was verified live to also get 200 there.

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

    headers = {"User-Agent": user_agent} if user_agent else {}
    for attempt in range(retries):
        try:
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=45) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            cached.write_text(body)
            return body
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"fetch failed: {url}: {exc}") from exc
            time.sleep(2**attempt)
    raise RuntimeError(f"fetch failed: {url}")


# Used by from_listing_pagination: the <title> tag is a reliable title
# source across every lab tried so far, unlike splitting the stripped body
# text on a separator that may not exist on a given site. `[^>]*` after
# `title` is load-bearing, not decorative -- caught live: Meta's pages use
# `<title id="pageTitle">`, and an exact `<title>` match silently found
# nothing on every single article, which is a worse failure than the bug it
# replaced (an empty title beats a wrong one, but neither is acceptable).
TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

# Site furniture that survives tag stripping. It is not content, it costs input
# tokens on every call, and it breaks quoting: a model that reads across
# "(opens in a new window)" -- correctly, since the prompt tells it to ignore
# furniture -- produces a quote that is not a substring of the stored text.
# 3,603 occurrences across 141 articles when this was added.
CHROME = re.compile(
    r"\s*(?:\u2060\s*)?\(opens in a new window\)"
    r"|\s*Skip to main content"
    r"|\s*Skip to footer"
    r"|\s*Loading\u2026",
    re.I,
)


def strip_chrome(text: str) -> str:
    """Remove site furniture from already-extracted text.

    Args:
        text: Extracted page text.

    Returns:
        The text with navigation and link chrome removed.
    """
    return " ".join(CHROME.sub(" ", text).split())


def strip_html(page: str) -> str:
    """Reduce an HTML page to readable text.

    Entities are unescaped twice. Anthropic's pages carry double-encoded
    apostrophes, so a single pass leaves `&#x27;` in the stored text. That is
    not cosmetic: the model reads the raw text and quotes the decoded form, so
    an undecoded corpus makes verbatim quote checking report hallucinations that
    did not happen -- which it did, on 9 of 90 tags, before this was fixed.

    Args:
        page: Raw HTML.

    Returns:
        Whitespace-collapsed text with script, style and chrome removed and
        HTML entities decoded.
    """
    body = re.sub(r"(?s)<(script|style|noscript|svg).*?</\1>", " ", page)
    body = re.sub(r"(?s)<!--.*?-->", " ", body)
    text = re.sub(r"<[^>]+>", " ", body)
    return strip_chrome(html.unescape(html.unescape(text)))


def date_from_page(text: str) -> str | None:
    """Read a publication date printed in the page body.

    Anthropic prints the date next to the headline rather than exposing it in
    metadata, so the first "Month D, YYYY" in the text is taken.

    Both full and abbreviated month names are accepted. Accepting only
    abbreviations silently mis-dated pages that print the full name: the header
    date was skipped and the first date in the "Related posts" footer was taken
    instead, which put a ten-month-old article inside a three-month window.

    Args:
        text: Stripped page text.

    Returns:
        ISO date string, or None if no date is present.
    """
    m = re.search(rf"\b(?:{MONTH_PATTERN})\s+(\d{{1,2}}),\s+(20\d{{2}})\b", text)
    if not m:
        return None
    abbr = next(g for g in m.groups()[:12] if g)
    day, year = m.groups()[12], m.groups()[13]
    return f"{year}-{MONTHS[abbr]:02d}-{int(day):02d}"


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

    The feed's title and summary are always the fallback text. When a source
    is configured `text_source: full_text` and its article pages are actually
    reachable (unlike OpenAI's, which are not -- see the `openai` entry's
    notes), each item's linked page is fetched and stripped for the real
    article text instead. A per-item fetch failure downgrades that one item to
    `rss_summary` rather than raising: `text_source` must always say what was
    actually read, never what the config asked for, so a downstream score is
    never silently computed from a two-line summary while believing it read
    the full page.

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
        url = field("link")
        text, text_source = f"{title}. {summary}", "rss_summary"

        if lab.get("text_source") == "full_text" and url:
            try:
                text = strip_html(fetch(url))
                text_source = "full_text"
            except RuntimeError as exc:
                print(f"    SKIP full text, using summary for {url}: {exc}", flush=True)

        out.append(
            {
                "lab": lab["id"],
                "url": url,
                "date": when.strftime("%Y-%m-%d"),
                "title": title,
                "text": text,
                "text_source": text_source,
                "feed_category": field("category"),
            }
        )
    return out


def from_listing_pagination(lab: dict, cutoff: datetime) -> list[dict]:
    """Discover in-window articles by paginating an HTML listing page.

    Used where neither a sitemap nor an RSS feed is reachable -- confirmed
    live for `ai.meta.com`: its sitemap is gated to named crawler
    user-agents only (403 for everyone else, robots.txt names
    `facebookexternalhit`/`meta-externalads` explicitly), and no RSS feed
    exists at any path tried. The listing page itself paginates via a
    `?page=N` query parameter and is plain server-rendered HTML.

    Pagination stops when a page contributes no new in-window article (the
    listing is assumed reverse-chronological, the normal blog convention),
    or after `max_pages` regardless -- a listing page changing shape or
    ordering silently must not turn into an unbounded fetch loop.

    Args:
        lab: Lab entry from sources.yaml. Must carry `page_param` (the query
            parameter name); may carry `user_agent` (see `fetch` -- Meta
            specifically needs this cleared, the opposite of every other
            lab) and `max_pages` (default 20).

    Returns:
        List of article dicts.
    """
    user_agent = lab.get("user_agent") or None
    max_pages = lab.get("max_pages", 20)
    seen_urls: set[str] = set()
    out = []

    for page in range(1, max_pages + 1):
        url = (
            lab["index_url"]
            if page == 1
            else f"{lab['index_url']}?{lab['page_param']}={page}"
        )
        try:
            listing_html = fetch(url, user_agent=user_agent)
        except RuntimeError as exc:
            print(f"    SKIP listing page {page}: {exc}", flush=True)
            break

        hrefs = re.findall(r'href="([^"]+)"', listing_html)
        candidates = sorted({
            urllib.parse.urljoin(lab["index_url"], h)
            for h in hrefs
            if lab["url_contains"] in h and "?" not in h
        })
        # The listing page links to itself (nav/logo/home links) and that
        # self-link trivially contains url_contains, so it must be excluded
        # explicitly -- caught live: the bare index URL was scraped as an
        # "article" with a date lifted from its own featured-item text.
        candidates = [c for c in candidates if c != lab["index_url"]]
        new_candidates = [c for c in candidates if c not in seen_urls]
        if not new_candidates:
            break
        seen_urls.update(new_candidates)

        found_in_window = False
        for article_url in new_candidates:
            try:
                article_html = fetch(article_url, user_agent=user_agent)
            except RuntimeError as exc:
                print(f"    SKIP {article_url}: {exc}", flush=True)
                continue
            text = strip_html(article_html)

            date = date_from_page(text)
            if not date or date < cutoff.strftime("%Y-%m-%d"):
                continue
            found_in_window = True

            # Body-text splitting on a separator (" \ ", " | ") assumes a
            # separator that doesn't exist in this site's stripped text --
            # caught live, titles came back with the full nav chrome
            # appended. The <title> tag is reliable across every lab tried
            # so far; DeepMind hit the same bug for the same reason.
            title_match = TITLE_TAG.search(article_html)
            title = html.unescape(title_match.group(1)).strip()[:200] if title_match else ""
            out.append(
                {
                    "lab": lab["id"],
                    "url": article_url,
                    "date": date,
                    "title": title,
                    "text": text[:24000],
                    "text_source": lab["text_source"],
                    "feed_category": None,
                }
            )

        if not found_in_window:
            break

    return out


WAYBACK_CDX = "http://web.archive.org/cdx/search/cdx"
WAYBACK_UA = {"User-Agent": "bitcap-research neilaf4@gmail.com"}
# The archive throttles aggressively -- confirmed in backfill_openai.py, whose
# own docstring records 20 of 24 probes failing at six concurrent requests.
# This loop is already serial; the pause is the only lever left.
WAYBACK_DELAY_SECONDS = 2.0
# arXiv-style JSON-LD is the reliable date source here, confirmed live on six
# real archived x.ai pages spanning 2023-2026 -- the visible-text date next to
# the headline (`date_from_page`) is a fallback for pages that lack it.
JSONLD_DATE_PUBLISHED = re.compile(r'"datePublished":\s*"(\d{4}-\d{2}-\d{2})')
# The live site's <title> carries a site suffix that varies with whatever the
# site's own branding was at crawl time, not at publish time -- confirmed
# live: a 2024-dated funding-round page crawled in August 2026 still renders
# "SpaceXAI" (the current brand), not "xAI" (what it said when published).
# Both variants are stripped regardless of the article's own age.
SITE_SUFFIX_XAI = re.compile(r"\s*\|\s*(?:SpaceXAI|xAI)\s*$", re.I)


def _wayback_decompress(body: bytes, encoding: str) -> bytes:
    """Decode a possibly-compressed archived response body.

    The header and the bytes disagree often enough that both are checked and
    neither is trusted alone -- same finding backfill_openai.py made for
    openai.com snapshots, confirmed independently true for x.ai's here.

    Args:
        body: Raw bytes.
        encoding: Declared Content-Encoding, possibly empty.

    Returns:
        Decompressed bytes, or the input unchanged if not compressed.
    """
    if encoding == "gzip" or body[:2] == b"\x1f\x8b":
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(body)).read()
        except OSError:
            return body
    if encoding == "deflate":
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                return zlib.decompress(body, wbits)
            except zlib.error:
                continue
    return body


def fetch_wayback(url: str, retries: int = 4) -> str:
    """Fetch an archive.org URL as text, decompressing and caching to disk.

    A separate function from `fetch()`, not a parameterised branch of it:
    archive.org snapshots need gzip/deflate handling `fetch()` has never
    needed for a live site, and a slower, dedicated backoff pace for a host
    that throttles hard.

    Args:
        url: Absolute archive.org URL (typically an `id_`-suffixed snapshot
            or a `cdx/search/cdx` query).
        retries: Attempts before giving up.

    Returns:
        Decoded response body.

    Raises:
        RuntimeError: If every attempt fails.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    key = "wb_" + re.sub(r"[^a-zA-Z0-9]+", "_", url)[:140]
    cached = CACHE / f"{key}.html"
    if cached.exists():
        return cached.read_text(errors="replace")

    for attempt in range(retries):
        try:
            req = request.Request(url, headers=WAYBACK_UA)
            with request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                encoding = (resp.headers.get("Content-Encoding") or "").lower()
            body = _wayback_decompress(raw, encoding).decode("utf-8", errors="replace")
            cached.write_text(body)
            time.sleep(WAYBACK_DELAY_SECONDS)
            return body
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"wayback fetch failed: {url}: {exc}") from exc
            time.sleep(WAYBACK_DELAY_SECONDS * (2**attempt))
    raise RuntimeError(f"wayback fetch failed: {url}")


def from_wayback_cdx(lab: dict, cutoff: datetime) -> list[dict]:
    """Discover in-window articles via the Internet Archive, bypassing the live site.

    Built for xAI: `x.ai` is fully Cloudflare-blocked on every live path,
    including `sitemap.xml` itself, confirmed on repeated checks across this
    project. The originally planned approach -- fetch the sitemap's own
    archived copy -- turned out not to work either: confirmed live that
    `x.ai/sitemap.xml` has not been re-archived since February 2026, so its
    URL list would miss every article from the real 3-month window entirely.

    Instead, this queries the Internet Archive's CDX API directly for every
    archived URL matching the lab's article path (one bulk wildcard request,
    same technique `backfill_openai.py` already uses for text recovery, here
    used for discovery itself). A snapshot's *crawl* date has no relationship
    to the article's *publish* date -- confirmed live: an August 2026 crawl
    of a 2024-dated funding-round page is completely ordinary, since Wayback
    recrawls existing pages on its own schedule regardless of content age.
    So every candidate is fetched and dated from its own JSON-LD
    `datePublished` (falling back to the visible text date), never from the
    CDX timestamp.

    Args:
        lab: Lab entry from sources.yaml. Must carry `index_url` (used to
            derive both the CDX URL pattern and to exclude the bare listing
            page) and `url_contains` (the article path filter, e.g.
            `/news/`).

    Returns:
        List of article dicts, `text_source: "full_text_archived"`, each
        carrying `archive_snapshot` alongside the live `url` -- the citation
        resolves through the snapshot even while the live site blocks
        automated fetches; a human following the link still reaches the
        real page.
    """
    netloc_and_path = lab["index_url"].split("://", 1)[-1]
    # Bounded to reduce wasted snapshot fetches, not for correctness: an
    # unbounded query on x.ai/news* returns ~2,900 rows going back to 2023,
    # and every distinct URL's snapshot gets fetched regardless of the
    # bound since dating happens from the page's own content, not the CDX
    # timestamp -- fetching years of evergreen pages just to discard nearly
    # all of them wastes real time at 2s/request. A 60-day buffer before
    # cutoff covers archive lag (a page can be recrawled well after
    # publication) without pulling the whole history.
    cdx_from = (cutoff - timedelta(days=60)).strftime("%Y%m%d")
    query = (
        f"{WAYBACK_CDX}?url={urllib.parse.quote(netloc_and_path + '*')}"
        f"&output=json&filter=statuscode:200&from={cdx_from}"
        "&fl=original,timestamp&limit=5000"
    )
    rows = json.loads(fetch_wayback(query))

    latest_snapshot: dict[str, str] = {}
    for original, timestamp in rows[1:]:
        clean_url = original.split("?", 1)[0]
        if clean_url == lab["index_url"] or lab["url_contains"] not in clean_url:
            continue
        if re.search(r"\.(png|jpe?g|svg|ico|webp)$", clean_url, re.I):
            continue
        if clean_url not in latest_snapshot or timestamp > latest_snapshot[clean_url]:
            latest_snapshot[clean_url] = timestamp

    out = []
    for url, timestamp in latest_snapshot.items():
        snapshot_url = f"http://web.archive.org/web/{timestamp}id_/{url}"
        try:
            page = fetch_wayback(snapshot_url)
        except RuntimeError as exc:
            print(f"    SKIP {url}: {exc}", flush=True)
            continue

        date_match = JSONLD_DATE_PUBLISHED.search(page)
        text = strip_html(page)
        date = date_match.group(1) if date_match else date_from_page(text)
        if not date or date < cutoff.strftime("%Y-%m-%d"):
            continue

        title_match = TITLE_TAG.search(page)
        title = html.unescape(title_match.group(1)).strip() if title_match else ""
        title = SITE_SUFFIX_XAI.sub("", title)[:200]

        out.append(
            {
                "lab": lab["id"],
                "url": url,
                "date": date,
                "title": title,
                "text": text[:24000],
                "text_source": "full_text_archived",
                "feed_category": None,
                "archive_snapshot": snapshot_url,
            }
        )
    return out


METHODS = {
    "sitemap": from_sitemap,
    "rss": from_rss,
    "listing_pagination": from_listing_pagination,
    "wayback_cdx": from_wayback_cdx,
}


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
