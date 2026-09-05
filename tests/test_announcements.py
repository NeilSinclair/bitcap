"""Tests for announcement fetching and scoring.

The silent failures this suite exists to catch: a hallucinated mechanism id
reaching the register as though it were a real transmission path, a scoring
rule that quietly stops distinguishing signal from noise, and a date parser
that places an old release inside the window.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "announcements"))

import fetch_announcements
from fetch_announcements import (
    date_from_page,
    date_from_slug,
    from_listing_pagination,
    from_rss,
    from_discourse,
    from_model_index,
    from_wayback_cdx,
    strip_html,
)


@pytest.fixture(scope="module")
def rules():
    return yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())


@pytest.fixture(scope="module")
def deepmind():
    config = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
    return next(l for l in config["labs"] if l["id"] == "google-deepmind")


@pytest.fixture(scope="module")
def register():
    return json.loads((ROOT / "research" / "docs" / "announcements.json").read_text())


def score_of(result, rules):
    """Import lazily: score_announcements imports the anthropic SDK."""
    from score_announcements import score_of as fn

    return fn(result, rules)


def mech(magnitude="high", confidence="high", mid="training_compute_up"):  # noqa: D103
    return {
        "id": mid,
        "sign": "positive",
        "magnitude": magnitude,
        "confidence": confidence,
        "reason": "r",
        "quote": "q",
    }


def result(event="frontier_model_release", signal=True, mechanisms=None):
    return {
        "is_signal": signal,
        "event_type": event,
        "mechanisms": mechanisms if mechanisms is not None else [mech()],
        "categories": [],
    }


class TestDateFromSlug:
    def test_six_digit_slug(self):
        assert date_from_slug("https://x/news/news260821") == "2026-08-21"

    def test_short_slug_is_undated_not_guessed(self):
        """A four-digit slug has no year; guessing one could place a 2024
        release inside a 2026 window."""
        assert date_from_slug("https://x/news/news0725") is None

    def test_non_news_url(self):
        assert date_from_slug("https://x/docs/intro") is None


class TestDateFromPage:
    def test_reads_printed_date(self):
        assert date_from_page("Introducing Claude Opus 5 Jul 24, 2026 It is") == "2026-07-24"

    def test_single_digit_day_padded(self):
        assert date_from_page("Posted Mar 3, 2026 today") == "2026-03-03"

    def test_absent_date(self):
        assert date_from_page("no date here at all") is None

    def test_ignores_non_date_numbers(self):
        assert date_from_page("We trained on 24, 2026 GPUs") is None

    def test_full_month_names_are_read(self):
        """Anthropic prints the article's own date in full and the "Related
        posts" footer in abbreviated form. Matching only abbreviations skipped
        the real date and took a footer link's, which put a ten-month-old
        article inside a three-month window -- 316 days off, in the gold set.
        """
        page = (
            "Introducing Agent Skills Category Product announcements "
            "Date October 16, 2025 Reading time 5 min "
            "... Related posts Aug 28, 2026 Claude for Teachers"
        )
        assert date_from_page(page) == "2025-10-16"

    def test_abbreviation_does_not_partially_match_a_full_name(self):
        """`Oct` matching the first three letters of `October` would leave the
        `\\s+` to match `ober`, silently failing the whole date."""
        for text, expected in [
            ("March 2, 2026", "2026-03-02"),
            ("September 3, 2026", "2026-09-03"),
            ("Sept 3, 2026", "2026-09-03"),
            ("Sep 3, 2026", "2026-09-03"),
            ("February 11, 2026", "2026-02-11"),
        ]:
            assert date_from_page(text) == expected, text

    def test_the_first_date_still_wins(self):
        """The header date precedes the footer; order is what makes this work."""
        assert date_from_page("June 1, 2026 body Aug 28, 2026 related") == "2026-06-01"


class TestStripHtml:
    def test_removes_script_and_style(self):
        out = strip_html("<p>keep</p><script>var x=1</script><style>a{}</style>")
        assert "keep" in out and "var x" not in out and "a{}" not in out

    def test_collapses_whitespace(self):
        assert strip_html("<p>a</p>\n\n   <p>b</p>") == "a b"


def _rss_xml(title="Example headline", summary="A short summary.", link="https://x.example/a"):
    return f"""<rss><channel><item>
        <title>{title}</title>
        <description>{summary}</description>
        <link>{link}</link>
        <pubDate>Mon, 01 Sep 2025 00:00:00 GMT</pubDate>
    </item></channel></rss>"""


class TestFromRssTextSource:
    """The silent failure this suite exists to catch: `text_source` in the
    output claims a page was fully read when the RSS path actually only ever
    saw the feed's own title+summary (or the reverse) -- a downstream score
    then can't be trusted to mean what its `text_source` field says it means.
    """

    def test_rss_summary_never_fetches_the_article_page(self, monkeypatch):
        calls = {"index": 0, "article": 0}

        def fake_fetch(url, **kw):
            calls["index" if url == "https://feed.example/rss" else "article"] += 1
            return _rss_xml()

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        lab = {"id": "openai", "index_url": "https://feed.example/rss", "text_source": "rss_summary"}
        out = from_rss(lab, datetime(2025, 1, 1))

        assert calls["article"] == 0
        assert out[0]["text_source"] == "rss_summary"
        assert out[0]["text"] == "Example headline. A short summary."

    def test_full_text_fetches_and_strips_the_article_page(self, monkeypatch):
        def fake_fetch(url, **kw):
            if url == "https://feed.example/rss":
                return _rss_xml(link="https://x.example/a")
            assert url == "https://x.example/a"
            return "<html><body><p>The real article body.</p></body></html>"

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        lab = {"id": "google-deepmind", "index_url": "https://feed.example/rss", "text_source": "full_text"}
        out = from_rss(lab, datetime(2025, 1, 1))

        assert out[0]["text_source"] == "full_text"
        assert out[0]["text"] == "The real article body."

    def test_full_text_falls_back_to_summary_on_fetch_failure(self, monkeypatch, capsys):
        # A source declared full_text but temporarily unreachable must not
        # crash the whole run, and must not silently mislabel the fallback
        # text as full_text either.
        def fake_fetch(url, **kw):
            if url == "https://feed.example/rss":
                return _rss_xml()
            raise RuntimeError("fetch failed: 500")

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        lab = {"id": "mistral", "index_url": "https://feed.example/rss", "text_source": "full_text"}
        out = from_rss(lab, datetime(2025, 1, 1))

        assert out[0]["text_source"] == "rss_summary"
        assert out[0]["text"] == "Example headline. A short summary."
        assert "SKIP full text" in capsys.readouterr().out


def _listing_html(*slugs):
    links = "".join(f'<a href="https://ai.example/blog/{s}/">{s}</a>' for s in slugs)
    return f"<html><body>{links}</body></html>"


def _article_html(date_text, title="An Article Title"):
    return (
        f"<html><title>{title}</title><body>Some article body. "
        f"{date_text} More body text.</body></html>"
    )


class TestFromListingPagination:
    """The silent failure this suite exists to catch: a listing page that
    changes shape or stops paginating cleanly turns into either an
    unbounded fetch loop, or a run that silently stops one page early and
    never notices it missed real in-window articles.
    """

    def _lab(self, **overrides):
        lab = {
            "id": "meta-ai",
            "index_url": "https://ai.example/blog/",
            "page_param": "page",
            "url_contains": "/blog/",
            "text_source": "full_text",
        }
        lab.update(overrides)
        return lab

    def test_single_page_all_in_window(self, monkeypatch):
        pages = {
            "https://ai.example/blog/": _listing_html("a", "b"),
            "https://ai.example/blog/a/": _article_html("September 1, 2026"),
            "https://ai.example/blog/b/": _article_html("August 20, 2026"),
            "https://ai.example/blog/?page=2": _listing_html(),  # empty -> stop
        }
        monkeypatch.setattr(
            "fetch_announcements.fetch", lambda url, user_agent=None, **kw: pages[url]
        )
        out = from_listing_pagination(self._lab(), datetime(2026, 1, 1))
        assert {a["url"] for a in out} == {
            "https://ai.example/blog/a/",
            "https://ai.example/blog/b/",
        }

    def test_stops_when_a_page_is_entirely_out_of_window(self, monkeypatch):
        pages = {
            "https://ai.example/blog/": _listing_html("new"),
            "https://ai.example/blog/new/": _article_html("September 1, 2026"),
            "https://ai.example/blog/?page=2": _listing_html("old"),
            "https://ai.example/blog/old/": _article_html("January 1, 2020"),
        }
        monkeypatch.setattr(
            "fetch_announcements.fetch", lambda url, user_agent=None, **kw: pages[url]
        )
        out = from_listing_pagination(self._lab(), datetime(2026, 1, 1))
        assert [a["url"] for a in out] == ["https://ai.example/blog/new/"]

    def test_listing_index_url_is_excluded_from_its_own_candidates(self, monkeypatch):
        # Caught live: the listing page links to itself (nav/logo/home
        # link), which trivially contains url_contains -- it was scraped as
        # an "article" with a date lifted from its own featured-item text.
        html = (
            '<html><body><a href="https://ai.example/blog/">Home</a>'
            '<a href="https://ai.example/blog/real/">real</a></body></html>'
        )
        pages = {
            "https://ai.example/blog/": html,
            "https://ai.example/blog/real/": _article_html("September 1, 2026"),
            "https://ai.example/blog/?page=2": _listing_html(),
        }
        monkeypatch.setattr(
            "fetch_announcements.fetch", lambda url, user_agent=None, **kw: pages[url]
        )
        out = from_listing_pagination(self._lab(), datetime(2026, 1, 1))
        assert [a["url"] for a in out] == ["https://ai.example/blog/real/"]

    def test_title_read_from_title_tag_not_body_text_split(self, monkeypatch):
        pages = {
            "https://ai.example/blog/": _listing_html("real"),
            "https://ai.example/blog/real/": _article_html(
                "September 1, 2026", title="The Real Headline"
            ),
            "https://ai.example/blog/?page=2": _listing_html(),
        }
        monkeypatch.setattr(
            "fetch_announcements.fetch", lambda url, user_agent=None, **kw: pages[url]
        )
        out = from_listing_pagination(self._lab(), datetime(2026, 1, 1))
        assert out[0]["title"] == "The Real Headline"

    def test_title_tag_with_attributes_still_matches(self, monkeypatch):
        # Confirmed live: Meta AI's real pages use <title id="pageTitle">,
        # not a bare <title> -- an exact-match regex silently found nothing
        # on every single article before this was caught and fixed.
        article = (
            '<html><title id="pageTitle">Attributed Title</title><body>'
            "September 1, 2026</body></html>"
        )
        pages = {
            "https://ai.example/blog/": _listing_html("real"),
            "https://ai.example/blog/real/": article,
            "https://ai.example/blog/?page=2": _listing_html(),
        }
        monkeypatch.setattr(
            "fetch_announcements.fetch", lambda url, user_agent=None, **kw: pages[url]
        )
        out = from_listing_pagination(self._lab(), datetime(2026, 1, 1))
        assert out[0]["title"] == "Attributed Title"

    def test_url_contains_filters_non_article_links(self, monkeypatch):
        html = (
            '<html><body><a href="https://ai.example/blog/real/">x</a>'
            '<a href="https://ai.example/careers/">not an article</a></body></html>'
        )
        pages = {
            "https://ai.example/blog/": html,
            "https://ai.example/blog/real/": _article_html("September 1, 2026"),
        }
        monkeypatch.setattr(
            "fetch_announcements.fetch", lambda url, user_agent=None, **kw: pages.get(url, "")
        )
        out = from_listing_pagination(self._lab(), datetime(2026, 1, 1))
        assert [a["url"] for a in out] == ["https://ai.example/blog/real/"]

    def test_max_pages_is_a_hard_cap(self, monkeypatch):
        # Every page yields exactly one new, in-window article -- without a
        # cap this listing would paginate forever.
        def fake_fetch(url, user_agent=None, **kw):
            if "?page=" in url:
                n = url.split("page=")[-1]
                return _listing_html(f"p{n}")
            if url == "https://ai.example/blog/":
                return _listing_html("p1")
            return _article_html("September 1, 2026")  # an article page

        monkeypatch.setattr("fetch_announcements.fetch", fake_fetch)
        out = from_listing_pagination(self._lab(max_pages=3), datetime(2026, 1, 1))
        assert len(out) == 3  # capped, not infinite

    def test_single_article_fetch_failure_does_not_abort_the_page(self, monkeypatch):
        def fake_fetch(url, user_agent=None, **kw):
            if url.endswith("broken/"):
                raise RuntimeError("fetch failed: 500")
            pages = {
                "https://ai.example/blog/": _listing_html("broken", "ok"),
                "https://ai.example/blog/ok/": _article_html("September 1, 2026"),
                "https://ai.example/blog/?page=2": _listing_html(),
            }
            return pages[url]

        monkeypatch.setattr("fetch_announcements.fetch", fake_fetch)
        out = from_listing_pagination(self._lab(), datetime(2026, 1, 1))
        assert [a["url"] for a in out] == ["https://ai.example/blog/ok/"]

    def test_user_agent_is_threaded_through_to_fetch(self, monkeypatch):
        seen_uas = []

        def fake_fetch(url, user_agent=None, **kw):
            seen_uas.append(user_agent)
            if url == "https://ai.example/blog/":
                return _listing_html("a")
            if url.endswith("a/"):
                return _article_html("September 1, 2026")
            return _listing_html()

        monkeypatch.setattr("fetch_announcements.fetch", fake_fetch)
        from_listing_pagination(self._lab(user_agent=""), datetime(2026, 1, 1))
        assert all(ua is None for ua in seen_uas)  # "" normalizes to None, never sent as-is


def _cdx_rows(*pairs):
    """Build a CDX JSON response body: pairs of (url, timestamp)."""
    import json as _json

    return _json.dumps([["original", "timestamp"], *[list(p) for p in pairs]])


def _xai_page(date_iso=None, title="An Announcement", body_date_text=None):
    """A minimal archived x.ai page, JSON-LD date optional."""
    jsonld = (
        f'<script type="application/ld+json">{{"datePublished": "{date_iso}T00:00:00Z"}}</script>'
        if date_iso
        else ""
    )
    body_date = body_date_text or ""
    return (
        f"<html><head><title>{title} | SpaceXAI</title>{jsonld}</head>"
        f"<body>Back to news {body_date} {title} is now available.</body></html>"
    )


class TestFromWaybackCdx:
    """The silent failure this suite exists to catch: trusting a snapshot's
    crawl date as the article's publish date, which would place an old,
    merely-recrawled page inside the window -- confirmed live, a real
    2024-dated xAI page was recrawled in August 2026.
    """

    def _lab(self, **overrides):
        lab = {
            "id": "xai",
            "index_url": "https://x.ai/news",
            "url_contains": "/news/",
            "text_source": "full_text_archived",
        }
        lab.update(overrides)
        return lab

    def test_dates_from_jsonld_not_crawl_timestamp(self, monkeypatch):
        # The CDX timestamp (20260828, August) is recent; the article's own
        # JSON-LD date (2024-05-26) is not -- the crawl date must never be
        # used as a stand-in.
        pages = {
            "https://x.ai/news/series-b": _xai_page(date_iso="2024-05-26", title="Series B"),
        }

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                return _cdx_rows(("https://x.ai/news/series-b", "20260828141342"))
            return pages[url.split("id_/", 1)[1]]

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        out = from_wayback_cdx(self._lab(), datetime(2026, 6, 1))
        assert out == []  # 2024 is outside a June-2026 cutoff, despite the fresh crawl

    def test_in_window_article_kept_with_archive_snapshot_recorded(self, monkeypatch):
        pages = {
            "https://x.ai/news/composer-2-5": _xai_page(date_iso="2026-07-15", title="Composer 2.5"),
        }

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                return _cdx_rows(("https://x.ai/news/composer-2-5", "20260828151426"))
            return pages[url.split("id_/", 1)[1]]

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        out = from_wayback_cdx(self._lab(), datetime(2026, 6, 1))
        assert len(out) == 1
        art = out[0]
        assert art["url"] == "https://x.ai/news/composer-2-5"  # live URL preserved, not replaced
        assert art["date"] == "2026-07-15"
        assert art["archive_snapshot"].startswith("http://web.archive.org/web/20260828151426id_/")
        assert art["text_source"] == "full_text_archived"

    def test_title_site_suffix_stripped_both_brand_variants(self, monkeypatch):
        pages = {
            "https://x.ai/news/a": _xai_page(date_iso="2026-07-01", title="Grok 5"),
        }

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                return _cdx_rows(("https://x.ai/news/a", "20260801000000"))
            return pages[url.split("id_/", 1)[1]]

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        out = from_wayback_cdx(self._lab(), datetime(2026, 6, 1))
        assert out[0]["title"] == "Grok 5"  # " | SpaceXAI" suffix stripped

    def test_missing_jsonld_falls_back_to_visible_page_date(self, monkeypatch):
        pages = {
            "https://x.ai/news/a": _xai_page(title="Old-style Page", body_date_text="Jun 15, 2026"),
        }

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                return _cdx_rows(("https://x.ai/news/a", "20260801000000"))
            return pages[url.split("id_/", 1)[1]]

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        out = from_wayback_cdx(self._lab(), datetime(2026, 6, 1))
        assert out[0]["date"] == "2026-06-15"

    def test_index_url_and_image_assets_excluded_from_candidates(self, monkeypatch):
        pages = {
            "https://x.ai/news/real": _xai_page(date_iso="2026-07-01", title="Real Article"),
        }

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                return _cdx_rows(
                    ("https://x.ai/news", "20260801000000"),  # bare index -- excluded
                    ("https://x.ai/news/real/opengraph-image-abc.png", "20260801000000"),
                    ("https://x.ai/news/real", "20260801000000"),
                )
            return pages[url.split("id_/", 1)[1]]

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        out = from_wayback_cdx(self._lab(), datetime(2026, 6, 1))
        assert [a["url"] for a in out] == ["https://x.ai/news/real"]

    def test_query_string_variants_dedupe_to_the_latest_snapshot(self, monkeypatch):
        calls = []
        pages = {
            "https://x.ai/news/real": _xai_page(date_iso="2026-07-01", title="Real Article"),
        }

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                return _cdx_rows(
                    ("https://x.ai/news/real?_rsc=abc", "20260601000000"),
                    ("https://x.ai/news/real?utm_source=x", "20260615000000"),
                    ("https://x.ai/news/real", "20260828000000"),  # latest -- must win
                )
            calls.append(url)
            return pages[url.split("id_/", 1)[1]]

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        out = from_wayback_cdx(self._lab(), datetime(2026, 6, 1))
        assert len(out) == 1  # one article, not three near-duplicate query-string rows
        assert len(calls) == 1  # only the winning (latest) snapshot was ever fetched
        assert "20260828000000" in calls[0]

    def test_one_snapshot_fetch_failure_does_not_abort_the_run(self, monkeypatch):
        pages = {
            "https://x.ai/news/good": _xai_page(date_iso="2026-07-01", title="Good"),
        }

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                return _cdx_rows(
                    ("https://x.ai/news/bad", "20260801000000"),
                    ("https://x.ai/news/good", "20260801000000"),
                )
            real_url = url.split("id_/", 1)[1]
            if real_url == "https://x.ai/news/bad":
                raise RuntimeError("wayback fetch failed: 503")
            return pages[real_url]

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        out = from_wayback_cdx(self._lab(), datetime(2026, 6, 1))
        assert [a["url"] for a in out] == ["https://x.ai/news/good"]


class TestWaybackDecompress:
    def test_uncompressed_body_passed_through(self):
        body = b"<html>plain</html>"
        assert fetch_announcements._wayback_decompress(body, "") == body

    def test_gzip_body_decoded(self):
        import gzip as _gzip

        raw = b"<html>gzipped</html>"
        compressed = _gzip.compress(raw)
        assert fetch_announcements._wayback_decompress(compressed, "gzip") == raw

    def test_gzip_magic_bytes_detected_even_without_header_saying_so(self):
        # Confirmed in backfill_openai.py: the declared Content-Encoding and
        # the actual bytes disagree often enough that both are checked.
        import gzip as _gzip

        raw = b"<html>gzipped</html>"
        compressed = _gzip.compress(raw)
        assert fetch_announcements._wayback_decompress(compressed, "") == raw


class TestFetchUserAgentOverride:
    def test_default_user_agent_is_the_module_constant(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def read(self):
                return b"body"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=45):
            captured["headers"] = dict(req.header_items())
            return FakeResponse()

        import fetch_announcements as fa

        monkeypatch.setattr(fa.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(fa, "CACHE", fa.CACHE.parent / "test_cache_nonexistent")
        import shutil

        shutil.rmtree(fa.CACHE, ignore_errors=True)
        try:
            fa.fetch("https://example.com/x")
            assert captured["headers"].get("User-agent") == fa.UA
        finally:
            shutil.rmtree(fa.CACHE, ignore_errors=True)

    def test_empty_user_agent_sends_no_override(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def read(self):
                return b"body"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=45):
            captured["headers"] = dict(req.header_items())
            return FakeResponse()

        import fetch_announcements as fa

        monkeypatch.setattr(fa.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(fa, "CACHE", fa.CACHE.parent / "test_cache_nonexistent2")
        import shutil

        shutil.rmtree(fa.CACHE, ignore_errors=True)
        try:
            fa.fetch("https://example.com/y", user_agent=None)
            # Confirmed live against ai.meta.com's actual block behaviour:
            # the module's browser-style UA must not be the one sent.
            assert captured["headers"].get("User-agent") != fa.UA
        finally:
            shutil.rmtree(fa.CACHE, ignore_errors=True)


class TestDiscoveryCacheExpires:
    """The silent failure this suite exists to catch: an unexpiring disk cache
    pinning *discovery* to the day it was first written.

    This was live. `fetch` served any cached file regardless of age, and the
    cache holds sitemaps and RSS feeds as well as article bodies, so a local
    run on 2026-09-04 read Anthropic's sitemap as of 2026-09-02 and reported
    zero new articles. Nothing failed, nothing was slow, and "no new articles"
    is exactly what a correct quiet day looks like -- the failure was invisible
    at every layer above it (docs/decisions.md D36).
    """

    @staticmethod
    def _fake_net(monkeypatch, cache_dir, body=b"live"):
        """Point the module at an empty cache and count real fetches."""
        calls = []

        class FakeResponse:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=45):
            calls.append(req.full_url)
            return FakeResponse()

        monkeypatch.setattr(fetch_announcements.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(fetch_announcements, "CACHE", cache_dir)
        return calls

    def _stale(self, cache_dir, url, age_hours, body="cached"):
        """Write a cache entry and backdate it."""
        import os
        import re
        import time

        cache_dir.mkdir(parents=True, exist_ok=True)
        key = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:140]
        path = cache_dir / f"{key}.html"
        path.write_text(body)
        old = time.time() - age_hours * 3600
        os.utime(path, (old, old))
        return path

    def test_a_stale_discovery_document_is_refetched(self, monkeypatch, tmp_path):
        url = "https://lab.example/sitemap.xml"
        self._stale(tmp_path / "c", url, age_hours=48)
        calls = self._fake_net(monkeypatch, tmp_path / "c")

        body = fetch_announcements.fetch(
            url, max_age_hours=fetch_announcements.DISCOVERY_MAX_AGE_HOURS
        )

        assert calls == [url], "a two-day-old sitemap must not be served from disk"
        assert body == "live"

    def test_a_fresh_discovery_document_is_served_from_disk(self, monkeypatch, tmp_path):
        url = "https://lab.example/sitemap.xml"
        self._stale(tmp_path / "c", url, age_hours=1)
        calls = self._fake_net(monkeypatch, tmp_path / "c")

        body = fetch_announcements.fetch(
            url, max_age_hours=fetch_announcements.DISCOVERY_MAX_AGE_HOURS
        )

        assert calls == [], "expiry must not turn into refetching on every call"
        assert body == "cached"

    def test_an_article_body_is_cached_forever(self, monkeypatch, tmp_path):
        """Published article text is immutable; re-downloading it is pure waste."""
        url = "https://lab.example/blog/some-post/"
        self._stale(tmp_path / "c", url, age_hours=24 * 365)
        calls = self._fake_net(monkeypatch, tmp_path / "c")

        assert fetch_announcements.fetch(url) == "cached"
        assert calls == []

    def test_discovery_max_age_beats_the_nightly_cadence(self):
        """A max age at or above 24h would let a nightly firing reuse yesterday's feed."""
        assert 0 < fetch_announcements.DISCOVERY_MAX_AGE_HOURS < 24

    def test_from_rss_expires_its_index_but_not_its_articles(self, monkeypatch):
        """The integration-level check: the real bug was at the call site, not in `fetch`.

        `fetch` could grow a perfect expiry policy and the pipeline stay blind
        if `from_rss` never passed one, so assert what each call site asks for.
        """
        seen = {}

        def fake_fetch(url, user_agent=None, max_age_hours=None, **kw):
            seen[url] = max_age_hours
            if url == "https://feed.example/rss":
                return _rss_xml(link="https://x.example/a")
            return "<html><body><p>Body.</p></body></html>"

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        from_rss({"id": "openai", "index_url": "https://feed.example/rss",
                  "text_source": "full_text"}, datetime(2025, 1, 1))

        assert seen["https://feed.example/rss"] == fetch_announcements.DISCOVERY_MAX_AGE_HOURS
        assert seen["https://x.example/a"] is None


