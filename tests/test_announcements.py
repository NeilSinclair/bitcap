"""Tests for announcement fetching and scoring.

The silent failures this suite exists to catch: a hallucinated mechanism id
reaching the register as though it were a real transmission path, a scoring
rule that quietly stops distinguishing signal from noise, and a date parser
that places an old release inside the window.
"""

import json
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
    from_wayback_cdx,
    strip_html,
)


@pytest.fixture(scope="module")
def rules():
    return yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())


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

        def fake_fetch(url):
            calls["index" if url == "https://feed.example/rss" else "article"] += 1
            return _rss_xml()

        monkeypatch.setattr(fetch_announcements, "fetch", fake_fetch)
        lab = {"id": "openai", "index_url": "https://feed.example/rss", "text_source": "rss_summary"}
        out = from_rss(lab, datetime(2025, 1, 1))

        assert calls["article"] == 0
        assert out[0]["text_source"] == "rss_summary"
        assert out[0]["text"] == "Example headline. A short summary."

    def test_full_text_fetches_and_strips_the_article_page(self, monkeypatch):
        def fake_fetch(url):
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
        def fake_fetch(url):
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
            "fetch_announcements.fetch", lambda url, user_agent=None: pages[url]
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
            "fetch_announcements.fetch", lambda url, user_agent=None: pages[url]
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
            "fetch_announcements.fetch", lambda url, user_agent=None: pages[url]
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
            "fetch_announcements.fetch", lambda url, user_agent=None: pages[url]
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
            "fetch_announcements.fetch", lambda url, user_agent=None: pages[url]
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
            "fetch_announcements.fetch", lambda url, user_agent=None: pages.get(url, "")
        )
        out = from_listing_pagination(self._lab(), datetime(2026, 1, 1))
        assert [a["url"] for a in out] == ["https://ai.example/blog/real/"]

    def test_max_pages_is_a_hard_cap(self, monkeypatch):
        # Every page yields exactly one new, in-window article -- without a
        # cap this listing would paginate forever.
        def fake_fetch(url, user_agent=None):
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
        def fake_fetch(url, user_agent=None):
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

        def fake_fetch(url, user_agent=None):
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

        def fake_fetch(url):
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

        def fake_fetch(url):
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

        def fake_fetch(url):
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

        def fake_fetch(url):
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

        def fake_fetch(url):
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

        def fake_fetch(url):
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

        def fake_fetch(url):
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
