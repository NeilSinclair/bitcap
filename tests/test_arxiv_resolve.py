"""Tests for the shared arXiv title-resolution module.

Extracted from test_meta_harvest.py (docs/decisions.md D17) once
mistral_harvest.py needed the same logic. The silent failures this suite
exists to catch: a relaxed match accepted on too little evidence, and a
correct candidate missed because only the top-ranked search result was ever
checked -- both confirmed as real bugs against real 2026 papers (D16), not
hypothetical.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "papers"))

import arxiv_resolve as ar
from arxiv_resolve import overlap, resolve_title


class TestOverlap:
    def test_identical_text_scores_one(self):
        assert overlap("mathematics formalization scale", "mathematics formalization scale") == 1.0

    def test_disjoint_text_scores_zero(self):
        assert overlap("mathematics formalization", "glass physics amber") == 0.0

    def test_stopwords_and_short_tokens_excluded(self):
        # "of", "a", "to" contribute nothing either way; "agents" is the only
        # word that should drive the overlap here.
        assert overlap("a topology of agents", "an unrelated topology of things") > 0.0


class TestResolveTitle:
    def test_exact_title_match_accepted(self, monkeypatch):
        monkeypatch.setattr(
            ar,
            "arxiv_query",
            lambda expr, max_results=5: [
                {"arxiv_id": "2605.11111", "title": "Some Paper Title", "date": "2026-05-01", "summary": ""}
            ],
        )
        match = resolve_title("Some Paper Title", "irrelevant desc")
        assert match["arxiv_id"] == "2605.11111"
        assert match["resolution"] == "exact_title"

    def test_single_ti_hit_accepted_despite_text_diff(self, monkeypatch):
        # A ti: query is already field-scoped and narrow -- a single hit
        # whose title differs slightly (e.g. a version annotation arXiv adds
        # that a lab's own page doesn't show) is still trusted, rather than
        # falling through to the noisier relaxed pass over one candidate.
        monkeypatch.setattr(
            ar,
            "arxiv_query",
            lambda expr, max_results=5: [
                {"arxiv_id": "2605.22222", "title": "GIM: Evaluating Models (v2)", "date": "2026-05-01", "summary": ""}
            ],
        )
        match = resolve_title("GIM: Evaluating models", "desc")
        assert match["resolution"] == "exact_title"

    def test_relaxed_match_accepted_above_threshold(self, monkeypatch):
        calls = []

        def fake_query(expr, max_results=5):
            calls.append(expr)
            if expr.startswith("ti:"):
                return []
            return [
                {
                    "arxiv_id": "2605.33333",
                    "title": "Formalizing Mathematics at Scale",
                    "date": "2026-05-01",
                    "summary": "a system for autoformalized textbook library at scale",
                }
            ]

        monkeypatch.setattr(ar, "arxiv_query", fake_query)
        match = resolve_title(
            "AutoformBot: Formalizing Mathematics at Scale",
            "a multi-agent system for building an autoformalized textbook library at scale",
        )
        assert match is not None
        assert match["resolution"] == "relaxed_title"
        assert any(c.startswith("all:") for c in calls)

    def test_relaxed_match_picks_best_scoring_candidate_not_just_top_ranked(self, monkeypatch):
        # Real case (docs/decisions.md D16): for "AIRA2: Overcoming
        # Bottlenecks in AI Research Agents", arXiv's own relevance ranking
        # put an unrelated paper that only shares the "AIRA" acronym first,
        # and the correct paper (overlap 1.0) second. Checking only
        # relaxed[0] missed it entirely.
        def fake_query(expr, max_results=5):
            if expr.startswith("ti:"):
                return []
            return [
                {
                    "arxiv_id": "2604.17587",
                    "title": "AIRA: AI-Induced Risk Audit",
                    "date": "2026-04-01",
                    "summary": "a structured inspection framework for ai-generated code",
                },
                {
                    "arxiv_id": "2603.26499",
                    "title": "AIRA_2: Overcoming Bottlenecks in AI Research Agents",
                    "date": "2026-03-01",
                    "summary": "structural performance bottlenecks in ai research agents",
                },
            ]

        monkeypatch.setattr(ar, "arxiv_query", fake_query)
        match = resolve_title(
            "AIRA2: Overcoming Bottlenecks in AI Research Agents",
            "structural performance bottlenecks in ai research agents",
        )
        assert match is not None
        assert match["arxiv_id"] == "2603.26499"

    def test_relaxed_match_rejected_below_threshold(self, monkeypatch):
        def fake_query(expr, max_results=5):
            if expr.startswith("ti:"):
                return []
            return [
                {
                    "arxiv_id": "9999.99999",
                    "title": "HyperAgent: Generalist Software Engineering Agents",
                    "date": "2026-01-01",
                    "summary": "coding tasks at scale via generalist software engineering",
                }
            ]

        monkeypatch.setattr(ar, "arxiv_query", fake_query)
        match = resolve_title(
            "HyperAgents",
            "self-improving AI systems reduce reliance on human engineering",
        )
        assert match is None

    def test_no_results_at_all_returns_none(self, monkeypatch):
        monkeypatch.setattr(ar, "arxiv_query", lambda expr, max_results=5: [])
        assert resolve_title("Nothing Findable", "desc") is None


class TestArxivQuery:
    def test_parses_entry_fields(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ar, "CACHE", tmp_path)
        xml = (
            "<feed><entry>"
            "<id>http://arxiv.org/abs/2605.11111v1</id>"
            "<title>A Real Title</title>"
            "<published>2026-05-01T00:00:00Z</published>"
            "<summary>An abstract.</summary>"
            "</entry></feed>"
        )
        monkeypatch.setattr(ar, "fetch", lambda url, **kw: xml)
        out = ar.arxiv_query("ti:whatever")
        assert out == [
            {
                "arxiv_id": "2605.11111v1",
                "title": "A Real Title",
                "date": "2026-05-01",
                "summary": "An abstract.",
            }
        ]

    def test_entry_missing_required_field_is_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ar, "CACHE", tmp_path)
        xml = "<feed><entry><title>No id or date</title></entry></feed>"
        monkeypatch.setattr(ar, "fetch", lambda url, **kw: xml)
        assert ar.arxiv_query("ti:whatever") == []