class TestScore:
    def test_is_signal_does_not_gate_the_score(self, rules):
        """`is_signal` was a hard veto until a stability probe found it flipping
        on 9% of re-classified items, zeroing articles that carried
        maximum-strength, quote-backed tags. The evidence decides the score now;
        the flag is diagnostic only.
        """
        with_flag = score_of(result(signal=True), rules)
        without = score_of(result(signal=False), rules)
        assert with_flag == without
        assert without[0] > 0

    def test_no_mechanism_means_no_score(self, rules):
        """The load-bearing property. Every mechanism tag must quote the
        document, so a multiplicative rule makes "cites a primary source" a
        structural guarantee of any non-zero score rather than a convention.

        An additive variant was tried and abandoned precisely because it let an
        item score on its event label alone, with no tags and no quotes.
        """
        for event in ("corporate_finance", "frontier_model_release", "other"):
            assert score_of(result(event, mechanisms=[]), rules)[0] == 0.0, event

    def test_zero_weight_event_scores_zero_however_it_is_tagged(self, rules):
        """`other` is a true residual: it cannot score, even on a maximum tag."""
        assert score_of(result("other"), rules) == (0.0, "none")

    def test_vocabulary_not_arithmetic_fixed_the_known_failures(self, rules):
        """The two v1 failures were vocabulary gaps, not a formula fault.

        Both clear the high band under the original multiplicative rule once the
        right event type exists.
        """
        s1 = score_of(result("corporate_finance", mechanisms=[mech()]), rules)[0]
        export = score_of(result("regulatory_action", mechanisms=[mech()]), rules)[0]
        assert s1 >= 60 and export >= 60

    def test_maximum_case(self, rules):
        score, band = score_of(result("frontier_model_release", mechanisms=[mech()]), rules)
        assert score == 100.0 and band == "high"

    def test_neither_axis_can_veto_the_other(self, rules):
        """The v1 bug: multiplying let a low event weight crush a maximum
        mechanism. The export control case scored 20; it must now clear 60."""
        assert score_of(result("regulatory_action"), rules)[0] >= 60

    def test_incremental_release_scores_below_frontier(self, rules):
        """Splitting the release types must actually separate them."""
        inc = score_of(result("incremental_model_release", mechanisms=[mech("medium", "high")]), rules)[0]
        front = score_of(result("frontier_model_release", mechanisms=[mech("high", "high")]), rules)[0]
        assert inc < front

    def test_confidence_is_a_gate_not_a_discount(self, rules):
        """Confidence is a probability weight running 0 to 1.

        Changed in v4 on evidence: a hand review of a 20-article gold run found
        that every tag a human rejected as unsupported was already marked low
        confidence by the model, and the old 1-3 scale paid for them anyway.
        Magnitude keeps 1-3 -- a small effect is still an effect; an unsupported
        claim is not.
        """
        assert rules["confidence"] == {"high": 1.0, "medium": 0.5, "low": 0.0}
        assert rules["magnitude"] == {"high": 3, "medium": 2, "low": 1}
        assert rules["max_mechanism"] == 3

    def test_a_low_confidence_tag_scores_nothing(self, rules):
        """The whole point of the v4 change, pinned."""
        for magnitude in ("high", "medium", "low"):
            assert score_of(
                result(mechanisms=[mech(magnitude, "low")]), rules
            )[0] == 0.0, magnitude

    def test_one_confident_tag_rescues_an_otherwise_low_confidence_item(self, rules):
        """The gate is per tag, not per article -- max, not veto."""
        assert score_of(
            result(mechanisms=[mech("low", "low"), mech("high", "high")]), rules
        )[0] > 0

    def test_axes_normalised_by_their_own_maxima(self, rules):
        assert rules["max_event_weight"] == max(rules["event_weight"].values())
        assert rules["max_mechanism"] == (
            max(rules["magnitude"].values()) * max(rules["confidence"].values())
        )

    def test_a_small_certain_effect_beats_a_big_unsupported_one(self, rules):
        """The v3 rule made these equal. That was backwards.

        In a system whose main risk is fabricated evidence, "a big thing we may
        be wrong about" must not outrank "a small thing the document states".
        """
        big_unsure = score_of(result(mechanisms=[mech("high", "low")]), rules)[0]
        small_sure = score_of(result(mechanisms=[mech("low", "high")]), rules)[0]
        assert big_unsure == 0.0
        assert small_sure > big_unsure

    def test_confidence_scales_the_score(self, rules):
        hi = score_of(result(mechanisms=[mech(confidence="high")]), rules)[0]
        med = score_of(result(mechanisms=[mech(confidence="medium")]), rules)[0]
        low = score_of(result(mechanisms=[mech(confidence="low")]), rules)[0]
        assert hi > med > low
        assert low == 0.0
        assert med == hi / 2, "medium is half of high, not two thirds"

    def test_strongest_mechanism_wins(self, rules):
        """Score takes the max, not the sum: many weak tags must not out-score
        one strong one."""
        weak = [mech(magnitude="low", confidence="low") for _ in range(6)]
        strong = [mech(magnitude="high", confidence="high")]
        assert score_of(result(mechanisms=weak), rules)[0] < score_of(
            result(mechanisms=strong), rules
        )[0]

    def test_unknown_event_type_scores_zero(self, rules):
        """A model inventing an event type must get 0, never a default."""
        assert score_of(result("something_invented"), rules)[0] == 0.0

    def test_bands_are_ordered_and_cover_zero(self, rules):
        mins = [b["min"] for b in rules["bands"]]
        assert mins == sorted(mins, reverse=True)
        assert mins[-1] == 0


