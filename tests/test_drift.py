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


@pytest.fixture()
def gold_stub(monkeypatch):
    """Synthetic gold articles, so a test can size the sample to what it needs.

    The suite's other tests use real gold ids because they are checking the real
    sample. These are checking concurrency, which needs a sample whose size the
    test controls and whose classification is instant.
    """
    import score_announcements as sa

    def install(fake_classify):
        monkeypatch.setattr(
            drift, "load_gold",
            lambda sample=drift.DEFAULT_SAMPLE: [
                {
                    "id": gid,
                    "url": f"https://lab.example/{gid}",
                    "text": "body",
                    "gold": {"event_type": "other", "mechanisms": [],
                             "categories": [], "practices": []},
                }
                for gid in sample
            ],
        )
        monkeypatch.setattr(sa, "classify", fake_classify)
        monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: object())

    return install


class TestTheSample:
    def test_the_default_sample_exists_on_disk(self):
        records = drift.load_gold()
        assert len(records) == len(drift.DEFAULT_SAMPLE)
        assert {r["id"] for r in records} == set(drift.DEFAULT_SAMPLE)

    def test_the_sample_is_every_gold_article(self):
        """A gold article added to disk but not to the sample is silent under-coverage.

        The sample is listed by hand (drift.py explains why), so the failure
        mode is adjudicating a new article and forgetting to register it: the
        check keeps passing while quietly measuring less than it claims to.
        """
        on_disk = {p.stem for p in drift.GOLD.glob("*.json")}
        assert set(drift.DEFAULT_SAMPLE) == on_disk, (
            f"unregistered: {sorted(on_disk - set(drift.DEFAULT_SAMPLE))}, "
            f"missing from disk: {sorted(set(drift.DEFAULT_SAMPLE) - on_disk)}"
        )

    def test_the_sample_cannot_run_the_budget_away(self):
        """Drift is uncached by design, so its size is a recurring per-run cost.

        At the measured ~$0.0375 an item it fires on every run against a $75
        monthly ceiling. This is the guard on growing the gold set without
        noticing the bill (docs/decisions.md D35).
        """
        assert len(drift.DEFAULT_SAMPLE) * 0.0375 < 1.50

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


