"""Recover full OpenAI article text from the Internet Archive.

openai.com serves 403 to every automated request for article HTML -- plain
curl, browser user agents, a full browser header set, and Googlebot all fail.
Its `sitemap.xml` and `news/rss.xml` return 200, which is why the corpus exists
at all, but the RSS feed carries only a title and a one-sentence summary. The
median OpenAI article in the register is 205 characters against roughly 8,000
for an Anthropic one, and OpenAI is 79% of the corpus.

The Internet Archive has the pages Cloudflare will not serve. Measured on the
current register: 141 of 152 OpenAI articles are archived (93%), and the
recovered text runs 15x to 55x longer than the RSS summary.

Two things make this practical rather than a scrape of 152 URLs:

- One bulk CDX wildcard query returns every archived `openai.com/index*` URL in
  a date range, so finding snapshots costs a single request rather than 152.
- Snapshot fetches must be serial with backoff. At six concurrent requests the
  archive returned errors for 20 of 24 probes. This is a per-source rate limit,
  and it is the source's, not ours to tune away.

Snapshots are fetched with the `id_` modifier, which returns the originally
archived bytes rather than the archive's rewritten page -- no injected banner or
rewritten links. Those bytes carry the original Content-Encoding, so gzip has to
be handled here.
"""

from __future__ import annotations

import argparse
import gzip
import html
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from fetch_announcements import strip_chrome  # noqa: E402

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"
ARTICLES = DOCS / "announcements.json"
CACHE = DOCS / "wayback_cache"
INDEX = DOCS / "wayback_index.json"
CONFIG = ROOT / "config" / "sources.yaml"

UA = {"User-Agent": "bitcap-research neilaf4@gmail.com"}
CDX = "http://web.archive.org/cdx/search/cdx"
# The archive throttles aggressively; this is deliberately unhurried.
DELAY_SECONDS = 2.0
MAX_RETRIES = 4


def slug_of(url: str) -> str:
    """Normalise an openai.com URL to a comparable path.

    Args:
        url: Article URL.

    Returns:
        Lowercased path with scheme, host and trailing slash removed.
    """
    return re.sub(r"^https?://(www\.)?openai\.com/", "", url).strip("/").lower()


def get(url: str, timeout: int = 60) -> bytes:
    """Fetch a URL with retries and exponential backoff.

    Args:
        url: URL to fetch.
        timeout: Per-attempt timeout in seconds.

    Returns:
        Raw response body.

    Raises:
        RuntimeError: If every attempt fails.
    """
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=timeout
            ) as response:
                body = response.read()
                encoding = (response.headers.get("Content-Encoding") or "").lower()
                return decompress(body, encoding)
        except Exception as exc:  # noqa: BLE001 - network, throttling, malformed
            last = exc
            time.sleep(DELAY_SECONDS * (2**attempt))
    raise RuntimeError(f"{url}: {last}")