class TestDropUnknownTags:
    def test_removes_hallucinated_ids(self):
        from score_announcements import drop_unknown_tags

        r = {
            "mechanisms": [mech(mid="training_compute_up"), mech(mid="invented_thing")],
            "categories": [{"id": "memory_storage"}, {"id": "not_a_category"}],
        }
        dropped = drop_unknown_tags(r, {"training_compute_up"}, {"memory_storage"})
        assert [m["id"] for m in r["mechanisms"]] == ["training_compute_up"]
        assert [c["id"] for c in r["categories"]] == ["memory_storage"]
        assert sorted(dropped) == [
            "categories:not_a_category",
            "mechanisms:invented_thing",
        ]

    def test_keeps_everything_valid(self):
        from score_announcements import drop_unknown_tags

        r = {"mechanisms": [mech()], "categories": []}
        assert drop_unknown_tags(r, {"training_compute_up"}, set()) == []


class TestConfigIntegrity:
    def test_every_event_type_in_prompt_has_a_weight(self, rules):
        """A type the prompt offers but the rule omits would score zero
        silently."""
        from score_announcements import PROMPT

        prompt = PROMPT.read_text()
        block = prompt.split("## Event type", 1)[1].split("## Output", 1)[0]
        offered = set(__import__("re").findall(r"`([a-z_]+)`", block))
        assert offered, "no event types found in prompt"
        assert offered <= set(rules["event_weight"]), (
            offered - set(rules["event_weight"])
        )

    def test_crypto_is_not_routable(self):
        cats = yaml.safe_load((ROOT / "config" / "categories.yaml").read_text())
        crypto = next(c for c in cats["categories"] if c["id"] == "crypto")
        assert crypto["lab_signal_routable"] is False

    def test_prompt_describes_every_text_source_we_emit(self):
        """A text_source the fetcher emits but the prompt never names.

        This is the bug that scored GPT-6 Astra 50 for the AI team: the
        model_index channel introduced `model_spec` and nobody told the prompt
        what it was, so the classifier had no calibration and hedged its
        confidence to medium. Silent -- the run succeeded, the score was just
        quietly wrong. Any new channel that invents a text_source now fails
        here instead.
        """
        from score_announcements import PROMPT

        emitted = set()
        for path in ("fetch_announcements.py", "backfill_openai.py"):
            src = (ROOT / "research" / "announcements" / path).read_text()
            emitted |= set(re.findall(r'"text_source"\]?\s*[:=]\s*"([a-z_]+)"', src))
        cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
        emitted |= {lab["text_source"] for lab in cfg["labs"] if "text_source" in lab}
        assert emitted, "found no text_source values to check"

        block = PROMPT.read_text().split("## What you are reading", 1)[1]
        block = block.split("## Mechanisms", 1)[0]
        described = set(re.findall(r"^- `([a-z_]+)`", block, re.M))
        assert emitted <= described, f"undescribed in prompt: {emitted - described}"

    def test_sources_declare_a_known_method(self):
        cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
        for lab in cfg["labs"]:
            assert lab["method"] in {"sitemap", "rss", "listing_pagination", "wayback_cdx"}
            assert lab["text_source"] in {"full_text", "rss_summary", "full_text_archived"}


class _LazyScoreAnnouncements:
    """Defer importing score_announcements until a test actually touches it.

    Same reason as the wrapper functions above: the module imports the Anthropic
    SDK at module scope, and the fetch/parse tests must run without it.
    """

    def __getattr__(self, name):
        import score_announcements

        return getattr(score_announcements, name)


sa = _LazyScoreAnnouncements()

class TestPracticeAxis:
    """The AI-team axis: tag validation and the second scoring rule.

    Written before the first paid run over the gold set, so a wiring error shows
    up here rather than in $0.30 of classifications.
    """

    @staticmethod
    def _tag(**kw):
        base = {
            "id": "orchestration", "action": "watch", "impact": "low",
            "confidence": "low", "dimensions": [], "reason": "r", "quote": "q",
        }
        base.update(kw)
        return base

    def test_no_practice_tag_scores_zero(self, rules):
        """The citation guarantee, mirrored onto the AI axis."""
        assert sa.ai_score_of({"practices": []}, rules) == (0.0, "none")

    def test_a_missing_practices_key_scores_zero(self, rules):
        """Results classified before v5 have no practices key at all."""
        assert sa.ai_score_of({}, rules) == (0.0, "none")

    def test_adopt_high_high_is_the_maximum(self, rules):
        score, band = sa.ai_score_of(
            {"practices": [self._tag(action="adopt", impact="high", confidence="high")]},
            rules,
        )
        assert (score, band) == (100.0, "high")

    def test_neither_axis_dominates_the_other(self, rules):
        """`action` is an axis, not an override.

        A high-impact, clearly-real development with nothing to do about it yet
        outranks a trivial thing that happens to be adoptable today. That is the
        multiplicative form doing its job, the same way event weight does not
        dominate mechanism strength on the investment side. Pinned because the
        tempting alternative -- sorting by action first -- would bury exactly the
        articles an AI team most wants to see early.
        """
        adopt_trivial = sa.ai_score_of(
            {"practices": [self._tag(action="adopt", impact="low", confidence="low")]},
            rules,
        )[0]
        watch_major = sa.ai_score_of(
            {"practices": [self._tag(action="watch", impact="high", confidence="high")]},
            rules,
        )[0]
        # Under the v4 confidence gate a low-confidence tag scores zero on
        # either axis, so the comparison is made at medium confidence.
        adopt_trivial = sa.ai_score_of(
            {"practices": [self._tag(action="adopt", impact="low", confidence="medium")]},
            rules,
        )[0]
        assert adopt_trivial == 16.7
        assert watch_major == 33.3
        assert watch_major > adopt_trivial

    def test_action_separates_two_otherwise_identical_tags(self, rules):
        """Holding impact and confidence fixed, action orders the result."""
        scores = [
            sa.ai_score_of(
                {"practices": [self._tag(action=a, impact="high", confidence="high")]},
                rules,
            )[0]
            for a in ("watch", "investigate", "adopt")
        ]
        assert scores == sorted(scores) and len(set(scores)) == 3, scores

    def test_the_strongest_tag_wins_not_the_sum(self, rules):
        """Same max-not-sum rule as the investment score."""
        one = sa.ai_score_of(
            {"practices": [self._tag(action="adopt", impact="high", confidence="high")]},
            rules,
        )[0]
        many = sa.ai_score_of(
            {"practices": [
                self._tag(action="adopt", impact="high", confidence="high"),
                self._tag(id="evaluation"),
                self._tag(id="integration"),
            ]},
            rules,
        )[0]
        assert one == many == 100.0

    def test_the_two_scores_are_independent(self, rules):
        """An article can be pure noise to investors and top of the AI list."""
        result = {
            "event_type": "other", "mechanisms": [],
            "practices": [self._tag(action="adopt", impact="high", confidence="high")],
        }
        assert sa.score_of(result, rules)[0] == 0.0
        assert sa.ai_score_of(result, rules)[0] == 100.0


class TestPracticeTagValidation:
    """drop_unknown_tags on the practice axis."""

    @staticmethod
    def _result(practices):
        return {"mechanisms": [], "categories": [], "practices": practices}

    def test_an_unknown_practice_id_is_dropped(self):
        r = self._result([{"id": "vibes", "dimensions": []}])
        dropped = sa.drop_unknown_tags(r, set(), set(), {"orchestration"}, {})
        assert r["practices"] == []
        assert dropped == ["practices:vibes"]

    def test_an_unknown_dimension_is_dropped_but_the_tag_survives(self):
        r = self._result([{"id": "orchestration", "dimensions": ["telepathy"]}])
        dropped = sa.drop_unknown_tags(
            r, set(), set(), {"orchestration"}, {"orchestration": set()}
        )
        assert len(r["practices"]) == 1
        assert r["practices"][0]["dimensions"] == []
        assert dropped == ["practices:orchestration/dimension:telepathy"]

    def test_model_capability_without_a_valid_dimension_is_dropped_entirely(self):
        """An undifferentiated 'it got better' is what the field exists to stop."""
        r = self._result([{"id": "model_capability", "dimensions": []}])
        dropped = sa.drop_unknown_tags(
            r, set(), set(), {"model_capability"}, {"model_capability": {"coding"}}
        )
        assert r["practices"] == []
        assert "no valid dimension" in dropped[0]

    def test_a_valid_dimension_is_kept(self):
        r = self._result([{"id": "model_capability", "dimensions": ["coding", "x"]}])
        dropped = sa.drop_unknown_tags(
            r, set(), set(), {"model_capability"}, {"model_capability": {"coding"}}
        )
        assert r["practices"][0]["dimensions"] == ["coding"]
        assert dropped == ["practices:model_capability/dimension:x"]

    def test_dimensions_are_capped_and_the_excess_is_reported(self):
        """Three big launches named 8, 7 and 7 of 9 dimensions on the first
        run, which says only "this is a big launch". The prompt asks for three;
        the cap is enforced here because the prompt asking did not work."""
        r = {"mechanisms": [], "categories": [], "practices": [{
            "id": "model_capability",
            "dimensions": ["coding", "reasoning", "latency", "cost", "reliability"],
        }]}
        dropped = sa.drop_unknown_tags(
            r, set(), set(), {"model_capability"},
            {"model_capability": {"coding", "reasoning", "latency", "cost", "reliability"}},
            3,
        )
        assert r["practices"][0]["dimensions"] == ["coding", "reasoning", "latency"]
        assert dropped == [
            "practices:model_capability/over-cap:cost",
            "practices:model_capability/over-cap:reliability",
        ], "the excess must be recorded, not silently truncated"

    def test_a_tag_at_the_cap_is_untouched(self):
        r = {"mechanisms": [], "categories": [], "practices": [{
            "id": "model_capability", "dimensions": ["coding", "reasoning", "cost"],
        }]}
        allowed = {"model_capability": {"coding", "reasoning", "cost"}}
        assert sa.drop_unknown_tags(r, set(), set(), {"model_capability"}, allowed, 3) == []
        assert len(r["practices"][0]["dimensions"]) == 3

    def test_no_cap_configured_means_no_truncation(self):
        r = {"mechanisms": [], "categories": [], "practices": [{
            "id": "model_capability", "dimensions": ["coding", "reasoning", "cost"],
        }]}
        allowed = {"model_capability": {"coding", "reasoning", "cost"}}
        sa.drop_unknown_tags(r, set(), set(), {"model_capability"}, allowed, None)
        assert len(r["practices"][0]["dimensions"]) == 3

    def test_the_cap_is_read_from_config_not_hardcoded(self):
        assert sa.dimension_cap() == 3

    def test_omitting_practice_ids_leaves_the_axis_untouched(self):
        """Callers that predate v5 must not have their practices silently wiped."""
        r = self._result([{"id": "anything", "dimensions": []}])
        sa.drop_unknown_tags(r, set(), set())
        assert len(r["practices"]) == 1


