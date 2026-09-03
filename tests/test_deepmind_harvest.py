"""Tests for the Google DeepMind papers harvest.

The silent failures this suite exists to catch: a `<lastmod>` window filter
that includes a stale entry (or drops a fresh one) because the regex doesn't
actually anchor to the DeepMind publications URL shape, an arXiv link
resolved to the wrong id, and an extraction failure that aborts the whole
run instead of being recorded and skipped.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "papers"))

import deepmind_harvest as dh
from deepmind_harvest import Paper, aggregate, detail_page_info


def _sitemap(entries):
    urls = "".join(
        f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>" for loc, lastmod in entries
    )
    return f"<urlset>{urls}</urlset>"


class TestListPublicationUrls:
    def test_filters_to_window_and_url_shape(self, monkeypatch):
        monkeypatch.setattr(
            dh,
            "fetch",
            lambda url, pause=1.5: _sitemap(
                [
                    ("https://deepmind.google/research/publications/1/", "2026-08-20"),
                    ("https://deepmind.google/research/publications/2/", "2025-01-01"),  # too old
                    ("https://deepmind.google/blog/some-post/", "2026-08-20"),  # not a publication
                ]
            ),
        )
        out = dh.list_publication_urls(months=3)
        assert [u for u, _ in out] == ["https://deepmind.google/research/publications/1/"]

    def test_sorted_most_recent_first(self, monkeypatch):
        monkeypatch.setattr(
            dh,
            "fetch",
            lambda url, pause=1.5: _sitemap(
                [
                    ("https://deepmind.google/research/publications/1/", "2026-07-01"),
                    ("https://deepmind.google/research/publications/2/", "2026-08-01"),
                ]
            ),
        )
        out = dh.list_publication_urls(months=6)
        assert [u for u, _ in out] == [
            "https://deepmind.google/research/publications/2/",
            "https://deepmind.google/research/publications/1/",
        ]


class TestDetailPageInfo:
    def test_title_read_from_title_tag_and_site_suffix_stripped(self):
        page = "<html><title>My Paper Title — Google DeepMind</title><body></body></html>"
        assert detail_page_info(page)["title"] == "My Paper Title"

    def test_title_tag_with_attributes_still_matches(self):
        # Confirmed live on Meta AI's pages (<title id="pageTitle">): an
        # exact `<title>` match silently finds nothing when the tag carries
        # any attribute, which is worse than the bug it would replace --
        # DeepMind's own pages happen to have no attributes, untested here
        # until this regression test.
        page = '<html><title id="pageTitle">My Paper Title</title><body></body></html>'
        assert detail_page_info(page)["title"] == "My Paper Title"

    def test_quoted_arxiv_link_resolved_to_html_rendering(self):
        page = (
            "<html><title>T — Google DeepMind</title>"
            "<body>June 26, 2026 "
            '<a href="https://arxiv.org/abs/2506.21718">arXiv</a></body></html>'
        )
        info = detail_page_info(page)
        assert info["source_url"] == "https://arxiv.org/html/2506.21718"
        assert info["date"] == "2026-06-26"

    def test_unquoted_href_is_matched(self):
        # Confirmed live: deepmind.google renders href without quotes
        # whenever the value has no whitespace (valid HTML5). A quote-only
        # regex silently matches nothing here -- this is the bug the first
        # live proving run caught (0 authors on 3/3 papers).
        page = (
            "<html><title>T — Google DeepMind</title><body>"
            "<a href=https://arxiv.org/abs/2608.25924 target=_blank>arXiv</a>"
            "</body></html>"
        )
        assert detail_page_info(page)["source_url"] == "https://arxiv.org/html/2608.25924"

    def test_arxiv_pdf_link_also_resolved(self):
        page = (
            '<html><title>T</title><body>'
            '<a href="https://arxiv.org/pdf/2509.20354v2">PDF</a></body></html>'
        )
        assert detail_page_info(page)["source_url"] == "https://arxiv.org/html/2509.20354"

    def test_jsonld_sameas_preferred_over_href_scraping(self):
        page = (
            '<html><title>T</title><body>'
            '<script type="application/ld+json">{"sameAs": "https://arxiv.org/abs/2608.25924"}'
            "</script>"
            '<a href=https://arxiv.org/abs/9999.99999 target=_blank>decoy</a>'
            "</body></html>"
        )
        assert detail_page_info(page)["source_url"] == "https://arxiv.org/html/2608.25924"

    def test_openreview_link_used_when_no_arxiv(self):
        page = (
            "<html><title>T</title><body>"
            '<a href="https://openreview.net/pdf?id=Mmi46Ytb1H">paper</a>'
            "</body></html>"
        )
        info = detail_page_info(page)
        assert info["source_url"] == "https://openreview.net/pdf?id=Mmi46Ytb1H"

    def test_no_external_link_falls_back_to_none(self):
        # Confirmed live: not every DeepMind publication links out at all.
        page = "<html><title>T</title><body>No links here.</body></html>"
        assert detail_page_info(page)["source_url"] is None


class TestAggregate:
    def _paper(self, title, authors, url="https://x/1"):
        return Paper(url=url, source_url=url, title=title, date="2026-01-01", authors=authors)

    def test_is_lab_staff_true_if_any_paper_says_so(self):
        papers = [
            self._paper("A", [{"name": "X", "is_lab_staff": False}], url="https://x/1"),
            self._paper("B", [{"name": "X", "is_lab_staff": True}], url="https://x/2"),
        ]
        rows = aggregate(papers)
        assert rows[0]["is_lab_staff"] is True

    def test_appearances_and_affiliations_accumulate(self):
        papers = [
            self._paper("A", [{"name": "X", "affiliations": ["Google"]}], url="https://x/1"),
            self._paper("B", [{"name": "X", "affiliations": ["Google DeepMind"]}], url="https://x/2"),
        ]
        rows = aggregate(papers)
        assert rows[0]["appearances"] == 2
        assert rows[0]["affiliations"] == ["Google", "Google DeepMind"]

    def test_sorted_most_appearances_first(self):
        papers = [
            self._paper("A", [{"name": "One"}, {"name": "Two"}], url="https://x/1"),
            self._paper("B", [{"name": "Two"}], url="https://x/2"),
        ]
        rows = aggregate(papers)
        assert [r["name"] for r in rows] == ["Two", "One"]


class TestFetchRetry:
    """A live run against arXiv's /html/ endpoint hit a transient 403
    mid-batch and crashed the whole run -- fetch() must retry with backoff
    before giving up, same as fetch_announcements.py's fetch().
    """

    def test_retries_then_succeeds(self, tmp_path, monkeypatch):
        import urllib.error

        monkeypatch.setattr(dh, "CACHE", tmp_path)
        monkeypatch.setattr(dh.time, "sleep", lambda s: None)

        calls = {"n": 0}

        class FakeResponse:
            def read(self):
                return b"ok body"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=60):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
            return FakeResponse()

        monkeypatch.setattr(dh.urllib.request, "urlopen", fake_urlopen)
        assert dh.fetch("https://arxiv.org/html/9999.99999") == "ok body"
        assert calls["n"] == 3

    def test_raises_runtime_error_after_exhausting_retries(self, tmp_path, monkeypatch):
        import urllib.error

        monkeypatch.setattr(dh, "CACHE", tmp_path)
        monkeypatch.setattr(dh.time, "sleep", lambda s: None)

        def always_fails(req, timeout=60):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

        monkeypatch.setattr(dh.urllib.request, "urlopen", always_fails)
        with pytest.raises(RuntimeError):
            dh.fetch("https://arxiv.org/html/9999.99999", retries=2)


class TestCollectResilience:
    """A single extraction failure must not abort papers collected so far."""

    def test_fetch_failure_is_skipped_not_raised(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dh, "CACHE", tmp_path)
        monkeypatch.setattr(dh, "COST", tmp_path / "cost.json")
        monkeypatch.setattr(dh, "EXTRACTION_CACHE", tmp_path / "extraction_cache.json")
        monkeypatch.setattr(
            dh,
            "list_publication_urls",
            lambda months: [("https://deepmind.google/research/publications/1/", "2026-08-01")],
        )

        def boom(url, pause=1.5, retries=3):
            raise RuntimeError(f"fetch failed: {url}: 403")

        monkeypatch.setattr(dh, "fetch", boom)

        papers = dh.collect(months=3, model="claude-sonnet-5")
        assert papers == []

    def test_extraction_failure_is_skipped_not_raised(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dh, "CACHE", tmp_path)
        monkeypatch.setattr(dh, "COST", tmp_path / "cost.json")
        monkeypatch.setattr(dh, "EXTRACTION_CACHE", tmp_path / "extraction_cache.json")
        monkeypatch.setattr(
            dh,
            "list_publication_urls",
            lambda months: [("https://deepmind.google/research/publications/1/", "2026-08-01")],
        )
        monkeypatch.setattr(dh, "fetch", lambda url, pause=1.5: "<html>page</html>")
        monkeypatch.setattr(
            dh,
            "detail_page_info",
            lambda html: {"title": "T", "date": "2026-08-01", "source_url": None},
        )

        import llm_byline

        def boom(*a, **kw):
            raise RuntimeError("refused")

        monkeypatch.setattr(llm_byline, "extract_page", boom)
        monkeypatch.setattr(llm_byline, "load_env", lambda: None)

        papers = dh.collect(months=3, model="claude-sonnet-5")
        assert papers == []

    def test_a_billed_but_unparseable_extraction_still_records_its_cost(self, monkeypatch, tmp_path):
        # Real gap found and fixed live (docs/decisions.md D17): a call that
        # was billed by the API but failed to parse must not lose its cost
        # record just because the extraction itself failed.
        cost_path = tmp_path / "cost.json"
        monkeypatch.setattr(dh, "CACHE", tmp_path)
        monkeypatch.setattr(dh, "COST", cost_path)
        monkeypatch.setattr(dh, "EXTRACTION_CACHE", tmp_path / "extraction_cache.json")
        monkeypatch.setattr(
            dh,
            "list_publication_urls",
            lambda months: [("https://deepmind.google/research/publications/1/", "2026-08-01")],
        )
        monkeypatch.setattr(dh, "fetch", lambda url, pause=1.5: "<html>page</html>")
        monkeypatch.setattr(
            dh,
            "detail_page_info",
            lambda html: {"title": "T", "date": "2026-08-01", "source_url": None},
        )

        import llm_byline

        def boom(*a, **kw):
            raise llm_byline.ExtractionError("malformed JSON", {"model": "claude-sonnet-5", "usd": 0.07})

        monkeypatch.setattr(llm_byline, "extract_page", boom)
        monkeypatch.setattr(llm_byline, "load_env", lambda: None)

        papers = dh.collect(months=3, model="claude-sonnet-5")
        assert papers == []
        recorded = json.loads(cost_path.read_text())
        assert recorded[0]["usd"] == 0.07
        assert recorded[0]["url"] == "https://deepmind.google/research/publications/1/"

    def test_cached_extraction_is_not_rebilled(self, monkeypatch, tmp_path):
        cost_path = tmp_path / "cost.json"
        cache_path = tmp_path / "extraction_cache.json"
        monkeypatch.setattr(dh, "CACHE", tmp_path)
        monkeypatch.setattr(dh, "COST", cost_path)
        monkeypatch.setattr(dh, "EXTRACTION_CACHE", cache_path)
        cache_path.write_text(json.dumps({
            "https://deepmind.google/research/publications/1/": {
                "authors": [{"name": "Cached Author"}],
                "date": "2026-08-01",
                "order_meaningful": True,
                "star_means": None,
            }
        }))
        monkeypatch.setattr(
            dh,
            "list_publication_urls",
            lambda months: [("https://deepmind.google/research/publications/1/", "2026-08-01")],
        )
        monkeypatch.setattr(dh, "fetch", lambda url, pause=1.5: "<html>page</html>")
        monkeypatch.setattr(
            dh,
            "detail_page_info",
            lambda html: {"title": "T", "date": "2026-08-01", "source_url": None},
        )

        import llm_byline

        def boom(*a, **kw):
            raise AssertionError("extract_page must not be called for a cached URL")

        monkeypatch.setattr(llm_byline, "extract_page", boom)
        monkeypatch.setattr(llm_byline, "load_env", lambda: None)

        papers = dh.collect(months=3, model="claude-sonnet-5")
        assert len(papers) == 1
        assert papers[0].authors == [{"name": "Cached Author"}]
        assert not cost_path.exists()  # nothing new billed
