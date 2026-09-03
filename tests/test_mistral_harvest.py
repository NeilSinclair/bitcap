"""Tests for the Mistral AI papers harvest.

The silent failures this suite exists to catch: an announcement title that
doesn't get cleaned of site phrasing before resolution is attempted, a
non-Mistral or out-of-window announcement leaking into the candidate list,
and a resolved-but-unresolvable-with-confidence candidate silently dropped
instead of recorded.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "papers"))

import mistral_harvest as mh
from mistral_harvest import Paper, aggregate, clean_title


class TestCleanTitle:
    def test_strips_introducing_prefix_and_trailing_period(self):
        assert clean_title("Introducing Shieldstral.") == "Shieldstral"

    def test_strips_announcing_prefix(self):
        assert clean_title("Announcing Robostral Navigate") == "Robostral Navigate"

    def test_title_without_noise_unchanged(self):
        assert clean_title("Robostral Navigate") == "Robostral Navigate"


class TestListCandidateTitles:
    def _announcements(self, tmp_path, articles):
        path = tmp_path / "announcements.json"
        path.write_text(json.dumps(articles))
        return path

    def test_filters_to_mistral_lab_only(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            mh,
            "ANNOUNCEMENTS",
            self._announcements(
                tmp_path,
                [
                    {"lab": "mistral", "title": "Introducing Shieldstral.", "date": "2026-08-01", "url": "https://x/1"},
                    {"lab": "meta-ai", "title": "Some Meta Post", "date": "2026-08-01", "url": "https://x/2"},
                ],
            ),
        )
        out = mh.list_candidate_titles(months=3)
        assert [c["title"] for c in out] == ["Shieldstral"]

    def test_filters_out_of_window_announcements(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            mh,
            "ANNOUNCEMENTS",
            self._announcements(
                tmp_path,
                [
                    {"lab": "mistral", "title": "Recent", "date": "2026-08-01", "url": "https://x/1"},
                    {"lab": "mistral", "title": "Old", "date": "2020-01-01", "url": "https://x/2"},
                ],
            ),
        )
        out = mh.list_candidate_titles(months=3)
        assert [c["title"] for c in out] == ["Recent"]

    def test_title_is_cleaned_and_url_preserved(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            mh,
            "ANNOUNCEMENTS",
            self._announcements(
                tmp_path,
                [{"lab": "mistral", "title": "Introducing Leanstral 1.5.", "date": "2026-08-01", "url": "https://x/1"}],
            ),
        )
        out = mh.list_candidate_titles(months=3)
        assert out == [{"title": "Leanstral 1.5", "date": "2026-08-01", "announcement_url": "https://x/1"}]


class TestAggregate:
    def _paper(self, title, authors, url="https://mistral.ai/news/x/"):
        return Paper(
            announcement_url=url,
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
        monkeypatch.setattr(mh, "list_candidate_titles", lambda months: candidates)

        import llm_byline

        monkeypatch.setattr(llm_byline, "load_env", lambda: None)

    def test_no_desc_signal_means_relaxed_match_never_accepted(self, monkeypatch):
        # The module's whole design bet: resolve_title's relaxed fallback is
        # called with desc="" here, which must always score 0.0 overlap and
        # never falsely accept a low-confidence candidate.
        import arxiv_resolve as ar

        monkeypatch.setattr(
            ar,
            "arxiv_query",
            lambda expr, max_results=5: []
            if expr.startswith("ti:")
            else [{"arxiv_id": "1", "title": "Something Else Entirely", "date": "2026-01-01", "summary": "unrelated content about a different topic"}],
        )
        assert mh.resolve_title("Some Product Post", "") is None

    def test_no_arxiv_match_is_recorded_not_raised(self, monkeypatch, tmp_path):
        self._base_patches(
            monkeypatch,
            tmp_path,
            [{"title": "Not A Paper", "date": "2026-08-01", "announcement_url": "https://x/1"}],
        )
        monkeypatch.setattr(mh, "resolve_title", lambda title, desc: None)

        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert papers == []
        assert unresolved[0]["reason"] == "no_arxiv_match"

    def test_extraction_failure_is_recorded_not_raised(self, monkeypatch, tmp_path):
        self._base_patches(
            monkeypatch,
            tmp_path,
            [{"title": "Shieldstral", "date": "2026-07-28", "announcement_url": "https://x/1"}],
        )
        monkeypatch.setattr(
            mh,
            "resolve_title",
            lambda title, desc: {"arxiv_id": "2607.25857", "date": "2026-07-28", "resolution": "exact_title"},
        )
        monkeypatch.setattr(mh, "fetch", lambda url, **kw: "<html>page</html>")

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
        self._base_patches(
            monkeypatch,
            tmp_path,
            [{"title": "Shieldstral", "date": "2026-07-28", "announcement_url": "https://x/1"}],
        )
        monkeypatch.setattr(mh, "COST", cost_path)
        monkeypatch.setattr(
            mh,
            "resolve_title",
            lambda title, desc: {"arxiv_id": "2607.25857", "date": "2026-07-28", "resolution": "exact_title"},
        )
        monkeypatch.setattr(mh, "fetch", lambda url, **kw: "<html>page</html>")

        import llm_byline

        def boom(*a, **kw):
            raise llm_byline.ExtractionError("malformed JSON", {"model": "claude-sonnet-5", "usd": 0.07})

        monkeypatch.setattr(llm_byline, "extract_page", boom)

        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert papers == []
        assert unresolved[0]["reason"] == "extraction_failed"
        recorded = json.loads(cost_path.read_text())
        assert recorded[0]["usd"] == 0.07
        assert recorded[0]["url"] == "https://x/1"

    def test_cached_extraction_is_not_rebilled(self, monkeypatch, tmp_path):
        cache_path = tmp_path / "extraction_cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "https://x/1": {
                        "authors": [{"name": "Cached Author"}],
                        "order_meaningful": True,
                        "star_means": None,
                    }
                }
            )
        )
        self._base_patches(
            monkeypatch,
            tmp_path,
            [{"title": "Shieldstral", "date": "2026-07-28", "announcement_url": "https://x/1"}],
        )
        monkeypatch.setattr(mh, "EXTRACTION_CACHE", cache_path)
        monkeypatch.setattr(
            mh,
            "resolve_title",
            lambda title, desc: {"arxiv_id": "2607.25857", "date": "2026-07-28", "resolution": "exact_title"},
        )
        monkeypatch.setattr(mh, "fetch", lambda url, **kw: "<html>page</html>")

        import llm_byline

        def boom(*a, **kw):
            raise AssertionError("extract_page must not be called for a cached URL")

        monkeypatch.setattr(llm_byline, "extract_page", boom)

        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert len(papers) == 1
        assert papers[0].authors == [{"name": "Cached Author"}]
        assert unresolved == []

    def test_truncated_flag_flows_from_extraction_to_the_paper_record(self, monkeypatch, tmp_path):
        # Real gap found by code review: extract_page's own truncated flag
        # was discarded, so a byline extracted from a cut-off page was
        # indistinguishable from a complete one. Both real Mistral papers
        # checked live are truncated (D17) -- this is not a hypothetical.
        self._base_patches(
            monkeypatch,
            tmp_path,
            [{"title": "Shieldstral", "date": "2026-07-28", "announcement_url": "https://x/1"}],
        )
        monkeypatch.setattr(
            mh,
            "resolve_title",
            lambda title, desc: {"arxiv_id": "2607.25857", "date": "2026-07-28", "resolution": "exact_title"},
        )
        monkeypatch.setattr(mh, "fetch", lambda url, **kw: "<html>page</html>")

        import llm_byline

        def fake_extract_page(*a, **kw):
            parsed = {"authors": [{"name": "Alice"}], "order_meaningful": True, "star_means": None}
            cost = {"model": "claude-sonnet-5", "usd": 0.05}
            return parsed, cost, True  # truncated

        monkeypatch.setattr(llm_byline, "extract_page", fake_extract_page)

        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert len(papers) == 1
        assert papers[0].truncated is True

        cached = json.loads((tmp_path / "extraction_cache.json").read_text())
        assert cached["https://x/1"]["truncated"] is True

    def test_arxiv_html_unavailable_is_recorded_not_raised(self, monkeypatch, tmp_path):
        self._base_patches(
            monkeypatch,
            tmp_path,
            [{"title": "Shieldstral", "date": "2026-07-28", "announcement_url": "https://x/1"}],
        )
        monkeypatch.setattr(
            mh,
            "resolve_title",
            lambda title, desc: {"arxiv_id": "2607.25857", "date": "2026-07-28", "resolution": "exact_title"},
        )

        def boom(url, **kw):
            raise RuntimeError("fetch failed: 404")

        monkeypatch.setattr(mh, "fetch", boom)

        papers, unresolved = mh.collect(months=3, model="claude-sonnet-5")
        assert papers == []
        assert unresolved[0]["reason"] == "arxiv_html_unavailable"