class TestPracticeVocabulary:
    """The config the prompt is built from."""

    def test_every_placeholder_is_filled(self):
        system, _ = sa.build_prompt({
            "lab": "l", "date": "d", "url": "u",
            "text_source": "full_text", "text": "t",
        })
        for placeholder in ("{mechanisms}", "{categories}", "{practices}"):
            assert placeholder not in system, f"{placeholder} left unfilled"

    def test_the_schema_asks_for_practices(self):
        assert "practices" in sa.build_schema()["properties"]
        assert "practices" in sa.build_schema()["required"]

    def test_model_capability_declares_dimensions(self):
        dims = sa.practice_dimensions()
        assert dims["model_capability"], "model_capability has no dimensions"
        assert dims["orchestration"] == set(), "only model_capability takes dimensions"

    def test_practice_ids_never_collide_with_mechanism_ids(self):
        _, _, _, mech_ids, _, prac_ids = sa.vocabularies()
        assert not (mech_ids & prac_ids)


class TestStripHtmlEntities:
    """Entity decoding, added after a real failure.

    The stored corpus held literal `&#x27;` and `&amp;` because strip_html never
    unescaped. The model reads that text and quotes the decoded form, so a
    verbatim quote check reported 9 false hallucinations out of 90 tags. The
    quotes were correct; the corpus was wrong.
    """

    def test_common_entities_are_decoded(self):
        assert strip_html("<p>compute &amp; memory</p>") == "compute & memory"
        assert strip_html("<p>it&#x27;s</p>") == "it's"
        assert strip_html("<p>&quot;quoted&quot;</p>") == '"quoted"'

    def test_double_encoded_entities_are_decoded(self):
        """Anthropic's pages carry these; one unescape pass is not enough."""
        assert strip_html("<p>it&amp;#x27;s</p>") == "it's"

    def test_comments_are_removed(self):
        assert "secret" not in strip_html("<p>keep</p><!-- secret -->")

    def test_a_quote_from_stripped_text_matches_the_stripped_text(self):
        """The property that actually matters for the citation guarantee."""
        page = "<article><p>reduced compute &amp; memory costs</p></article>"
        text = strip_html(page)
        assert "reduced compute & memory costs" in text


class _Usage:
    """Stub of an Anthropic usage object, cache fields optional."""

    def __init__(self, input_tokens, output_tokens, write=None, read=None):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        if write is not None:
            self.cache_creation_input_tokens = write
        if read is not None:
            self.cache_read_input_tokens = read


class TestCallCost:
    """The silent failure here: caching lands and the cost log keeps billing
    every token at the plain input rate — under-reporting writes (1.25x) and
    over-reporting reads (0.1x)."""

    def cost(self, usage):
        from score_announcements import call_cost

        return call_cost("claude-sonnet-5", "https://x", usage, 0.0)

    def test_no_cache_fields_bills_plain_rates(self):
        c = self.cost(_Usage(1_000_000, 100_000))
        assert c["usd"] == pytest.approx(2.00 + 1.00)
        assert c["cache_write_tokens"] == 0
        assert c["cache_read_tokens"] == 0

    def test_cache_write_billed_at_1_25x(self):
        c = self.cost(_Usage(0, 0, write=1_000_000))
        assert c["usd"] == pytest.approx(2.50)

    def test_cache_read_billed_at_0_1x(self):
        c = self.cost(_Usage(0, 0, read=1_000_000))
        assert c["usd"] == pytest.approx(0.20)

    def test_none_cache_fields_treated_as_zero(self):
        # The SDK reports None, not 0, on some responses.
        c = self.cost(_Usage(1_000, 100, write=None, read=None))
        assert c["cache_write_tokens"] == 0
        assert c["cache_read_tokens"] == 0

    def test_mixed_call_sums_all_components(self):
        # Warm-cache call: small uncached preamble, big cached read.
        c = self.cost(_Usage(3_560, 2_500, write=0, read=7_280))
        expected = (3_560 * 2.0 + 7_280 * 2.0 * 0.10 + 2_500 * 10.0) / 1e6
        assert c["usd"] == pytest.approx(round(expected, 6))

    def test_default_mode_is_interactive(self):
        c = self.cost(_Usage(1_000_000, 100_000))
        assert c["mode"] == "interactive"


class TestCallCostBatchMode:
    """The silent failure here: a batch run's cost log looks identical to an
    interactive run's, so a cost dashboard can't separate the two rates the
    workflow actually paid -- planning.md 4a requires the mode be recorded,
    not just the number."""

    def cost(self, usage, mode):
        from score_announcements import call_cost

        return call_cost("claude-sonnet-5", "https://x", usage, 0.0, mode=mode)

    def test_batch_mode_halves_the_price(self):
        interactive = self.cost(_Usage(1_000_000, 100_000), "interactive")
        batch = self.cost(_Usage(1_000_000, 100_000), "batch")
        assert batch["usd"] == pytest.approx(interactive["usd"] * 0.5)

    def test_batch_mode_halves_cache_components_too(self):
        # The 0.5x must apply to the already cache-adjusted total, not just
        # the plain input/output tokens -- a batch call that mostly reads
        # from cache should still be half the interactive equivalent.
        interactive = self.cost(_Usage(0, 0, write=1_000_000), "interactive")
        batch = self.cost(_Usage(0, 0, write=1_000_000), "batch")
        assert batch["usd"] == pytest.approx(interactive["usd"] * 0.5)

    def test_mode_is_recorded_on_the_row(self):
        assert self.cost(_Usage(100, 10), "batch")["mode"] == "batch"


