"""The paper gold grader: what it sends, what it counts, what it refuses.

No API calls anywhere here. The classification path is exercised in
`tests/test_papers_scoring.py`; this pins the grading arithmetic and the two
guarantees that would silently corrupt the number — leaking the answer into the
model's input, and grading against a set whose provenance nobody recorded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "papers"))

import grade_paper_gold as grader  # noqa: E402


def _record(gold_id: str, event_type: str = "research_result", **gold) -> dict:
    """A minimal gold record."""
    return {
        "id": gold_id, "lab": "anthropic", "date": "2026-07-01",
        "title": "t", "url": f"https://example.test/{gold_id}",
        "text_source": "paper_abstract", "document_type": "interpretability",
        "text": "body",
        "gold": {"labelled_by": "claude-fable-5", "event_type": event_type,
                 "mechanisms": [], "categories": [], "practices": [], **gold},
        "review": {},
    }


def _run(event_type: str = "research_result", **tags) -> dict:
    """A minimal classifier result."""
    return {"event_type": event_type, "mechanisms": [], "categories": [],
            "practices": [], "dropped_tags": [], "score": 0.0, "band": "none",
            "ai_score": 0.0, "ai_band": "none", **tags}


class TestTheGoldAnswerNeverReachesTheModel:
    """The set is labelled blind and graded blind, or the number means nothing."""

    def test_the_answer_and_the_stratum_are_excluded_from_the_input(self):
        # `document_type` is excluded too: it is the sampler's guess at what
        # kind of document this is, which is a hint about the event type.
        assert set(grader.NOT_INPUT) >= {"gold", "review", "document_type"}

    def test_every_excluded_key_is_actually_dropped(self):
        record = _record("01")
        article = {k: v for k, v in record.items() if k not in grader.NOT_INPUT}
        assert "gold" not in article and "document_type" not in article
        # And the things the prompt genuinely needs survive the filter.
        assert {"lab", "date", "url", "text_source", "text"} <= set(article)


class TestProvenanceIsRequiredNotOptional:

    def test_an_unlabelled_paper_fails_the_load(self, tmp_path):
        blank = _record("01")
        blank["gold"]["labelled_by"] = None
        (tmp_path / "01.json").write_text(json.dumps(blank))
        with pytest.raises(ValueError, match="labelled_by"):
            grader.load_gold(tmp_path)

    def test_the_committed_set_states_its_labeller(self):
        for record in grader.load_gold():
            assert record["gold"]["labelled_by"]


class TestTheHeadlineIsNoiseRejectionNotMechanismF1:
    """Seven of ten gold papers correctly carry no mechanism (D58)."""

    def test_a_safety_paper_that_starts_scoring_is_a_sign_disagreement(self):
        # The failure this corpus exists to catch: the noise filter stops
        # rejecting noise. It must show up as a named disagreement, not as a
        # small movement in a mean.
        gold = [_record("01")]
        runs = {"01": _run(score=100.0, band="high",
                           mechanisms=[{"id": "capability_jump", "sign": "positive",
                                        "magnitude": "high", "confidence": "high",
                                        "reason": "r", "quote": "q"}])}
        result = grader.metrics(gold, runs)
        misses = result["scores"]["investment"]["sign_disagreements"]
        assert [m["id"] for m in misses] == ["01"]
        assert result["scores"]["investment"]["zero_agreement"] == 0.0

    def test_two_correct_zeros_contribute_nothing_to_mechanism_micro_f1(self):
        # The reason mechanism F1 cannot be the paper headline: `axis` scores an
        # empty-vs-empty pair as no contribution at all, so the papers most
        # worth pinning are invisible to it. Pinned so nobody "fixes" the
        # headline back to mechanisms without meeting this.
        gold = [_record("01"), _record("02")]
        runs = {"01": _run(), "02": _run()}
        result = grader.metrics(gold, runs)
        assert result["axes"]["mechanisms"]["reference_tags"] == 0
        assert result["axes"]["mechanisms"]["micro_f1"] == 0.0
        # ...while the metric that does see them reports perfect agreement.
        assert result["scores"]["investment"]["zero_agreement"] == 1.0
        assert result["scores"]["investment"]["both_zero"] == 2


class TestDisagreementsAreNamedNotAveraged:

    def test_an_event_type_disagreement_carries_its_document_type(self):
        gold = [_record("09", event_type="frontier_model_release")]
        runs = {"09": _run(event_type="open_weights")}
        result = grader.metrics(gold, runs)
        assert result["event_type_agreement"] == 0.0
        assert result["event_type_disagreements"] == [
            {"id": "09", "document_type": "interpretability",
             "reference": "frontier_model_release", "run": "open_weights"}]

    def test_agreement_is_counted_over_compared_papers_only(self):
        # A paper that failed to classify must not count as agreement. Grading
        # 9 of 10 and reporting a denominator of 10 would read as a worse score
        # rather than a smaller sample -- and grading it as a match would read
        # as a better one.
        gold = [_record("01"), _record("02")]
        result = grader.metrics(gold, {"01": _run()})
        assert result["compared"] == 1
        assert result["event_type_agreement"] == 1.0

    def test_no_overlap_reports_nothing_rather_than_perfect_agreement(self):
        assert grader.metrics([_record("01")], {}) == {"compared": 0}


class TestTheCommittedBaselineStillReadsBack:
    """D58 quotes these numbers; a schema change that broke them must fail."""

    BASELINE = ROOT / "research" / "test_results" / "paper_gold_20260905T000000Z_p1.json"

    def test_the_recorded_run_reproduces_the_numbers_in_d58(self):
        saved = json.loads(self.BASELINE.read_text())
        recomputed = grader.metrics(grader.load_gold(), saved["runs"])
        assert recomputed["event_type_agreement"] == 0.9
        assert recomputed["scores"]["investment"]["zero_agreement"] == 1.0
        assert recomputed["scores"]["investment"]["both_zero"] == 7
        assert recomputed["axes"]["mechanisms"]["micro_f1"] == pytest.approx(0.857, abs=0.001)
        # Recomputed from the stored run, not read off the stored metrics: this
        # would catch `metrics()` drifting away from the figures D58 quotes.
        assert recomputed["event_type_agreement"] == saved["metrics"]["event_type_agreement"]

    def test_the_baseline_states_it_is_cross_model(self):
        saved = json.loads(self.BASELINE.read_text())
        assert saved["labelled_by"] == "claude-fable-5"
        assert saved["model"] == "claude-sonnet-5" and saved["prompt"] == "p1"
