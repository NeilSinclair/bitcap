"""Drift monitoring: the check that catches the classifier degrading quietly.

The silent failures these catch, all of which produce a reassuring number:

* **Reading through the cache.** `classify_one` returns a cached result keyed on
  the article URL, so a drift check that used it would report perfect agreement
  forever — the failure mode that looks exactly like success. The measurement
  must call the uncached path.
* **A shrinking sample.** If a gold file goes missing and the sample silently
  drops to five, the metric keeps a plausible value while measuring something
  else. Comparability between runs is the entire point of a fixed sample.
* **A missing measurement read as a good one.** A run that classified nothing —
  budget gone, API down — must not report agreement at all, let alone alert.
* **A budget-less drift check.** It costs money by design; unbounded, it is the
  one stage that runs on every firing with no ceiling.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models as m
from app.db import create_all
from app.pipeline import drift
from app.pipeline.budget import Budget


@pytest.fixture(autouse=True)
def isolated_cost_log(tmp_path, monkeypatch):
    """Point the shared cost log at a temp file for every test in this module.

    `measure()` records cost at the call site into `score_announcements.COST`,
    which is a committed artifact. Without this the suite appends its stub
    records to the real ledger — which it did, and `load_costs` then crashed on
    rows with no `url`.
    """
    import score_announcements as sa

    monkeypatch.setattr(sa, "COST", tmp_path / "cost.json")


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


class TestTheSample:
    def test_the_default_sample_exists_on_disk(self):
        records = drift.load_gold()
        assert len(records) == len(drift.DEFAULT_SAMPLE)
        assert {r["id"] for r in records} == set(drift.DEFAULT_SAMPLE)

    def test_the_sample_is_small(self):
        """planning.md §6a: 5-6 items, never the corpus. It is pure cost."""
        assert 4 <= len(drift.DEFAULT_SAMPLE) <= 8

    def test_the_sample_spans_both_scoring_axes(self):
        """A sample of only richly-tagged articles cannot detect over-tagging."""
        records = drift.load_gold()
        tagged = [r for r in records if r["gold"].get("mechanisms")]
        untagged = [r for r in records if not r["gold"].get("mechanisms")]
        assert tagged and untagged, "sample must contain both scoring and non-scoring items"

    def test_a_missing_gold_file_raises_rather_than_shrinking_the_sample(self):
        with pytest.raises(FileNotFoundError, match="99"):
            drift.load_gold(sample=("12", "99"))


class TestMeasurementBypassesTheCache:
    def test_the_uncached_path_is_used(self, monkeypatch):
        """The single most important property here.

        `sa.classify` is uncached; `sa.classify_one` reads the per-URL cache. If
        this ever flips, drift reports 1.0 forever and the alert never fires.
        """
        import score_announcements as sa

        called = []
        monkeypatch.setattr(sa, "classify_one",
                            lambda *a, **k: pytest.fail("must not read the cache"))
        monkeypatch.setattr(sa, "classify", lambda client, model, article: (
            called.append(article["url"]) or (
                {"event_type": "other", "mechanisms": [], "categories": [],
                 "practices": [], "summary": "", "notable": False, "notable_reason": ""},
                {"usd": 0.01},
            )
        ))
        monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: object())

        metrics = drift.measure(sample=("12", "21"))

        assert len(called) == 2
        assert metrics["compared"] == 2

    def test_spend_is_recorded_against_the_budget(self, monkeypatch):
        import score_announcements as sa

        monkeypatch.setattr(sa, "classify", lambda client, model, article: (
            {"event_type": "other", "mechanisms": [], "categories": [], "practices": [],
             "summary": "", "notable": False, "notable_reason": ""},
            {"usd": 0.02},
        ))
        monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: object())

        budget = Budget(per_run_usd=3.0, per_month_usd=20.0)
        metrics = drift.measure(sample=("12", "21"), budget=budget)

        assert budget.run_spent == pytest.approx(0.04)
        assert metrics["cost_usd"] == pytest.approx(0.04)

    def test_an_exhausted_budget_skips_rather_than_reporting_agreement(self, monkeypatch):
        import score_announcements as sa

        monkeypatch.setattr(sa, "classify",
                            lambda *a: pytest.fail("must not call with no budget"))
        monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: object())

        budget = Budget(per_run_usd=0.0, per_month_usd=0.0)
        metrics = drift.measure(sample=("12", "21"), budget=budget)

        assert metrics["skipped"] == 2
        assert metrics["compared"] == 0
        assert metrics["mechanism_f1"] is None

    def test_one_failed_call_does_not_sink_the_measurement(self, monkeypatch):
        import score_announcements as sa

        seen = []

        def flaky(client, model, article):
            seen.append(article["url"])
            if len(seen) == 1:
                raise RuntimeError("overloaded")
            return ({"event_type": "other", "mechanisms": [], "categories": [],
                     "practices": [], "summary": "", "notable": False,
                     "notable_reason": ""}, {"usd": 0.01})

        monkeypatch.setattr(sa, "classify", flaky)
        monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: object())

        metrics = drift.measure(sample=("12", "21"))

        assert metrics["compared"] == 1
        assert len(metrics["errors"]) == 1


class TestTheAlertThreshold:
    def test_agreement_below_the_floor_fires(self):
        assert drift.below_floor({"mechanism_f1": 0.5}, floor=0.8) is True

    def test_agreement_at_the_floor_does_not_fire(self):
        assert drift.below_floor({"mechanism_f1": 0.8}, floor=0.8) is False

    def test_a_missing_measurement_is_not_drift(self):
        """Otherwise the alert fires every time the budget runs out."""
        assert drift.below_floor({"mechanism_f1": None, "compared": 0}, floor=0.8) is False
        assert drift.below_floor({}, floor=0.8) is False


class TestSnapshots:
    def test_a_snapshot_is_written(self, session):
        run = m.PipelineRun(kind="scheduled")
        session.add(run)
        session.flush()

        drift.record(session, {"mechanism_f1": 0.91, "compared": 6}, "v7", run.id)
        session.commit()

        saved = session.scalars(select(m.GoldSnapshot)).one()
        assert saved.prompt_version == "v7"
        assert saved.metrics["mechanism_f1"] == 0.91
        assert saved.run_id == run.id

    def test_history_is_oldest_first_for_the_chart(self, session):
        for f1 in (0.80, 0.85, 0.91):
            drift.record(session, {"mechanism_f1": f1, "compared": 6}, "v7")
        session.commit()

        points = drift.history(session)

        assert [p["mechanism_f1"] for p in points] == [0.80, 0.85, 0.91]

    def test_history_can_be_restricted_to_one_prompt_version(self, session):
        """Comparing across versions compares two different questions."""
        drift.record(session, {"mechanism_f1": 0.5}, "v6")
        drift.record(session, {"mechanism_f1": 0.9}, "v7")
        session.commit()

        assert [p["mechanism_f1"] for p in drift.history(session, "v7")] == [0.9]

    def test_history_survives_a_snapshot_with_no_metrics(self, session):
        drift.record(session, {}, "v7")
        session.commit()
        assert drift.history(session)[0]["mechanism_f1"] is None