class TestScoringEnabledGate:
    """The silent failure here: a scheduled invocation of this script spends
    real money the moment it exists, with no explicit switch a human decided
    to flip. The gate must be off by default and never bypassed silently."""

    class _Proceeded(Exception):
        """Raised by the patched load_env() to prove control passed the gate."""

    def _prepare(self, monkeypatch, env, argv):
        import score_announcements as mod

        monkeypatch.delenv("SCORING_ENABLED", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(mod.sys, "argv", ["score_announcements.py", *argv])

        def fake_load_env():
            raise self._Proceeded

        monkeypatch.setattr(mod, "load_env", fake_load_env)
        return mod

    def test_unset_env_var_blocks_the_run(self, monkeypatch, capsys):
        mod = self._prepare(monkeypatch, {}, [])
        mod.main()  # returns quietly; load_env is never reached
        assert "deactivated" in capsys.readouterr().out

    def test_false_env_var_blocks_the_run(self, monkeypatch):
        mod = self._prepare(monkeypatch, {"SCORING_ENABLED": "false"}, [])
        mod.main()

    def test_force_flag_bypasses_the_gate_even_when_unset(self, monkeypatch):
        # --force is an explicit, visible human decision at the call site,
        # not a silent default -- that distinction is the point of the gate.
        mod = self._prepare(monkeypatch, {}, ["--force"])
        with pytest.raises(self._Proceeded):
            mod.main()

    def test_true_env_var_lets_it_proceed_past_the_gate(self, monkeypatch):
        mod = self._prepare(monkeypatch, {"SCORING_ENABLED": "true"}, [])
        with pytest.raises(self._Proceeded):
            mod.main()

    def test_case_insensitive_true_values_accepted(self, monkeypatch):
        for value in ("1", "True", "YES"):
            mod = self._prepare(monkeypatch, {"SCORING_ENABLED": value}, [])
            with pytest.raises(self._Proceeded):
                mod.main()


class _Content:
    """Stub of one content block in a batch result message."""

    def __init__(self, type, text=None):
        self.type = type
        self.text = text


class _Message:
    """Stub of a batch item's `result.message`."""

    def __init__(self, stop_reason, content, usage):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = usage
        self.stop_details = None


class _Result:
    def __init__(self, type, message=None):
        self.type = type
        self.message = message


class _ResultItem:
    def __init__(self, custom_id, result):
        self.custom_id = custom_id
        self.result = result


class _Batch:
    def __init__(self, id, processing_status, succeeded=0, errored=0):
        self.id = id
        self.processing_status = processing_status
        self.request_counts = type("_Counts", (), {"processing": 0, "succeeded": succeeded, "errored": errored})()


class _FakeBatches:
    """Stub of `client.messages.batches` -- create/retrieve/results only."""

    def __init__(self, result_items):
        self._result_items = result_items

    def create(self, requests):
        return _Batch("batch_1", "ended", succeeded=len(self._result_items))

    def retrieve(self, batch_id):
        return _Batch(batch_id, "ended", succeeded=len(self._result_items))

    def results(self, batch_id):
        return iter(self._result_items)


class _FakeClient:
    def __init__(self, result_items):
        self.messages = type("_Messages", (), {"batches": _FakeBatches(result_items)})()


class TestRunBatch:
    """The silent failure this suite exists to catch: one bad item in a
    hundred-item batch either loses the cost record of every already-billed
    item ahead of it, or a genuinely billed refusal/truncation contributes
    no cost row at all -- both real bugs found by code review, not by a
    test written in advance.
    """

    def _article(self, url):
        return {
            "url": url,
            "text": "some article text",
            "date": "2026-01-01",
            "title": "T",
            "lab": "anthropic",
            "text_source": "full_text",
        }

    def _run(self, monkeypatch, tmp_path, result_items, articles):
        import score_announcements as sa

        monkeypatch.setattr(sa, "CACHE", tmp_path / "cache")
        (tmp_path / "cache").mkdir()
        cost_path = tmp_path / "cost.json"
        monkeypatch.setattr(sa, "COST", cost_path)
        client = _FakeClient(result_items)
        results, failures, costs = sa.run_batch(
            client, "claude-sonnet-5", articles, set(), set(), None, None, None, poll_seconds=0
        )
        return results, failures, costs, cost_path

    def test_malformed_json_item_does_not_abort_remaining_items(self, monkeypatch, tmp_path):
        good_content = [_Content("text", '{"mechanisms": [], "categories": []}')]
        bad_content = [_Content("text", "not valid json")]
        items = [
            _ResultItem("c1", _Result("succeeded", _Message("end_turn", bad_content, _Usage(100, 10)))),
            _ResultItem("c2", _Result("succeeded", _Message("end_turn", good_content, _Usage(100, 10)))),
        ]
        articles = [self._article("https://x/1"), self._article("https://x/2")]
        # by_custom_id is built from articles in the order run_batch iterates
        # them, keyed by a sha1 of the url -- reach in via the real hashing
        # so the fake custom_ids line up.
        import hashlib
        items[0].custom_id = hashlib.sha1(b"https://x/1").hexdigest()
        items[1].custom_id = hashlib.sha1(b"https://x/2").hexdigest()

        results, failures, costs, _ = self._run(monkeypatch, tmp_path, items, articles)
        assert results == {"https://x/2": {"mechanisms": [], "categories": [], "dropped_tags": []}}
        assert failures[0]["url"] == "https://x/1"
        assert "JSONDecodeError" in failures[0]["error"]
        # The malformed item did not abort the loop -- item 2 was still scored.

    def test_malformed_json_item_still_records_its_cost(self, monkeypatch, tmp_path):
        bad_content = [_Content("text", "not valid json")]
        items = [_ResultItem("", _Result("succeeded", _Message("end_turn", bad_content, _Usage(100, 10))))]
        articles = [self._article("https://x/1")]
        import hashlib
        items[0].custom_id = hashlib.sha1(b"https://x/1").hexdigest()

        _, failures, costs, cost_path = self._run(monkeypatch, tmp_path, items, articles)
        assert len(costs) == 1
        assert costs[0]["usd"] > 0
        assert json.loads(cost_path.read_text()) == costs  # persisted to disk, not just returned

    def test_refusal_is_billed_not_dropped(self, monkeypatch, tmp_path):
        items = [_ResultItem("", _Result("succeeded", _Message("refusal", [], _Usage(50, 5))))]
        articles = [self._article("https://x/1")]
        import hashlib
        items[0].custom_id = hashlib.sha1(b"https://x/1").hexdigest()

        _, failures, costs, cost_path = self._run(monkeypatch, tmp_path, items, articles)
        assert failures[0]["error"].startswith("refused")
        assert len(costs) == 1
        assert json.loads(cost_path.read_text()) == costs

    def test_max_tokens_truncation_is_billed_not_dropped(self, monkeypatch, tmp_path):
        items = [_ResultItem("", _Result("succeeded", _Message("max_tokens", [], _Usage(50, 12000))))]
        articles = [self._article("https://x/1")]
        import hashlib
        items[0].custom_id = hashlib.sha1(b"https://x/1").hexdigest()

        _, failures, costs, cost_path = self._run(monkeypatch, tmp_path, items, articles)
        assert "max_tokens" in failures[0]["error"]
        assert len(costs) == 1
        assert json.loads(cost_path.read_text()) == costs

    def test_batch_level_failure_records_no_cost(self, monkeypatch, tmp_path):
        # No `message` exists at all for a batch-level error (expired etc.)
        # -- nothing was billed, so nothing should be recorded.
        items = [_ResultItem("", _Result("errored", None))]
        articles = [self._article("https://x/1")]
        import hashlib
        items[0].custom_id = hashlib.sha1(b"https://x/1").hexdigest()

        _, failures, costs, cost_path = self._run(monkeypatch, tmp_path, items, articles)
        assert failures[0]["error"] == "batch errored"
        assert costs == []

    def test_cost_written_incrementally_survives_a_later_item_crashing(self, monkeypatch, tmp_path):
        # The core of the fix: item 1 succeeds and must be persisted to disk
        # before item 2 (malformed) is even processed -- not batched up and
        # written only after the whole loop returns.
        good_content = [_Content("text", '{"mechanisms": [], "categories": []}')]
        bad_content = [_Content("text", "not valid json")]
        items = [
            _ResultItem("", _Result("succeeded", _Message("end_turn", good_content, _Usage(100, 10)))),
            _ResultItem("", _Result("succeeded", _Message("end_turn", bad_content, _Usage(100, 10)))),
        ]
        articles = [self._article("https://x/1"), self._article("https://x/2")]
        import hashlib
        items[0].custom_id = hashlib.sha1(b"https://x/1").hexdigest()
        items[1].custom_id = hashlib.sha1(b"https://x/2").hexdigest()

        _, failures, costs, cost_path = self._run(monkeypatch, tmp_path, items, articles)
        assert len(costs) == 2  # both items billed, including the failed one
        assert json.loads(cost_path.read_text()) == costs


def _models_md(*ids):
    """A Markdown model index linking each given model id."""
    return "# Models\n\n" + "\n".join(
        f"- [{i}](/api/docs/models/{i}) ([md](/api/docs/models/{i}.md))" for i in ids
    )


class TestModelIndex:
    """The second OpenAI discovery channel.

    The silent failure this exists to catch: a lab ships a flagship model, the
    RSS feed omits the launch post, and the run reports success having found
    nothing. That is exactly what happened with GPT-6 Astra on 2026-09-03.
    """

    def _baseline(self, tmp_path, monkeypatch, ids):
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"openai": list(ids)}))
        monkeypatch.setattr(fetch_announcements, "MODEL_BASELINE", path)

    def _lab(self):
        return {"id": "openai", "index_url": "https://dev.example/models.md"}

    def test_model_absent_from_baseline_is_emitted(self, tmp_path, monkeypatch):
        def fake_fetch(url, **kw):
            if url.endswith("models.md"):
                return _models_md("gpt-5", "gpt-6-astra")
            assert url == "https://dev.example/models/gpt-6-astra.md"
            return "# GPT-6 Astra\n\nOur most capable model.\n"

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        self._baseline(tmp_path, monkeypatch, ["gpt-5"])
        out = from_model_index(self._lab(), datetime(2025, 1, 1))

        assert len(out) == 1
        assert out[0]["url"] == "https://dev.example/models/gpt-6-astra"
        assert out[0]["title"] == "GPT-6 Astra"
        assert out[0]["text_source"] == "model_spec"

    def test_discovery_date_is_labelled_not_passed_off_as_publication(
        self, tmp_path, monkeypatch
    ):
        # A model spec page carries a knowledge cutoff, never a publication
        # date. If `date` were emitted unlabelled, a first-seen date would be
        # scored as though the lab had published that day.
        monkeypatch.setattr(
            fetch_announcements,
            "fetch",
            lambda url, **kw: _models_md("m1") if url.endswith("models.md") else "# M1\n",
        )
        self._baseline(tmp_path, monkeypatch, [])
        out = from_model_index(self._lab(), datetime(2025, 1, 1))

        assert out[0]["date_basis"] == "first_seen"

    def test_baseline_models_are_not_re_emitted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            fetch_announcements,
            "fetch",
            lambda url, **kw: _models_md("gpt-4o", "gpt-5"),
        )
        self._baseline(tmp_path, monkeypatch, ["gpt-4o", "gpt-5"])

        assert from_model_index(self._lab(), datetime(2025, 1, 1)) == []

    def test_already_stored_model_is_skipped(self, tmp_path, monkeypatch):
        # The deployed run has no disk, so the settled-URL set is the only
        # thing preventing a re-emit once the baseline is out of date.
        monkeypatch.setattr(
            fetch_announcements, "fetch", lambda url, **kw: _models_md("gpt-6-astra")
        )
        self._baseline(tmp_path, monkeypatch, [])
        out = from_model_index(
            self._lab(),
            datetime(2025, 1, 1),
            skip={"https://dev.example/models/gpt-6-astra"},
        )

        assert out == []

    def test_parsing_nothing_raises_rather_than_reporting_no_launches(
        self, tmp_path, monkeypatch
    ):
        # The whole point of this channel is redundancy. A parser that returns
        # [] when the page shape changes is indistinguishable from "the lab
        # shipped nothing", which is the failure it was added to close.
        monkeypatch.setattr(
            fetch_announcements, "fetch", lambda url, **kw: "<html>redesigned</html>"
        )
        self._baseline(tmp_path, monkeypatch, [])

        with pytest.raises(RuntimeError, match="parsed 0 models"):
            from_model_index(self._lab(), datetime(2025, 1, 1))

    def test_one_unreachable_spec_page_does_not_lose_the_others(
        self, tmp_path, monkeypatch, capsys
    ):
        def fake_fetch(url, **kw):
            if url.endswith("models.md"):
                return _models_md("broken", "fine")
            if "broken" in url:
                raise RuntimeError("fetch failed: 503")
            return "# Fine\n\nBody.\n"

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        self._baseline(tmp_path, monkeypatch, [])
        out = from_model_index(self._lab(), datetime(2025, 1, 1))

        assert [a["title"] for a in out] == ["Fine"]
        assert "SKIP model spec broken" in capsys.readouterr().out


class TestChannels:
    def test_lab_without_also_has_one_channel(self):
        lab = {"id": "mistral", "method": "rss", "index_url": "https://m.example/rss"}
        assert fetch_announcements.channels(lab) == [lab]

    def test_secondary_channel_inherits_identity_and_overrides_its_own_keys(self):
        lab = {
            "id": "openai",
            "label": "OpenAI",
            "method": "rss",
            "index_url": "https://openai.example/rss",
            "also": [{"method": "model_index", "index_url": "https://dev.example/m.md"}],
        }
        primary, second = fetch_announcements.channels(lab)

        assert primary["method"] == "rss"
        assert second["method"] == "model_index"
        assert second["index_url"] == "https://dev.example/m.md"
        assert second["id"] == "openai" and second["label"] == "OpenAI"


class TestOpenAIHasRedundantDiscovery:
    def test_openai_declares_a_second_channel(self):
        # Regression guard on the incident itself: OpenAI's RSS feed omitted
        # the GPT-6 Astra launch post entirely. Dropping back to one channel
        # restores the blind spot without failing anything.
        srcs = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
        openai = next(l for l in srcs["labs"] if l["id"] == "openai")

        assert [c["method"] for c in openai.get("also", [])] == [
            "model_index",
            "discourse",
        ]

    def test_baseline_covers_the_catalogue_but_not_the_missed_launch(self):
        baseline = json.loads(
            (ROOT / "research" / "docs" / "model_index_baseline.json").read_text()
        )["openai"]

        assert len(baseline) > 50
        assert "gpt-5" in baseline
        assert "gpt-6-astra" not in baseline


def _category_json(*topics):
    """A Discourse category listing."""
    return json.dumps({"topic_list": {"topics": [
        {"id": i, "slug": f"slug-{i}", "title": t, "created_at": f"{d}T19:51:25.371Z"}
        for i, (t, d) in enumerate(topics, start=100)
    ]}})


def _topic_json(cooked):
    return json.dumps({"post_stream": {"posts": [{"cooked": cooked}]}})


class TestDiscourse:
    """OpenAI's developer forum, the third channel.

    It carried the GPT-6 Astra launch that reached neither the RSS feed nor
    any sitemap.
    """

    def _lab(self):
        return {"id": "openai", "index_url": "https://forum.example/c/announcements/6.json"}

    def test_topic_is_emitted_and_the_canonical_link_is_kept(self, monkeypatch):
        def fake_fetch(url, **kw):
            if url.endswith("6.json"):
                return _category_json(("Introducing GPT-6-Astra", "2026-09-03"))
            return _topic_json('<p>Astra is here. <a href="https://openai.com/index/gpt-6-astra/">Read more</a></p>')

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        out = from_discourse(self._lab(), datetime(2026, 6, 1))

        assert len(out) == 1
        assert out[0]["date"] == "2026-09-03"
        assert out[0]["text_source"] == "forum_post"
        assert out[0]["canonical_url"] == "https://openai.com/index/gpt-6-astra"

    def test_citation_is_the_topic_not_the_unfetchable_canonical_page(self, monkeypatch):
        # openai.com returns 403 to everything this pipeline can send, so the
        # canonical link cannot be the citation — it could never be
        # re-verified. The forum topic can.
        def fake_fetch(url, **kw):
            if url.endswith("6.json"):
                return _category_json(("Launch", "2026-09-03"))
            return _topic_json('<p>x <a href="https://openai.com/index/thing/">c</a></p>')

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        out = from_discourse(self._lab(), datetime(2026, 6, 1))

        assert out[0]["url"] == "https://forum.example/t/slug-100/100"

    def test_topic_without_a_canonical_link_still_stands_alone(self, monkeypatch):
        def fake_fetch(url, **kw):
            if url.endswith("6.json"):
                return _category_json(("No link here", "2026-09-03"))
            return _topic_json("<p>Body with no outbound link.</p>")

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        out = from_discourse(self._lab(), datetime(2026, 6, 1))

        assert out[0]["canonical_url"] is None
        assert out[0]["url"].startswith("https://forum.example/t/")

    def test_topic_older_than_the_window_is_dropped(self, monkeypatch):
        # The category listing carries pinned topics from 2021.
        monkeypatch.setattr(
            fetch_announcements,
            "fetch",
            lambda url, **kw: _category_json(("Welcome to the forum", "2021-02-26")),
        )
        assert from_discourse(self._lab(), datetime(2026, 6, 1)) == []

    def test_already_stored_topic_is_skipped(self, monkeypatch):
        monkeypatch.setattr(
            fetch_announcements,
            "fetch",
            lambda url, **kw: _category_json(("Launch", "2026-09-03")),
        )
        out = from_discourse(
            self._lab(), datetime(2026, 6, 1),
            skip={"https://forum.example/t/slug-100/100"},
        )
        assert out == []

    def test_empty_category_raises_rather_than_reporting_no_announcements(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            fetch_announcements, "fetch", lambda url, **kw: json.dumps({"topic_list": {"topics": []}})
        )
        with pytest.raises(RuntimeError, match="listed 0 topics"):
            from_discourse(self._lab(), datetime(2026, 6, 1))

    def test_one_unreadable_topic_does_not_lose_the_others(self, monkeypatch, capsys):
        def fake_fetch(url, **kw):
            if url.endswith("6.json"):
                return _category_json(("Broken", "2026-09-03"), ("Fine", "2026-09-03"))
            if url.endswith("/100.json"):
                return "not json at all"
            return _topic_json("<p>Fine body.</p>")

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        out = from_discourse(self._lab(), datetime(2026, 6, 1))

        assert [a["title"] for a in out] == ["Fine"]
        assert "SKIP topic 100" in capsys.readouterr().out


class TestChannelsCanBeTurnedOff:
    def test_disabled_channel_does_not_run(self):
        lab = {
            "id": "openai",
            "method": "rss",
            "index_url": "https://openai.example/rss",
            "also": [
                {"method": "model_index", "index_url": "https://a.example/m.md"},
                {"method": "discourse", "enabled": False, "index_url": "https://f.example/c.json"},
            ],
        }
        assert [c["method"] for c in fetch_announcements.channels(lab)] == [
            "rss",
            "model_index",
        ]

    def test_a_channel_is_on_unless_it_says_otherwise(self):
        lab = {"id": "x", "method": "rss", "also": [{"method": "discourse"}]}
        assert len(fetch_announcements.channels(lab)) == 2

    def test_disabled_channels_are_still_validated(self, tmp_path):
        # A channel turned off while it misbehaves is meant to come back on.
        # A config error that only surfaces on re-enabling is found at the
        # worst possible moment.
        sys.path.insert(0, str(ROOT / "config"))
        import validate

        (tmp_path / "sources.yaml").write_text(yaml.safe_dump({
            "window_months": 3,
            "labs": [{
                "id": "openai", "method": "rss", "index_url": "https://a.example/rss",
                "also": [{"method": "discourse", "enabled": False}],  # no index_url
            }],
        }))
        errors = validate.check_sources(tmp_path)

        assert any("discourse" in e and "index_url" in e for e in errors)