def decompress(body: bytes, encoding: str) -> bytes:
    """Decode a possibly-compressed archived response body.

    The header and the bytes disagree often enough that both are checked and
    neither is trusted: a page whose header says gzip can arrive as plain HTML.

    Args:
        body: Raw bytes.
        encoding: Declared Content-Encoding, possibly empty.

    Returns:
        Decompressed bytes, or the input unchanged if it is not compressed.
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


def clean(page: str) -> str:
    """Reduce archived HTML to readable text.

    Args:
        page: Raw HTML.

    Returns:
        Whitespace-collapsed text with entities decoded. Entities are unescaped
        twice: the stored corpus was found to contain literal `&amp;` because
        the original fetcher never decoded them, which made verbatim quote
        checking fail on tags that were in fact correct.
    """
    page = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", page)
    page = re.sub(r"(?is)<!--.*?-->", " ", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    return strip_chrome(html.unescape(html.unescape(page)))


def build_index(since: str, until: str) -> dict[str, str]:
    """Find every archived openai.com article URL in a window, in one request.

    Args:
        since: Inclusive start, YYYYMMDD.
        until: Inclusive end, YYYYMMDD.

    Returns:
        Mapping of normalised slug -> snapshot timestamp.
    """
    query = (
        f"{CDX}?url=openai.com/index*&output=json&filter=statuscode:200"
        f"&from={since}&to={until}&collapse=urlkey&fl=original,timestamp&limit=6000"
    )
    rows = json.loads(get(query, timeout=180) or b"[]")
    index: dict[str, str] = {}
    for original, timestamp in rows[1:]:
        # collapse=urlkey keeps the first row per URL; prefer the latest snapshot.
        key = slug_of(original)
        if key not in index or timestamp > index[key]:
            index[key] = timestamp
    return index


def recover(article: dict, timestamp: str) -> tuple[str, str] | None:
    """Fetch one article's archived text, using a disk cache.

    Args:
        article: Article record from announcements.json.
        timestamp: Wayback snapshot timestamp.

    Returns:
        Tuple of (text, snapshot url), or None if the snapshot is unusable.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9]+", "_", article["url"])[:140] + ".json"
    cached = CACHE / key
    if cached.exists():
        blob = json.loads(cached.read_text())
        return blob["text"], blob["snapshot"]

    snapshot = f"http://web.archive.org/web/{timestamp}id_/{article['url']}"
    text = clean(get(snapshot).decode("utf-8", "ignore"))

    # A snapshot can be a redirect stub or an error page. Anything shorter than
    # the RSS summary we already hold is not an improvement.
    if len(text) <= len(article["text"]):
        return None
    cached.write_text(json.dumps({"text": text, "snapshot": snapshot}))
    return text, snapshot


def main() -> None:
    """Backfill archived full text into the register."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="20260525", help="CDX window start, YYYYMMDD")
    parser.add_argument("--until", default="20260901", help="CDX window end, YYYYMMDD")
    parser.add_argument("--limit", type=int, help="stop after N recoveries")
    parser.add_argument(
        "--dry-run", action="store_true", help="report coverage, write nothing"
    )
    args = parser.parse_args()

    articles = json.loads(ARTICLES.read_text())
    targets = [
        a for a in articles
        if a["lab"] == "openai" and a["text_source"] == "rss_summary"
    ]
    print(f"{len(targets)} OpenAI articles still on RSS summaries")

    index = build_index(args.since, args.until)
    INDEX.write_text(json.dumps(index, indent=2))
    found = [a for a in targets if slug_of(a["url"]) in index]
    print(f"{len(index)} archived openai.com URLs in window; "
          f"{len(found)} of our {len(targets)} are archived "
          f"({100 * len(found) / max(1, len(targets)):.0f}%)")

    if args.dry_run:
        print("\ndry run: nothing written")
        for a in [x for x in targets if slug_of(x["url"]) not in index][:10]:
            print(f"   NOT ARCHIVED  {a['date']}  {a['title'][:56]}")
        return

    by_url = {a["url"]: a for a in articles}
    recovered, skipped, failed = 0, 0, []
    gained = 0
    for i, article in enumerate(found, 1):
        if args.limit and recovered >= args.limit:
            break
        try:
            got = recover(article, index[slug_of(article["url"])])
        except Exception as exc:  # noqa: BLE001
            failed.append((article["url"], str(exc)))
            continue
        if got is None:
            skipped += 1
            continue
        text, snapshot = got
        before = len(by_url[article["url"]]["text"])
        by_url[article["url"]]["text"] = text
        by_url[article["url"]]["text_source"] = "full_text_archived"
        by_url[article["url"]]["archive_snapshot"] = snapshot
        gained += len(text) - before
        recovered += 1
        if i % 20 == 0:
            print(f"   {i}/{len(found)} … {recovered} recovered")
        time.sleep(DELAY_SECONDS)

    ARTICLES.write_text(json.dumps(articles, indent=2))
    print(f"\nrecovered   : {recovered}")
    print(f"no better   : {skipped}")
    print(f"failed      : {len(failed)}")
    print(f"text gained : {gained:,} characters")
    for url, err in failed[:5]:
        print(f"   FAIL {url}: {err[:80]}")


if __name__ == "__main__":
    main()