class TestDriftRunsInParallelButWarmsTheCacheFirst:
    """The silent failure this catches: a parallel fan-out that costs *more*.

    `sa.classify` marks the shared system prompt `cache_control: ephemeral`.
    Anthropic bills a cache write at 1.25x input and a read at 0.10x, so firing
    every article at a cold cache makes all of them writes — 12x faster and
    ~12x the input cost, which is the opposite of the point. One call has to
    complete serially and create the entry before the rest fan out (D42).

    Nothing here asserts on wall-clock. The observable that matters is the
    *order* of the first call against the others, and that is deterministic.
    """

    def test_no_other_call_starts_until_the_warm_up_has_finished(
        self, monkeypatch, gold_stub
    ):
        """The whole point: one write, then reads. Not "roughly first".

        The first call holds for a beat. Any call that begins while it is still
        in flight would, in production, be a second cache *write* at 1.25x. The
        violation is recorded rather than raced on, so a regression fails
        loudly instead of flaking.
        """
        import threading
        import time

        import app.pipeline.drift as drift_mod

        warm_finished = threading.Event()
        violations, order, lock = [], [], threading.Lock()

        def fake_classify(client, model, article):
            with lock:
                first = not order
                order.append(article["id"])
            if first:
                time.sleep(0.15)          # the warm-up call is in flight
                warm_finished.set()
            elif not warm_finished.is_set():
                with lock:
                    violations.append(article["id"])
            return (
                {"event_type": "other", "mechanisms": [], "categories": [],
                 "practices": []},
                {"usd": 0.01, "url": article["url"],
                 "at": "2026-09-04T00:00:00+00:00"},
            )

        monkeypatch.setattr(drift_mod, "_record_cost", lambda sa, cost: None)
        gold_stub(fake_classify)

        drift.measure(sample=tuple(str(i) for i in range(8)), workers=6)

        assert not violations, (
            f"{len(violations)} call(s) started before the cache was warm — "
            "each one is a 1.25x cache write instead of a 0.10x read"
        )
        assert len(order) == 8

    def test_the_rest_really_do_overlap(self, monkeypatch, gold_stub):
        """The other half of the bargain: after the warm-up it must be parallel.

        A warm-up that accidentally serialised everything would pass the test
        above and be no faster than the loop it replaced.
        """
        import threading

        import app.pipeline.drift as drift_mod

        live, peak, lock = [0], [0], threading.Lock()
        barrier = threading.Barrier(4, timeout=3)
        order = []

        def fake_classify(client, model, article):
            with lock:
                first = not order
                order.append(article["id"])
            if not first:
                with lock:
                    live[0] += 1
                    peak[0] = max(peak[0], live[0])
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    pass
                with lock:
                    live[0] -= 1
            return (
                {"event_type": "other", "mechanisms": [], "categories": [],
                 "practices": []},
                {"usd": 0.01, "url": article["url"],
                 "at": "2026-09-04T00:00:00+00:00"},
            )

        monkeypatch.setattr(drift_mod, "_record_cost", lambda sa, cost: None)
        gold_stub(fake_classify)

        drift.measure(sample=tuple(str(i) for i in range(9)), workers=4)

        assert peak[0] >= 4, f"never ran more than {peak[0]} calls at once"

    def test_every_article_is_classified_exactly_once(self, monkeypatch, gold_stub):
        order = []

        def fake_classify(client, model, article):
            order.append(article["id"])
            return (
                {"event_type": "other", "mechanisms": [], "categories": [],
                 "practices": []},
                {"usd": 0.01, "url": article["url"],
                 "at": "2026-09-04T00:00:00+00:00"},
            )

        import app.pipeline.drift as drift_mod
        monkeypatch.setattr(drift_mod, "_record_cost", lambda sa, cost: None)
        gold_stub(fake_classify)

        out = drift.measure(sample=tuple(str(i) for i in range(8)), workers=4)

        assert sorted(order) == sorted(str(i) for i in range(8))
        assert out["compared"] == 8

    def test_progress_never_goes_backwards(self, monkeypatch, gold_stub):
        """Calls finish out of order; `done` is incremented under the lock."""
        ticks = []

        def fake_classify(client, model, article):
            return (
                {"event_type": "other", "mechanisms": [], "categories": [],
                 "practices": []},
                {"usd": 0.01, "url": article["url"],
                 "at": "2026-09-04T00:00:00+00:00"},
            )

        import app.pipeline.drift as drift_mod
        monkeypatch.setattr(drift_mod, "_record_cost", lambda sa, cost: None)
        gold_stub(fake_classify)

        drift.measure(
            sample=tuple(str(i) for i in range(8)),
            workers=4,
            on_progress=lambda done, total: ticks.append(done),
        )

        assert ticks == sorted(ticks), f"progress went backwards: {ticks}"
        assert ticks[-1] == 8

    def test_a_failing_call_does_not_take_the_others_down(self, monkeypatch, gold_stub):
        """One bad call is not a drift signal — it is recorded and stepped over."""
        calls = []

        def fake_classify(client, model, article):
            calls.append(article["id"])
            if article["id"] == "3":
                raise RuntimeError("overloaded")
            return (
                {"event_type": "other", "mechanisms": [], "categories": [], "practices": []},
                {"usd": 0.01, "url": article["url"], "at": "2026-09-04T00:00:00+00:00"},
            )

        import app.pipeline.drift as drift_mod
        monkeypatch.setattr(drift_mod, "_record_cost", lambda sa, cost: None)
        gold_stub(fake_classify)

        out = drift.measure(sample=tuple(str(i) for i in range(6)), workers=4)

        assert out["compared"] == 5
        assert [e["id"] for e in out["errors"]] == ["3"]

    def test_the_budget_still_stops_it_mid_fan_out(self, monkeypatch, gold_stub):
        """The ceiling has to hold across threads, not just down a serial loop."""
        from app.pipeline.budget import Budget

        def fake_classify(client, model, article):
            return (
                {"event_type": "other", "mechanisms": [], "categories": [], "practices": []},
                {"usd": 1.0, "url": article["url"], "at": "2026-09-04T00:00:00+00:00"},
            )

        import app.pipeline.drift as drift_mod
        monkeypatch.setattr(drift_mod, "_record_cost", lambda sa, cost: None)
        gold_stub(fake_classify)

        budget = Budget(per_run_usd=2.5, per_month_usd=100.0)
        out = drift.measure(sample=tuple(str(i) for i in range(10)), workers=4,
                            budget=budget)

        assert out["skipped"] > 0, "the ceiling never bound"
        assert out["compared"] < 10
        assert budget.run_spent <= budget.per_run_usd, (
            f"spent ${budget.run_spent:.2f} against a ${budget.per_run_usd:.2f} ceiling"
        )

    def test_the_ceiling_holds_when_the_calls_are_slow(self, monkeypatch, gold_stub):
        """The version of this that matters, and the one the first test missed.

        With an instant stub the workers serialise by accident: each finishes and
        records its spend before the next checks. Real calls take ~25 seconds, so
        all N workers read the total before any of them has added to it. Asserting
        only `skipped > 0` passed against a run that spent $13.00 against a $2.00
        ceiling — 6.5x over, measured (D43). Assert the money.
        """
        import time

        from app.pipeline.budget import Budget

        def slow_classify(client, model, article):
            time.sleep(0.3)     # stands in for a real API call
            return (
                {"event_type": "other", "mechanisms": [], "categories": [],
                 "practices": []},
                {"usd": 1.0, "url": article["url"],
                 "at": "2026-09-04T00:00:00+00:00"},
            )

        import app.pipeline.drift as drift_mod
        monkeypatch.setattr(drift_mod, "_record_cost", lambda sa, cost: None)
        gold_stub(slow_classify)

        budget = Budget(per_run_usd=2.0, per_month_usd=100.0)
        drift.measure(sample=tuple(str(i) for i in range(13)), workers=12,
                      budget=budget)

        assert budget.run_spent <= 2.0, (
            f"12 workers spent ${budget.run_spent:.2f} against a $2.00 ceiling — "
            "the guard must count work in flight, not just settled spend"
        )