class TestCanonicalLink:
    """The link a forum post points at.

    This field is the compensating control for citing the forum topic rather
    than the canonical page (D47). A confident wrong link is worse than none:
    it sends a reader to a pricing page while claiming to preserve the
    announcement.
    """

    def test_reference_subdomains_are_not_the_announcement(self):
        cooked = '<p>See <a href="https://developers.openai.com/api/docs/pricing">pricing</a>.</p>'
        assert fetch_announcements._canonical_link(cooked) is None

    def test_an_index_article_wins_over_an_earlier_reference_link(self):
        cooked = (
            '<a href="https://developers.openai.com/api/docs/models/gpt-6-astra">docs</a>'
            '<a href="https://openai.com/index/gpt-6-astra/">announcement</a>'
        )
        assert (fetch_announcements._canonical_link(cooked)
                == "https://openai.com/index/gpt-6-astra")

    def test_a_fragment_is_stripped_so_one_page_has_one_spelling(self):
        # Two spellings of one URL defeat the free exact-match dedupe pass
        # planned in docs/next_steps_0309.md.
        cooked = '<a href="https://openai.com/index/previewing-gpt-5-6-sol/#pricing">x</a>'
        assert (fetch_announcements._canonical_link(cooked)
                == "https://openai.com/index/previewing-gpt-5-6-sol")

    def test_a_newsroom_page_outside_index_still_counts(self):
        cooked = '<a href="https://openai.com/webmcp-challenge/">x</a>'
        assert (fetch_announcements._canonical_link(cooked)
                == "https://openai.com/webmcp-challenge")


class TestDiscoursePagination:
    """Discourse pages at 30 and orders by activity, not creation date.

    A launch announcement with few replies can sit past position 30 after a
    busy month. Reading page one only would drop it with no error and no count
    — the same silent failure this channel was built to close.
    """

    def _lab(self):
        return {"id": "openai", "index_url": "https://forum.example/c/a/6.json"}

    def _page(self, topics, more=None):
        return json.dumps({"topic_list": {
            "topics": topics, "more_topics_url": more}})

    def _topic(self, tid, created, bumped):
        return {"id": tid, "slug": f"s-{tid}", "title": f"T{tid}",
                "created_at": f"{created}T10:00:00.000Z",
                "bumped_at": f"{bumped}T10:00:00.000Z"}

    def test_an_in_window_topic_on_page_two_is_still_found(self, monkeypatch):
        def fake_fetch(url, **kw):
            if "page=1" in url:
                return self._page([self._topic(2, "2026-09-03", "2026-09-03")])
            if url.endswith("6.json"):
                # Page one: an old topic kept at the top by a recent bump.
                return self._page([self._topic(1, "2021-02-26", "2026-09-04")],
                                  more="/c/a/6?page=1")
            return _topic_json("<p>body</p>")

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        out = from_discourse(self._lab(), datetime(2026, 6, 1))

        assert [a["title"] for a in out] == ["T2"]

    def test_pagination_stops_once_a_page_predates_the_window(self, monkeypatch):
        pages = []

        def fake_fetch(url, **kw):
            if url.endswith("6.json"):
                pages.append(url)
                return self._page([self._topic(1, "2020-01-01", "2020-01-01")],
                                  more="/c/a/6?page=1")
            if "page=" in url:
                pages.append(url)
                return self._page([self._topic(2, "2019-01-01", "2019-01-01")])
            return _topic_json("<p>body</p>")

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        from_discourse(self._lab(), datetime(2026, 6, 1))

        assert len(pages) == 1

    def test_endless_pagination_raises_rather_than_truncating(self, monkeypatch):
        # A capped sweep that reports success is indistinguishable from a
        # category with nothing older — the original bug in a new place.
        def fake_fetch(url, **kw):
            if "/t/" in url:
                return _topic_json("<p>body</p>")
            return self._page([self._topic(1, "2026-09-01", "2026-09-01")],
                              more="/c/a/6?page=99")

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        with pytest.raises(RuntimeError, match="hit .* pages still inside the window"):
            from_discourse(self._lab(), datetime(2026, 6, 1))


class TestModelBaselineIsRequired:
    def test_a_lab_with_no_baseline_raises_instead_of_flooding(
        self, tmp_path, monkeypatch
    ):
        # Adding a model_index channel is a config-only change, so this case is
        # reachable by design. Defaulting to an empty baseline would emit a
        # lab's entire catalogue as launches, each paying for a classification.
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"openai": ["gpt-5"]}))
        monkeypatch.setattr(fetch_announcements, "MODEL_BASELINE", path)
        monkeypatch.setattr(
            fetch_announcements, "fetch",
            lambda url, **kw: _models_md("m1", "m2"))

        with pytest.raises(RuntimeError, match="no model baseline for 'mistral'"):
            from_model_index(
                {"id": "mistral", "index_url": "https://d.example/models.md"},
                datetime(2025, 1, 1))


class TestStoredTextIsCapped:
    def test_a_very_long_forum_post_is_capped_like_every_other_source(
        self, monkeypatch
    ):
        # Uncapped text goes into the classifier prompt at full length while
        # every other source is capped, so the only symptom is per-call cost
        # drifting above what docs/cost.md was measured against.
        def fake_fetch(url, **kw):
            if url.endswith("6.json"):
                return _category_json(("Long", "2026-09-03"))
            return _topic_json("<p>" + ("word " * 20000) + "</p>")

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        out = from_discourse(
            {"id": "openai", "index_url": "https://f.example/c/a/6.json"},
            datetime(2026, 6, 1))

        assert len(out[0]["text"]) == 24000


class TestModelIndexPathComesFromConfig:
    def test_a_docs_path_other_than_openais_still_parses(self, tmp_path, monkeypatch):
        # Hardcoding OpenAI's /api/docs/models/ made a configuration mismatch
        # present as "parsed 0 models", which reads as a page-shape change.
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps({"otherlab": []}))
        monkeypatch.setattr(fetch_announcements, "MODEL_BASELINE", path)

        def fake_fetch(url, **kw):
            if url.endswith("catalogue.md"):
                return "# Models\n- [A](/docs/model-catalogue/model-a)"
            return "# Model A\n\nSpec.\n"

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        out = from_model_index(
            {"id": "otherlab",
             "index_url": "https://lab.example/docs/model-catalogue.md"},
            datetime(2025, 1, 1))

        assert [a["url"] for a in out] == [
            "https://lab.example/docs/model-catalogue/model-a"]


class TestEnabledMustBeABool:
    def test_a_quoted_false_is_rejected(self, tmp_path):
        # channels() reads `enabled` by truthiness, so `enabled: "false"` keeps
        # the channel running while the file says it is off.
        sys.path.insert(0, str(ROOT / "config"))
        import validate

        (tmp_path / "sources.yaml").write_text(yaml.safe_dump({
            "window_months": 3,
            "labs": [{
                "id": "openai", "method": "rss", "index_url": "https://a.example/rss",
                "also": [{"method": "discourse", "enabled": "false",
                          "index_url": "https://f.example/c.json"}],
            }],
        }))
        errors = validate.check_sources(tmp_path)

        assert any("not a bool" in e for e in errors)


class TestWaybackBackfillSurvivesTheFetch:
    """The silent failure: a lab's corpus quietly reverting to feed summaries.

    This one really happened and nothing caught it. `backfill_openai.py` wrote
    141 recovered articles into announcements.json on 2026-09-02 (mean 9,105
    characters); `collect()` rewrites that file from scratch, so the next
    fetch a day later put every one of them back to a ~200-character RSS blurb.
    The register still had 251 articles, every citation still resolved, every
    test stayed green, and OpenAI -- 59% of the corpus -- was scored on its own
    meta descriptions.

    The reason no test saw it is that every assertion was about articles being
    *present*. None was about the text being worth reading. These are.
    """

    def _lab(self, **overrides):
        lab = {
            "id": "openai",
            "label": "OpenAI",
            "method": "rss",
            "index_url": "https://openai.com/blog/rss.xml",
            "text_source": "rss_summary",
            "backfill": "wayback",
        }
        lab.update(overrides)
        return lab

    def _summary(self, url, text="A headline. One sentence of summary."):
        return {"lab": "openai", "url": url, "date": "2026-08-01",
                "title": "A headline", "text": text, "text_source": "rss_summary"}

    def _archive(self, pages, rows):
        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                return _cdx_rows(*rows)
            return pages[url.split("id_/", 1)[1]]
        return fake_fetch

    def test_a_summary_is_upgraded_to_archived_full_text(self, monkeypatch):
        article = self._summary("https://openai.com/index/jalapeno-first-results")
        body = "<html><body><p>" + ("Measured results. " * 200) + "</p></body></html>"
        monkeypatch.setattr("fetch_announcements.fetch_wayback", self._archive(
            {"https://openai.com/index/jalapeno-first-results": body},
            [("https://openai.com/index/jalapeno-first-results", "20260902120000")]))

        missed = fetch_announcements.enrich_wayback(
            self._lab(), [article], datetime(2026, 6, 1))

        assert missed == []
        assert article["text_source"] == "full_text_archived"
        assert len(article["text"]) > 3000
        assert "Measured results." in article["text"]
        assert article["archive_snapshot"].startswith(
            "http://web.archive.org/web/20260902120000id_/")

    def test_the_live_url_is_kept_as_the_citation(self, monkeypatch):
        """The snapshot is how the text was reached; the article is still the
        article. Rewriting `url` would cite our own plumbing."""
        article = self._summary("https://openai.com/index/gpt-6-astra")
        monkeypatch.setattr("fetch_announcements.fetch_wayback", self._archive(
            {"https://openai.com/index/gpt-6-astra": "<p>" + "x " * 500 + "</p>"},
            [("https://openai.com/index/gpt-6-astra", "20260902120000")]))

        fetch_announcements.enrich_wayback(self._lab(), [article], datetime(2026, 6, 1))

        assert article["url"] == "https://openai.com/index/gpt-6-astra"

    def test_an_unarchived_article_is_reported_not_silently_left(self, monkeypatch):
        """The 13% the archive has not crawled yet. Staying on a summary is
        acceptable; staying on one with nobody told is how this went unnoticed
        for two days."""
        article = self._summary("https://openai.com/index/published-yesterday")
        monkeypatch.setattr("fetch_announcements.fetch_wayback",
                            self._archive({}, []))

        missed = fetch_announcements.enrich_wayback(
            self._lab(), [article], datetime(2026, 6, 1))

        assert len(missed) == 1
        assert missed[0]["name"] == "https://openai.com/index/published-yesterday"
        assert "summary" in missed[0]["reason"]
        assert article["text_source"] == "rss_summary"

    def test_a_stub_snapshot_never_replaces_a_longer_summary(self, monkeypatch):
        """A redirect stub or an error page archives with status 200. Shorter
        than what we hold is never an improvement."""
        article = self._summary(
            "https://openai.com/index/redirected",
            text="A headline. " + "A genuinely long RSS summary sentence. " * 10)
        before = article["text"]
        monkeypatch.setattr("fetch_announcements.fetch_wayback", self._archive(
            {"https://openai.com/index/redirected": "<p>Redirecting...</p>"},
            [("https://openai.com/index/redirected", "20260902120000")]))

        missed = fetch_announcements.enrich_wayback(
            self._lab(), [article], datetime(2026, 6, 1))

        assert article["text"] == before
        assert article["text_source"] == "rss_summary"
        assert "no longer than" in missed[0]["reason"]

    def test_already_recovered_articles_are_not_refetched(self, monkeypatch):
        """`full_text_archived` is not a target. Without this the leg re-walks
        the whole corpus at two seconds a page on every firing -- the shape of
        the arXiv outage the papers leg just had."""
        done = {"lab": "openai", "url": "https://openai.com/index/done",
                "date": "2026-08-01", "title": "Done",
                "text": "x" * 9000, "text_source": "full_text_archived"}
        calls = []

        def fake_fetch(url, **kw):
            calls.append(url)
            return _cdx_rows()

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        assert fetch_announcements.enrich_wayback(
            self._lab(), [done], datetime(2026, 6, 1)) == []
        assert calls == []  # not even the bulk CDX query

    def test_a_second_path_is_covered_without_a_config_change(self, monkeypatch):
        """`backfill_openai.py` queried `openai.com/index*` only, so an
        `openai.com/academy/*` article could never be recovered however long
        the archive held it. Patterns come from the URLs themselves."""
        wildcard = []
        pages = {"https://openai.com/academy/getting-started": "<p>" + "y " * 500 + "</p>"}

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                if "%2A" in url or "*" in url:
                    wildcard.append(url)
                    return _cdx_rows(
                        ("https://openai.com/academy/getting-started", "20260902120000"))
                return _cdx_rows()  # the exact-lookup fallback finds nothing
            return pages[url.split("id_/", 1)[1]]

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        articles = [self._summary("https://openai.com/index/a"),
                    self._summary("https://openai.com/academy/getting-started")]
        fetch_announcements.enrich_wayback(self._lab(), articles, datetime(2026, 6, 1))

        assert len(wildcard) == 2, "one bulk query per distinct path prefix"
        assert any("academy" in q for q in wildcard)
        assert articles[1]["text_source"] == "full_text_archived"

    def test_an_exact_lookup_catches_what_the_wildcard_index_misses(self, monkeypatch):
        """The archive's wildcard index lags its exact one. Measured live:
        `path-to-astra` has a 2026-09-03 snapshot that `openai.com/index*`
        does not return at any date bound or row limit. Trusting the bulk
        query alone leaves half a frontier launch on a 221-character blurb and
        calls it "not archived yet" -- a wrong answer wearing a correct one's
        clothes."""
        article = self._summary("https://openai.com/index/path-to-astra")
        body = "<p>" + ("Frontier capability thresholds. " * 200) + "</p>"

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                if "%2A" in url or "*" in url:
                    return _cdx_rows()  # the wildcard index does not have it
                return _cdx_rows(
                    ("https://openai.com/index/path-to-astra/", "20260903190243"))
            return body

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        missed = fetch_announcements.enrich_wayback(
            self._lab(), [article], datetime(2026, 6, 1))

        assert missed == []
        assert article["text_source"] == "full_text_archived"
        assert article["archive_snapshot"].startswith(
            "http://web.archive.org/web/20260903190243id_/")

    def test_the_exact_lookup_runs_only_for_genuine_misses(self, monkeypatch):
        """One extra request per miss, not per article. 149 exact lookups a
        night to re-confirm what the bulk query already answered is the
        request storm this leg must not become."""
        hit = self._summary("https://openai.com/index/found")
        exact = []

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                if "%2A" in url or "*" in url:
                    return _cdx_rows(("https://openai.com/index/found", "20260902120000"))
                exact.append(url)
                return _cdx_rows()
            return "<p>" + "w " * 500 + "</p>"

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        fetch_announcements.enrich_wayback(self._lab(), [hit], datetime(2026, 6, 1))

        assert exact == []

    def test_a_trailing_slash_still_matches_its_snapshot(self, monkeypatch):
        """The archive's `original` column and our register disagree on scheme,
        `www.` and trailing slash for the same page. Comparing them raw
        recovers nothing and looks exactly like a corpus the archive lacks."""
        article = self._summary("https://openai.com/index/a-scorecard-for-the-ai-age")
        monkeypatch.setattr("fetch_announcements.fetch_wayback", self._archive(
            {"https://openai.com/index/a-scorecard-for-the-ai-age":
                "<p>" + "z " * 500 + "</p>"},
            [("http://www.openai.com/index/a-scorecard-for-the-ai-age/",
              "20260902120000")]))

        fetch_announcements.enrich_wayback(self._lab(), [article], datetime(2026, 6, 1))

        assert article["text_source"] == "full_text_archived"


