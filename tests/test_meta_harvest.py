"""Tests for the Meta AI papers harvest.

The silent failures this suite exists to catch: pagination that stops before
a real, distinct page of results (or loops forever on a wraparound repeat), a
title match accepted on too little evidence (Meta's site prepends system
names arXiv's own title drops -- confirmed live, see meta_harvest.py's module
docstring), a resolved paper placed in the window using the wrong date
source, and an unresolved paper silently dropped instead of recorded.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "papers"))

import meta_harvest as mh
from meta_harvest import Paper, aggregate, detail_page_info


def _search_page(slugs):
    links = "".join(f'<a href="/research/publications/{s}/">x</a>' for s in slugs)
    return f"<html><body>{links}</body></html>"


class TestListPaperCandidates:
    def test_stops_on_empty_page(self, monkeypatch):
        pages = {
            1: _search_page(["a", "b"]),
            2: "<html><body>no results</body></html>",
        }

        def fake_fetch(url, **kwargs):
            page = int(url.rsplit("page=", 1)[1])
            return pages[page]

        monkeypatch.setattr(mh, "fetch", fake_fetch)
        out = mh.list_paper_candidates([2026])
        assert [slug for slug, _ in out] == ["a", "b"]

    def test_stops_on_wraparound_repeat_not_max_pages(self, monkeypatch):
        # Confirmed live that page 2 within one year has zero overlap with
        # page 1 for real content -- but the endpoint's ordering is not
        # documented, so a page that repeats everything already seen must
        # stop the loop rather than being treated as "new" and looping to
        # max_pages.
        calls = {"n": 0}

        def fake_fetch(url, **kwargs):
            calls["n"] += 1
            return _search_page(["a", "b"])  # every page identical

        monkeypatch.setattr(mh, "fetch", fake_fetch)
        out = mh.list_paper_candidates([2026], max_pages=20)
        assert [slug for slug, _ in out] == ["a", "b"]
        assert calls["n"] == 2  # page 1 (new), page 2 (all repeats -> stop)

    def test_dedups_across_years(self, monkeypatch):
        def fake_fetch(url, **kwargs):
            if "years%5B0%5D=2025" in url and "page=1" in url:
                return _search_page(["shared", "only-2025"])
            if "years%5B0%5D=2026" in url and "page=1" in url:
                return _search_page(["shared", "only-2026"])
            return "<html><body>no results</body></html>"

        monkeypatch.setattr(mh, "fetch", fake_fetch)
        out = mh.list_paper_candidates([2025, 2026])
        assert sorted(slug for slug, _ in out) == ["only-2025", "only-2026", "shared"]

    def test_detail_url_shape(self, monkeypatch):
        monkeypatch.setattr(
            mh,
            "fetch",
            lambda url, **kw: _search_page(["my-paper"]) if "page=1" in url else "<html></html>",
        )
        out = mh.list_paper_candidates([2026])
        assert out == [("my-paper", "https://ai.meta.com/research/publications/my-paper/")]


class TestDetailPageInfo:
    def test_title_read_and_site_suffix_stripped(self):
        page = "<html><title>My Paper | Research - AI at Meta</title></html>"
        assert detail_page_info(page)["title"] == "My Paper"

    def test_title_tag_with_attributes_still_matches(self):
        page = '<html><title id="pageTitle">My Paper | Research - AI at Meta</title></html>'
        assert detail_page_info(page)["title"] == "My Paper"

    def test_desc_read_from_meta_description(self):
        page = '<html><meta property="og:description" content="An abstract snippet."></html>'
        assert detail_page_info(page)["desc"] == "An abstract snippet."

    def test_missing_desc_is_empty_not_none(self):
        page = "<html><title>T | Research - AI at Meta</title></html>"
        assert detail_page_info(page)["desc"] == ""


class TestAggregate:
    def _paper(self, title, authors, url="https://ai.meta.com/research/publications/x/"):
        return Paper(
            meta_url=url,
            arxiv_id="2605.00000",
            source_url="https://arxiv.org/html/2605.00000",
            title=title,
            date="2026-01-01",
            resolution="exact_title",
            authors=authors,
        )

    def test_appearances_accumulate_across_papers(self):
        papers = [
            self._paper("A", [{"name": "X"}], url="https://x/1"),
            self._paper("B", [{"name": "X"}], url="https://x/2"),
        ]
        rows = aggregate(papers)
        assert rows[0]["appearances"] == 2

    def test_sorted_most_appearances_first(self):
        papers = [
            self._paper("A", [{"name": "One"}, {"name": "Two"}], url="https://x/1"),
            self._paper("B", [{"name": "Two"}], url="https://x/2"),
        ]
        rows = aggregate(papers)
        assert [r["name"] for r in rows] == ["Two", "One"]


class TestCollectResilience:
    def _base_patches(self, monkeypatch, tmp_path, candidates):
        monkeypatch.setattr(mh, "CACHE", tmp_path)
        monkeypatch.setattr(mh, "COST", tmp_path / "cost.json")
        monkeypatch.setattr(mh, "EXTRACTION_CACHE", tmp_path / "extraction_cache.json")
        monkeypatch.setattr(mh, "UNRESOLVED", tmp_path / "unresolved.json")
        monkeypatch.setattr(mh, "list_paper_candidates", lambda years: candidates)

        import llm_byline

        monkeypatch.setattr(llm_byline, "load_env", lambda: None)

    def test_fetch_failure_is_recorded_not_raised(self, monkeypatch, tmp_path):
        self._base_patches(monkeypatch, tmp_path, [("p1", "https://ai.meta.com/research/publications/p1/")])

        def boom(url, **kwargs):
            raise RuntimeError("fetch failed: 500")

        monkeypatch.setattr(mh, "fetch", boom)
        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert papers == []
        assert unresolved[0]["reason"] == "fetch_failed"

    def test_no_arxiv_match_is_recorded_not_raised(self, monkeypatch, tmp_path):
        self._base_patches(monkeypatch, tmp_path, [("p1", "https://ai.meta.com/research/publications/p1/")])
        monkeypatch.setattr(mh, "fetch", lambda url, **kw: "<html><title>T | Research - AI at Meta</title></html>")
        monkeypatch.setattr(mh, "resolve_title", lambda title, desc: None)

        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert papers == []
        assert unresolved[0]["reason"] == "no_arxiv_match"

    def test_resolved_but_outside_window_is_dropped_silently_not_unresolved(self, monkeypatch, tmp_path):
        # Not a failure -- a resolved paper that's simply too old is a
        # correct exclusion, not something to flag as unresolved.
        self._base_patches(monkeypatch, tmp_path, [("p1", "https://ai.meta.com/research/publications/p1/")])
        monkeypatch.setattr(mh, "fetch", lambda url, **kw: "<html><title>T | Research - AI at Meta</title></html>")
        monkeypatch.setattr(
            mh,
            "resolve_title",
            lambda title, desc: {"arxiv_id": "2001.00001", "date": "2020-01-01", "resolution": "exact_title"},
        )

        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert papers == []
        assert unresolved == []

    def test_extraction_failure_is_recorded_not_raised(self, monkeypatch, tmp_path):
        self._base_patches(monkeypatch, tmp_path, [("p1", "https://ai.meta.com/research/publications/p1/")])
        monkeypatch.setattr(mh, "fetch", lambda url, **kw: "<html><title>T | Research - AI at Meta</title></html>")
        monkeypatch.setattr(
            mh,
            "resolve_title",
            lambda title, desc: {
                "arxiv_id": "2608.00001",
                "date": "2026-08-01",
                "resolution": "exact_title",
            },
        )

        import llm_byline

        def boom(*a, **kw):
            raise RuntimeError("refused")

        monkeypatch.setattr(llm_byline, "extract_page", boom)

        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert papers == []
        assert unresolved[0]["reason"] == "extraction_failed"

    def test_a_billed_but_unparseable_extraction_still_records_its_cost(self, monkeypatch, tmp_path):
        # Real gap found and fixed live (docs/decisions.md D17): a call that
        # was billed by the API but failed to parse must not lose its cost
        # record just because the extraction itself failed.
        cost_path = tmp_path / "cost.json"
        self._base_patches(monkeypatch, tmp_path, [("p1", "https://ai.meta.com/research/publications/p1/")])
        monkeypatch.setattr(mh, "COST", cost_path)
        monkeypatch.setattr(mh, "fetch", lambda url, **kw: "<html><title>T | Research - AI at Meta</title></html>")
        monkeypatch.setattr(
            mh,
            "resolve_title",
            lambda title, desc: {
                "arxiv_id": "2608.00001",
                "date": "2026-08-01",
                "resolution": "exact_title",
            },
        )

        import llm_byline

        def boom(*a, **kw):
            raise llm_byline.ExtractionError("malformed JSON", {"model": "claude-sonnet-5", "usd": 0.07})

        monkeypatch.setattr(llm_byline, "extract_page", boom)

        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert papers == []
        assert unresolved[0]["reason"] == "extraction_failed"
        recorded = json.loads(cost_path.read_text())
        assert recorded[0]["usd"] == 0.07
        assert recorded[0]["url"] == "https://ai.meta.com/research/publications/p1/"

    def test_cached_extraction_is_not_rebilled(self, monkeypatch, tmp_path):
        cache_path = tmp_path / "extraction_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "https://ai.meta.com/research/publications/p1/": {
                        "authors": [{"name": "Cached Author"}],
                        "order_meaningful": True,
                        "star_means": None,
                    }
                }
            )
        )
        self._base_patches(monkeypatch, tmp_path, [("p1", "https://ai.meta.com/research/publications/p1/")])
        monkeypatch.setattr(mh, "EXTRACTION_CACHE", cache_path)
        monkeypatch.setattr(mh, "fetch", lambda url, **kw: "<html><title>T | Research - AI at Meta</title></html>")
        monkeypatch.setattr(
            mh,
            "resolve_title",
            lambda title, desc: {
                "arxiv_id": "2608.00001",
                "date": "2026-08-01",
                "resolution": "exact_title",
            },
        )

        import llm_byline

        def boom(*a, **kw):
            raise AssertionError("extract_page must not be called for a cached URL")

        monkeypatch.setattr(llm_byline, "extract_page", boom)

        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert len(papers) == 1
        assert papers[0].authors == [{"name": "Cached Author"}]
        assert unresolved == []