class TestTheConfigKeyIsRead:
    """`backfill: wayback` sat in sources.yaml for two days, fully documented,
    read by nothing. Config that describes a step nobody runs is worse than no
    config: it reads as wired up."""

    def test_openai_declares_the_backfill(self):
        config = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
        openai = [l for l in config["labs"] if l["id"] == "openai"][0]
        assert openai.get("backfill") == "wayback"

    def test_collect_runs_the_backfill_for_a_lab_that_declares_it(self, monkeypatch):
        """The regression test proper: the file `collect()` writes must already
        contain the recovered text, because anything done to it afterwards is
        erased by the next fetch."""
        lab = {"id": "openai", "label": "OpenAI", "method": "rss",
               "index_url": "https://openai.com/blog/rss.xml",
               "text_source": "rss_summary", "backfill": "wayback"}
        summary = {"lab": "openai", "url": "https://openai.com/index/x",
                   "date": "2026-08-01", "title": "X",
                   "text": "X. A summary.", "text_source": "rss_summary"}

        # Patched on the module under test, not on the shared `yaml` object,
        # which would swap safe_load for every importer for the test's duration.
        monkeypatch.setattr(fetch_announcements.yaml, "safe_load",
                            lambda *_a, **_k: {"window_months": 3, "labs": [lab]})
        monkeypatch.setattr("fetch_announcements.METHODS",
                            {"rss": lambda ch, cutoff, skip=None: [dict(summary)]})

        def fake_enrich(lab_arg, articles, cutoff):
            for a in articles:
                a["text"] = "recovered full text " * 100
                a["text_source"] = "full_text_archived"
            return []

        monkeypatch.setattr("fetch_announcements.enrich_wayback", fake_enrich)

        out = fetch_announcements.collect()

        assert [a["text_source"] for a in out] == ["full_text_archived"]

    def test_a_lab_without_the_key_is_left_alone(self, monkeypatch):
        """Anthropic fetches its own full text; a needless archive walk would
        cost two seconds a page for nothing."""
        lab = {"id": "anthropic", "label": "Anthropic", "method": "rss",
               "index_url": "https://www.anthropic.com/rss.xml",
               "text_source": "full_text"}
        article = {"lab": "anthropic", "url": "https://www.anthropic.com/news/a",
                   "date": "2026-08-01", "title": "A", "text": "x" * 9000,
                   "text_source": "full_text"}

        # Patched on the module under test, not on the shared `yaml` object,
        # which would swap safe_load for every importer for the test's duration.
        monkeypatch.setattr(fetch_announcements.yaml, "safe_load",
                            lambda *_a, **_k: {"window_months": 3, "labs": [lab]})
        monkeypatch.setattr("fetch_announcements.METHODS",
                            {"rss": lambda ch, cutoff, skip=None: [dict(article)]})

        called = []
        monkeypatch.setattr("fetch_announcements.enrich_wayback",
                            lambda *a, **k: called.append(1) or [])

        fetch_announcements.collect()

        assert called == []


class TestTheArchiveIsQueriedCorrectly:
    """Three ways to ask the archive a question and get a confidently wrong
    answer back. None of them raises, and all three were live in the first
    version of this leg."""

    def _lab(self):
        return {"id": "openai", "label": "OpenAI", "method": "rss",
                "index_url": "https://openai.com/blog/rss.xml",
                "text_source": "rss_summary", "backfill": "wayback"}

    def _summary(self, url="https://openai.com/index/x"):
        return {"lab": "openai", "url": url, "date": "2026-08-01",
                "title": "X", "text": "X. A summary.", "text_source": "rss_summary"}

    def test_the_exact_lookup_asks_for_the_newest_snapshots(self, monkeypatch):
        """CDX returns rows oldest-first, so `limit=5` is the first five
        snapshots ever taken, not the last. Measured on
        `openai.com/index/introducing-gpt-5` (309 snapshots): `limit=5`
        returns August 2025, `limit=-5` returns August 2026. Taking the oldest
        is not just stale -- the earliest crawls are the ones most likely to
        have caught a consent wall, and a nav shell clears the
        longer-than-the-summary guard easily, so chrome gets stored as
        `full_text_archived` and quoted from."""
        seen = []

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                if "%2A" in url or "*" in url:
                    return _cdx_rows()
                seen.append(url)
                return _cdx_rows(("https://openai.com/index/x", "20260101000000"))
            return "<p>" + "q " * 500 + "</p>"

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        fetch_announcements.enrich_wayback(
            self._lab(), [self._summary()], datetime(2026, 6, 1))

        assert seen, "no exact lookup was made"
        assert "limit=-5" in seen[0], f"asked for the oldest snapshots: {seen[0]}"

    def test_the_bulk_query_takes_the_newest_snapshot_of_each_article(self, monkeypatch):
        """`collapse=urlkey` is the obvious way to stop the row count growing
        and it is wrong here: it keeps the FIRST row of each group, and CDX
        returns rows oldest-first, so every article would resolve to its
        earliest crawl. Confirmed live -- with collapse,
        jalapeno-first-results resolved to a 2026-08-25 snapshot instead of
        the 2026-08-29 one. The bulk path must agree with `_exact_snapshot`,
        which asks for the newest explicitly."""
        article = self._summary("https://openai.com/index/x")

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                if "%2A" in url or "*" in url:
                    assert "collapse=urlkey" not in url, (
                        "collapse keeps the oldest snapshot of each article")
                    return _cdx_rows(
                        ("https://openai.com/index/x", "20260825000000"),
                        ("https://openai.com/index/x", "20260829000000"),
                        ("https://openai.com/index/x", "20260101000000"),
                    )
                return _cdx_rows()
            return "<p>" + "s " * 500 + "</p>"

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        fetch_announcements.enrich_wayback(
            self._lab(), [article], datetime(2026, 6, 1))

        assert article["archive_snapshot"].startswith(
            "http://web.archive.org/web/20260829000000id_/")

    def test_a_truncated_bulk_response_says_so(self, monkeypatch, capsys):
        """Truncation is indistinguishable from absence at the API. Silence
        here reads as "the archive does not have these"."""
        rows = [("https://openai.com/index/a%d" % i, "20260801000000")
                for i in range(fetch_announcements.CDX_ROW_LIMIT)]

        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                return _cdx_rows(*rows) if ("%2A" in url or "*" in url) else _cdx_rows()
            return ""

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        fetch_announcements.enrich_wayback(
            self._lab(), [self._summary()], datetime(2026, 6, 1))

        assert "truncated" in capsys.readouterr().out

    def test_two_queries_differing_only_in_punctuation_do_not_share_a_cache_file(self):
        """The readable cache key collapses punctuation and truncates, so
        `...&limit=5` and `...&limit=-5` both render as `_limit_5`. That
        silently served the stale response while the snapshot ordering above
        was being fixed -- the fix appeared to do nothing."""
        import re as _re

        def key(url):
            return ("wb_" + _re.sub(r"[^a-zA-Z0-9]+", "_", url)[:120]
                    + "_" + __import__("hashlib").sha1(url.encode()).hexdigest()[:10])

        a = "http://web.archive.org/cdx/search/cdx?url=x&limit=5"
        b = "http://web.archive.org/cdx/search/cdx?url=x&limit=-5"
        assert key(a) != key(b)


class TestAPartialArchiveResponse:
    """Observed live, not imagined: the archive closed the connection mid-array
    on `openai.com/index*` -- a 114,899-byte body ending `...],` with no
    closing bracket, where the same query a minute later returned 141,565 bytes
    and parsed cleanly. `json.loads` raises on that."""

    def _lab(self):
        return {"id": "openai", "label": "OpenAI", "method": "rss",
                "index_url": "https://openai.com/blog/rss.xml",
                "text_source": "rss_summary", "backfill": "wayback"}

    def _summary(self, url="https://openai.com/index/x"):
        return {"lab": "openai", "url": url, "date": "2026-08-01",
                "title": "X", "text": "X. A summary.", "text_source": "rss_summary"}

    def test_a_truncated_bulk_body_does_not_abort_the_fetch(self, monkeypatch):
        """In `collect()` an uncaught raise here loses all seven labs' articles
        over one flaky read on one of them."""
        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                if "%2A" in url or "*" in url:
                    return '[["original","timestamp"],\n["https://a/","2026"],'
                return _cdx_rows()
            return ""

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        missed = fetch_announcements.enrich_wayback(
            self._lab(), [self._summary()], datetime(2026, 6, 1))

        assert len(missed) == 1  # reported, not raised

    def test_articles_fall_through_to_an_exact_lookup(self, monkeypatch):
        """A dead bulk query must degrade to slower-and-correct, not to
        absent. Otherwise one partial response reads as "the archive has
        nothing" for every article in that prefix."""
        def fake_fetch(url, **kw):
            if "cdx/search/cdx" in url:
                if "%2A" in url or "*" in url:
                    return "not json at all"
                return _cdx_rows(("https://openai.com/index/x", "20260902120000"))
            return "<p>" + "r " * 500 + "</p>"

        monkeypatch.setattr("fetch_announcements.fetch_wayback", fake_fetch)
        article = self._summary()
        missed = fetch_announcements.enrich_wayback(
            self._lab(), [article], datetime(2026, 6, 1))

        assert missed == []
        assert article["text_source"] == "full_text_archived"

    def test_the_unusable_body_is_dropped_from_the_cache(self, monkeypatch, tmp_path):
        """`fetch_wayback` caches before anything validates the body, so a
        partial read would otherwise be replayed for the whole discovery TTL.
        One bad second becomes six bad hours."""
        monkeypatch.setattr(fetch_announcements, "CACHE", tmp_path)
        query = "http://web.archive.org/cdx/search/cdx?url=x&output=json"
        path = fetch_announcements._wayback_cache_path(query)
        path.write_text('[["original","timestamp"],')

        monkeypatch.setattr("fetch_announcements.fetch_wayback",
                            lambda url, **kw: path.read_text())

        assert fetch_announcements._cdx_json(query) is None
        assert not path.exists()

    def test_an_archive_outage_is_reported_not_raised(self, monkeypatch):
        def boom(url, **kw):
            raise RuntimeError("wayback fetch failed: 503")

        monkeypatch.setattr("fetch_announcements.fetch_wayback", boom)
        missed = fetch_announcements.enrich_wayback(
            self._lab(), [self._summary()], datetime(2026, 6, 1))

        assert len(missed) == 1


class TestThePromptFileIsNotTheSystemPrompt:
    """A prompt file has two audiences and only one should see all of it.

    v9 was written as a copy of v8 plus a fifteen-line comment explaining why
    the version existed. `build_prompt` sent the file verbatim, so all 259 v9
    calls were told "OpenAI's articles were ~200-character RSS summaries and
    are now archived full text" -- a leading claim about the input, pointing in
    exactly the direction the scores then moved. Nothing caught it: the tests
    checked that a prompt file *existed* and that the version matched, never
    what the model was actually handed.
    """

    def test_a_comment_never_reaches_the_model(self):
        from score_announcements import strip_comments

        raw = "# Title\n\n<!--\na note for the reader\n-->\nReal instructions.\n"
        out = strip_comments(raw)
        assert "a note for the reader" not in out
        assert "Real instructions." in out

    def test_several_comments_are_all_removed(self):
        from score_announcements import strip_comments

        raw = "A\n<!-- one -->\nB\n<!-- two -->\nC"
        out = strip_comments(raw)
        assert "one" not in out and "two" not in out
        assert "A" in out and "B" in out and "C" in out

    def test_v9_sends_the_same_body_as_v8(self):
        """The claim D56 rests on, checked where it is actually true -- on what
        is sent, not on the files. The two files differ by 686 characters."""
        from score_announcements import strip_comments

        d = ROOT / "prompts" / "announcement_scoring"
        v8 = strip_comments(d.joinpath("v8.md").read_text()).split("\n", 1)[1]
        v9 = strip_comments(d.joinpath("v9.md").read_text()).split("\n", 1)[1]
        assert v8 == v9, "v9 must ask v8's question; only the title may differ"

    def test_build_prompt_itself_does_not_ship_the_comment(self):
        """The wiring, not the helper. `strip_comments` passing its own unit
        test proves nothing if `build_prompt` never calls it -- which is
        precisely the shape of the original defect, where the file was read
        verbatim one line away from a function that would have cleaned it."""
        from score_announcements import build_prompt

        system, _user = build_prompt({
            "lab": "openai", "date": "2026-09-01",
            "url": "https://openai.com/index/x", "title": "X",
            "text": "Some article text.", "text_source": "full_text",
        })
        assert "PROVENANCE WARNING" not in system
        assert "archived full text" not in system
        assert "<!--" not in system
        # Still the real prompt, not an empty string.
        assert "transmission mechanisms" in system

    def test_the_files_themselves_are_not_identical(self):
        """Guards the guard. If someone deletes v9's rationale to make the
        check above pass trivially, the check stops proving anything -- and
        v9.md carries the provenance warning about the contaminated run, which
        must not quietly disappear."""
        d = ROOT / "prompts" / "announcement_scoring"
        assert d.joinpath("v8.md").read_text() != d.joinpath("v9.md").read_text()
        assert "PROVENANCE WARNING" in d.joinpath("v9.md").read_text()


class TestOnePromptVersionConstant:
    """Two constants that had to agree by hand. The drift is silent and
    recurring: the scorer writes into its own cache directory, the app looks in
    a different one, finds nothing, writes no classifications, `connect` then
    deletes every connection row and rebuilds none -- and the next firing pays
    for the identical pending set again."""

    def test_the_scorer_takes_the_app_s_version(self):
        import score_announcements
        from app.cli import PROMPT_VERSION

        assert score_announcements.PROMPT_VERSION == PROMPT_VERSION

    def test_the_scorer_does_not_declare_its_own(self):
        """Equality today is not the property; being unable to disagree is."""
        import re as _re

        src = (ROOT / "research" / "announcements"
               / "score_announcements.py").read_text()
        # Line-anchored: the module legitimately contains the string
        # `PROMPT_VERSION = "` inside the regex it uses to read app/cli.py.
        assert not _re.search(r'^PROMPT_VERSION = "', src, _re.M), (
            "a literal assignment reintroduces the two-constant drift")

    def test_the_prompt_file_for_the_current_version_exists(self):
        from app.cli import PROMPT_VERSION

        path = ROOT / "prompts" / "announcement_scoring" / f"{PROMPT_VERSION}.md"
        assert path.exists(), (
            f"PROMPT_VERSION is {PROMPT_VERSION} but {path.name} is missing -- "
            "every call would fail at read time")


class TestDeepMindDiscoveryChannel:
    """The channel that silently under-covered Google DeepMind (D63).

    `method: sitemap` on deepmind.google/sitemap.xml looked healthy from every
    angle the pipeline could see: the fetch returned 200, the parse succeeded,
    the dates were real and every article it did return was genuinely in
    window. It was simply reading a document that does not enumerate the blog,
    and 18 of 30 in-window articles never existed as far as the register was
    concerned -- among them Gemini 3.6, 3.7 and 3.8 Flash, Gemma 4 and
    DiffusionGemma, which is the single highest-signal category this product
    has.

    No mock can catch that: a stubbed sitemap returns exactly the URLs the test
    author put in it, so a unit test of `from_sitemap` passes just as happily
    against a document listing nothing. These tests therefore pin the two
    things that are checkable offline -- the configured channel, and the
    coverage actually present in the committed register.
    """

    def test_discovery_is_the_blog_feed_not_the_site_sitemap(self, deepmind):
        assert deepmind["method"] == "rss"
        assert deepmind["index_url"] == "https://deepmind.google/blog/rss.xml"

    def test_the_date_comes_from_the_feed_not_the_page(self, deepmind):
        """Page-read dates drifted: 06-18 against the feed's 06-16 for
        "Securing the future of AI agents", because the first "Month D, YYYY"
        in a stripped DeepMind page is not reliably the article's own."""
        assert deepmind["date_from"] == "feed"

    def test_coverage_has_not_fallen_back_to_sitemap_depth(self, register):
        """The floor that fails if discovery silently narrows again.

        The sitemap yielded 12 articles where the feed yields 30 over the same
        window. 20 sits below the observed feed depth -- DeepMind's cadence
        varies and this must not fail on a quiet month -- and well above the
        12 the broken channel produced.
        """
        found = [a for a in register if a["lab"] == "google-deepmind"]
        assert len(found) >= 20, (
            f"DeepMind coverage is {len(found)} articles; the sitemap channel "
            "that D63 replaced produced 12. Check the feed still enumerates "
            "the blog before lowering this floor.")

    def test_the_model_launches_are_in_the_register(self, register):
        """The 18 missed articles were not a random sample of the blog.

        What the sitemap did carry was the education, policy and programme
        posts, so a count alone could be met while still missing every launch.
        """
        titles = " ".join(a["title"].lower() for a in register
                          if a["lab"] == "google-deepmind")
        for launch in ("gemini 3.6", "gemini 3.7", "gemini 3.8", "gemma 4"):
            assert launch in titles, f"{launch} missing from DeepMind coverage"

    def test_every_deepmind_article_carries_the_text_it_claims(self, register):
        """`text_source` must describe what was read, never what was asked
        for: a score computed from a two-line summary must never be compared
        against one computed from a full article."""
        for a in (a for a in register if a["lab"] == "google-deepmind"):
            if a["text_source"] == "full_text":
                assert len(a["text"]) > 1000, f"thin full_text: {a['url']}"
            else:
                assert a["text_source"] == "rss_summary", a["text_source"]


class TestFromRssPaging:
    """Paged feeds, added for about.fb.com (D63), which serves ten items a page.

    The silent failures these catch: a page-2 request that quietly returns
    page 1 again and doubles every article; a feed shorter than its configured
    page count raising instead of stopping; and the user-agent regression that
    paging nearly introduced -- threading a UA through `fetch` turns an absent
    `user_agent` key into "send no override", which is not what the RSS labs
    configured before this relied on.
    """

    def _paged(self, pages):
        """A fake fetch serving a different single-item feed per page."""
        def fake_fetch(url, **kw):
            if "?" in url:
                n = int(url.split("=")[-1])
            elif url.startswith("https://feed.example/rss"):
                n = 1
            else:
                return "<html><body><p>body</p></body></html>"
            return pages.get(n, "<rss><channel></channel></rss>")
        return fake_fetch

    def test_one_page_by_default(self, monkeypatch):
        seen = []

        def fake_fetch(url, **kw):
            seen.append(url)
            return _rss_xml()

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        lab = {"id": "mistral", "index_url": "https://feed.example/rss",
               "text_source": "rss_summary"}
        from_rss(lab, datetime(2025, 1, 1))
        assert seen == ["https://feed.example/rss"]

    def test_feed_pages_reads_each_page_and_keeps_every_item(self, monkeypatch):
        pages = {
            1: _rss_xml(title="One", link="https://x.example/1"),
            2: _rss_xml(title="Two", link="https://x.example/2"),
            3: _rss_xml(title="Three", link="https://x.example/3"),
        }
        monkeypatch.setattr(fetch_announcements, "fetch", self._paged(pages))
        lab = {"id": "meta-ai", "index_url": "https://feed.example/rss",
               "text_source": "rss_summary", "feed_pages": 3}
        out = from_rss(lab, datetime(2025, 1, 1))
        assert [a["title"] for a in out] == ["One", "Two", "Three"]

    def test_the_page_param_is_configurable(self, monkeypatch):
        seen = []

        def fake_fetch(url, **kw):
            seen.append(url)
            return _rss_xml(link=f"https://x.example/{len(seen)}")

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        lab = {"id": "meta-ai", "index_url": "https://feed.example/rss",
               "text_source": "rss_summary", "feed_pages": 2,
               "feed_page_param": "paged"}
        from_rss(lab, datetime(2025, 1, 1))
        assert seen[1] == "https://feed.example/rss?paged=2"

    def test_an_item_repeated_across_pages_is_stored_once(self, monkeypatch):
        """A feed repaginates as new posts land, so the same item can appear on
        two pages of one sweep."""
        same = _rss_xml(title="Dup", link="https://x.example/same")
        monkeypatch.setattr(fetch_announcements, "fetch",
                            self._paged({1: same, 2: same}))
        lab = {"id": "meta-ai", "index_url": "https://feed.example/rss",
               "text_source": "rss_summary", "feed_pages": 2}
        out = from_rss(lab, datetime(2025, 1, 1))
        assert len(out) == 1

    def test_a_feed_shorter_than_its_page_count_stops_rather_than_failing(self, monkeypatch):
        monkeypatch.setattr(fetch_announcements, "fetch", self._paged(
            {1: _rss_xml(link="https://x.example/1")}))
        lab = {"id": "meta-ai", "index_url": "https://feed.example/rss",
               "text_source": "rss_summary", "feed_pages": 5}
        out = from_rss(lab, datetime(2025, 1, 1))
        assert len(out) == 1

    def test_a_failing_page_keeps_what_earlier_pages_found(self, monkeypatch):
        def fake_fetch(url, **kw):
            if url.endswith("=2"):
                raise RuntimeError("fetch failed: 503")
            return _rss_xml(link="https://x.example/1")

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        lab = {"id": "meta-ai", "index_url": "https://feed.example/rss",
               "text_source": "rss_summary", "feed_pages": 3}
        out = from_rss(lab, datetime(2025, 1, 1))
        assert len(out) == 1

    def test_an_absent_user_agent_key_keeps_the_module_default(self, monkeypatch):
        """The regression paging nearly introduced: every RSS lab configured
        before D63 relied on `fetch`'s own default browser agent."""
        seen = {}

        def fake_fetch(url, **kw):
            seen[url] = kw.get("user_agent", "NOT PASSED")
            return _rss_xml()

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        lab = {"id": "mistral", "index_url": "https://feed.example/rss",
               "text_source": "rss_summary"}
        from_rss(lab, datetime(2025, 1, 1))
        assert seen["https://feed.example/rss"] == fetch_announcements.UA

    def test_an_explicit_empty_user_agent_sends_no_override(self, monkeypatch):
        """ai.meta.com 400s on any browser-shaped agent; its newsroom channel
        inherits the cleared key, and the article fetch needs it too."""
        seen = {}

        def fake_fetch(url, **kw):
            seen[url] = kw.get("user_agent", "NOT PASSED")
            if url == "https://feed.example/rss":
                return _rss_xml(link="https://x.example/a")
            return "<html><body><p>body</p></body></html>"

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        lab = {"id": "meta-ai", "index_url": "https://feed.example/rss",
               "text_source": "full_text", "user_agent": ""}
        from_rss(lab, datetime(2025, 1, 1))
        assert seen["https://feed.example/rss"] is None
        assert seen["https://x.example/a"] is None


class TestFromRssTitleEncoding:
    """about.fb.com serves numeric entities where every feed configured before
    it served real UTF-8, so an unescaped title stored `Meta&#8217;s AI`
    verbatim. The title is what dedupe's exact gate matches on and what it
    embeds, so this is the same class of fault `strip_html` unescapes twice to
    avoid -- there it cost 9 of 90 verbatim quote checks.
    """

    def test_numeric_entities_are_decoded(self, monkeypatch):
        monkeypatch.setattr(fetch_announcements, "fetch",
                            lambda url, **kw: _rss_xml(title="Meta&#8217;s AI"))
        lab = {"id": "meta-ai", "index_url": "https://feed.example/rss",
               "text_source": "rss_summary"}
        out = from_rss(lab, datetime(2025, 1, 1))
        assert out[0]["title"] == "Meta’s AI"

    def test_double_encoded_entities_are_decoded(self, monkeypatch):
        monkeypatch.setattr(fetch_announcements, "fetch",
                            lambda url, **kw: _rss_xml(title="Meta&amp;#8217;s AI"))
        lab = {"id": "meta-ai", "index_url": "https://feed.example/rss",
               "text_source": "rss_summary"}
        out = from_rss(lab, datetime(2025, 1, 1))
        assert out[0]["title"] == "Meta’s AI"

    def test_a_plain_utf8_title_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(fetch_announcements, "fetch",
                            lambda url, **kw: _rss_xml(title="Gemini’s guided learning"))
        lab = {"id": "google-deepmind", "index_url": "https://feed.example/rss",
               "text_source": "rss_summary"}
        out = from_rss(lab, datetime(2025, 1, 1))
        assert out[0]["title"] == "Gemini’s guided learning"
