"""The ingestion loop and the per-source state behind it.

The silent failures these catch:

* One source's exception taking the whole run down. Before the orchestrator,
  `fetch_announcements.collect()` looped over seven labs with no error handling
  between them, so a single Cloudflare block cost six working labs. A regression
  here would look like a smaller-than-usual run, not like an error.
* A failure streak that never resets, or resets when it should not. The
  `source_down` alert reads `consecutive_failures` directly, so an off-by-one
  either cries wolf on a transient outage or stays silent through a real one.
* A failed fetch clobbering the watermark it never learned anything about,
  which would make the next run refetch a window it already has.
* Ingestion being rolled back by a later, unrelated failure — the work really
  happened and, for the LLM legs, was really paid for.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models as m
from app.db import create_all
from app.pipeline import state as state_mod
from app.pipeline.adapters import FetchResult
from app.pipeline.orchestrator import FAILED, SKIPPED, SUCCEEDED, ingest, run_source
from app.pipeline.registry import Source
from app.pipeline.sink import merge_announcements

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


def make_source(leg="announcements", id="anthropic", enabled=True, stage=1):
    return Source(leg=leg, id=id, label=id, stage=stage, enabled=enabled, config={})


@pytest.fixture()
def adapters(monkeypatch):
    """Swap the real adapters for scripted ones, keyed by source id.

    Registered as `{source_id: callable}`; anything unregistered returns an
    empty result. Patched on the orchestrator's own lookup so no network,
    filesystem or API key is involved.
    """
    scripted = {}

    def fake_adapter_for(source):
        return scripted.get(source.id, lambda s, st, sess=None: FetchResult())

    monkeypatch.setattr("app.pipeline.orchestrator.adapter_for", fake_adapter_for)
    return scripted


class TestOneDeadSourceDoesNotEndTheRun:
    def test_the_other_sources_still_complete(self, session, adapters):
        def boom(source, st, sess=None):
            raise RuntimeError("503 from the archive")

        adapters["openai"] = boom
        adapters["anthropic"] = lambda s, st, sess=None: FetchResult(items=[{"url": "https://a/1"}])
        adapters["deepseek"] = lambda s, st, sess=None: FetchResult(items=[{"url": "https://d/1"}])

        report = ingest(session, [make_source(id=x) for x in ("anthropic", "openai", "deepseek")])

        assert [o.status for o in report.outcomes] == [SUCCEEDED, FAILED, SUCCEEDED]
        assert len(report.items_for("announcements")) == 2
        assert report.ok is False

    def test_the_failure_reason_is_kept_not_swallowed(self, session, adapters):
        adapters["openai"] = lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("503 from the archive"))

        report = ingest(session, [make_source(id="openai")])

        outcome = report.outcomes[0]
        assert "503 from the archive" in outcome.error
        assert "RuntimeError" in outcome.error
        st = session.get(m.SourceState, ("announcements", "openai"))
        assert "503 from the archive" in st.last_error

    def test_sources_run_in_the_order_given(self, session, adapters):
        """Mistral's papers harvester reads the corpus announcements produces.

        `load_sources` is what sorts by stage; `ingest` must then not reorder.
        """
        seen = []

        def recorder(source, st, sess=None):
            seen.append(source.id)
            return FetchResult()

        adapters["anthropic"] = adapters["mistral"] = recorder
        ingest(session, [
            make_source(leg="announcements", id="anthropic", stage=1),
            make_source(leg="papers", id="mistral", stage=2),
        ])
        assert seen == ["anthropic", "mistral"]


class TestFailureStreaks:
    def test_consecutive_failures_accumulate(self, session, adapters):
        adapters["openai"] = lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("down"))
        source = make_source(id="openai")

        for _ in range(3):
            run_source(session, source)

        assert session.get(m.SourceState, ("announcements", "openai")).consecutive_failures == 3

    def test_a_success_resets_the_streak(self, session, adapters):
        source = make_source(id="openai")
        adapters["openai"] = lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("down"))
        run_source(session, source)
        run_source(session, source)

        adapters["openai"] = lambda s, st, sess=None: FetchResult(items=[{"url": "u"}])
        run_source(session, source)

        st = session.get(m.SourceState, ("announcements", "openai"))
        assert st.consecutive_failures == 0
        assert st.last_error is None
        assert st.last_success_at is not None

    def test_failing_lists_only_sources_at_or_past_the_threshold(self, session, adapters):
        adapters["openai"] = lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("down"))
        adapters["mistral"] = lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("down"))
        for _ in range(3):
            run_source(session, make_source(id="openai"))
        run_source(session, make_source(id="mistral"))

        incidents = state_mod.failing(session, threshold=3)

        assert [s.source_id for s in incidents] == ["openai"]

    def test_a_disabled_source_is_never_an_incident(self, session, adapters):
        adapters["openai"] = lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("down"))
        for _ in range(3):
            run_source(session, make_source(id="openai"))
        session.get(m.SourceState, ("announcements", "openai")).disabled = True
        session.commit()

        assert state_mod.failing(session, threshold=3) == []


class TestWatermarks:
    def test_success_advances_the_watermark(self, session, adapters):
        adapters["anthropic"] = lambda s, st, sess=None: FetchResult(
            items=[{"url": "u"}], watermark={"max_published": "2026-08-27"}
        )
        run_source(session, make_source(id="anthropic"))

        st = session.get(m.SourceState, ("announcements", "anthropic"))
        assert st.watermark == {"max_published": "2026-08-27"}

    def test_failure_leaves_the_previous_watermark_alone(self, session, adapters):
        source = make_source(id="anthropic")
        adapters["anthropic"] = lambda s, st, sess=None: FetchResult(watermark={"max_published": "2026-08-27"})
        run_source(session, source)

        adapters["anthropic"] = lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("down"))
        run_source(session, source)

        st = session.get(m.SourceState, ("announcements", "anthropic"))
        assert st.watermark == {"max_published": "2026-08-27"}
        assert st.consecutive_failures == 1

    def test_an_empty_watermark_does_not_erase_the_stored_one(self, session, adapters):
        """A source that found nothing this run has not lost what it knew."""
        source = make_source(id="anthropic")
        state = state_mod.load(session, "announcements", "anthropic")
        state_mod.record_success(state, {"max_published": "2026-08-27"}, now=NOW)
        session.commit()

        adapters["anthropic"] = lambda s, st, sess=None: FetchResult(items=[], watermark={})
        run_source(session, source)

        assert session.get(m.SourceState, ("announcements", "anthropic")).watermark == {
            "max_published": "2026-08-27"
        }


class TestDisabledSources:
    def test_a_config_disabled_source_is_skipped_not_failed(self, session, adapters):
        """xAI publishes no papers. That is a finding, not an outage."""
        adapters["xai"] = lambda s, st, sess=None: (_ for _ in ()).throw(AssertionError("must not be called"))

        report = ingest(session, [make_source(leg="papers", id="xai", enabled=False)])

        assert report.outcomes[0].status == SKIPPED
        assert report.ok is True
        assert session.get(m.SourceState, ("papers", "xai")).consecutive_failures == 0

    def test_an_operator_disabled_source_is_skipped(self, session, adapters):
        adapters["openai"] = lambda s, st, sess=None: (_ for _ in ()).throw(AssertionError("must not be called"))
        state_mod.load(session, "announcements", "openai").disabled = True
        session.commit()

        report = ingest(session, [make_source(id="openai")])

        assert report.outcomes[0].status == SKIPPED
        assert "operator" in report.outcomes[0].error


class TestRunRecording:
    def test_each_source_gets_a_run_row(self, session, adapters):
        run = m.PipelineRun(kind="scheduled")
        session.add(run)
        session.commit()

        adapters["openai"] = lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("down"))
        adapters["anthropic"] = lambda s, st, sess=None: FetchResult(items=[{"url": "u"}], cost_usd=0.25)
        ingest(session, [make_source(id="anthropic"), make_source(id="openai")], run_id=run.id)

        rows = {r.source_id: r for r in session.scalars(select(m.RunSource)).all()}
        assert rows["anthropic"].status == SUCCEEDED
        assert rows["anthropic"].items_seen == 1
        assert rows["anthropic"].cost_usd == 0.25
        assert rows["openai"].status == FAILED
        assert "down" in rows["openai"].error

    def test_ingestion_survives_a_later_source_dying(self, session, adapters):
        """A fetch that happened must not be rolled back by an unrelated failure.

        This is the deliberate difference from the ETL, which is one atomic
        transaction. Re-fetching costs money on the LLM legs.
        """
        run = m.PipelineRun(kind="scheduled")
        session.add(run)
        session.commit()

        adapters["anthropic"] = lambda s, st, sess=None: FetchResult(items=[{"url": "u"}])
        adapters["openai"] = lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("down"))
        ingest(session, [make_source(id="anthropic"), make_source(id="openai")], run_id=run.id)

        # A brand-new session sees only what was actually committed.
        with Session(session.get_bind()) as fresh:
            saved = fresh.get(m.SourceState, ("announcements", "anthropic"))
            assert saved.last_success_at is not None
            assert fresh.scalars(select(m.RunSource)).all() != []

    def test_stats_summarise_the_run(self, session, adapters):
        adapters["openai"] = lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("down"))
        adapters["anthropic"] = lambda s, st, sess=None: FetchResult(items=[{"url": "a"}, {"url": "b"}])

        report = ingest(session, [
            make_source(id="anthropic"),
            make_source(id="openai"),
            make_source(leg="papers", id="xai", enabled=False),
        ])

        assert report.stats == {
            "sources": 3, "succeeded": 1, "failed": 1, "skipped": 1,
            "items": 2, "cost_usd": 0.0,
        }


class TestProgressCallback:
    def test_reports_each_source_as_it_finishes(self, session, adapters):
        seen = []
        ingest(session, [make_source(id="anthropic"), make_source(id="openai")],
               on_progress=seen.append)
        assert [o.source.id for o in seen] == ["anthropic", "openai"]


class TestMergeAnnouncements:
    def test_a_fresh_corpus_is_written(self, tmp_path):
        path = tmp_path / "announcements.json"
        stats = merge_announcements(
            [{"url": "https://a/1", "date": "2026-08-01", "lab": "anthropic"}], path
        )
        assert stats["added"] == 1 and stats["kept"] == 0
        assert json.loads(path.read_text())[0]["url"] == "https://a/1"

    def test_a_failed_labs_articles_survive_a_partial_run(self, tmp_path):
        """The property an overwrite would break, and the reason this exists."""
        path = tmp_path / "announcements.json"
        merge_announcements([
            {"url": "https://openai/1", "date": "2026-08-01", "lab": "openai"},
            {"url": "https://anthropic/1", "date": "2026-08-02", "lab": "anthropic"},
        ], path)

        # Next run: OpenAI is down, so only Anthropic returns anything.
        stats = merge_announcements(
            [{"url": "https://anthropic/1", "date": "2026-08-02", "lab": "anthropic"}], path
        )

        assert stats["kept"] == 1
        urls = {a["url"] for a in json.loads(path.read_text())}
        assert urls == {"https://openai/1", "https://anthropic/1"}

    def test_re_running_an_unchanged_source_adds_nothing(self, tmp_path):
        """Idempotence: a second run over the same data is a no-op."""
        path = tmp_path / "announcements.json"
        items = [{"url": "https://a/1", "date": "2026-08-01", "lab": "anthropic"}]
        merge_announcements(items, path)
        before = path.read_text()

        stats = merge_announcements(items, path)

        assert stats == {"added": 0, "updated": 0, "unchanged": 1, "kept": 0, "total": 1}
        assert path.read_text() == before

    def test_a_changed_article_is_updated_in_place(self, tmp_path):
        path = tmp_path / "announcements.json"
        merge_announcements([{"url": "https://a/1", "date": "2026-08-01", "text": "short"}], path)
        stats = merge_announcements(
            [{"url": "https://a/1", "date": "2026-08-01", "text": "recovered full text"}], path
        )
        assert stats["updated"] == 1
        assert json.loads(path.read_text())[0]["text"] == "recovered full text"

    def test_the_corpus_stays_newest_first(self, tmp_path):
        path = tmp_path / "announcements.json"
        merge_announcements([
            {"url": "https://a/1", "date": "2026-06-01"},
            {"url": "https://a/2", "date": "2026-08-27"},
        ], path)
        dates = [a["date"] for a in json.loads(path.read_text())]
        assert dates == sorted(dates, reverse=True)


class TestSpendReachesTheRunsCeiling:
    """A source's spend has to be charged to the budget of the run that spent it.

    `Budget.month_spent_before` is snapshotted at construction, so spend that
    only reaches `raw_costs` shows up on the *next* firing — the one firing that
    cannot see it is the one that spent it. That matters most on the first
    firing after the relevance filter ships, which is also the firing that
    backfills every newly promoted repository at `releases_backfill` apiece,
    i.e. exactly when the per-run ceiling is doing work.

    The two fields are charged the same way and stored differently, which is the
    whole point: `cost_usd` also reaches `run_sources`, `metered_usd` does not,
    because its records are already in `raw_costs` and `month_to_date` sums both
    tables on the stated assumption that they never overlap.
    """

    class _Budget:
        """The slice of `Budget` `run_source` touches."""

        exhausted = False

        def __init__(self):
            self.spent = 0.0

        def spend(self, usd):
            self.spent += usd

    def test_metered_spend_is_charged_to_the_budget(self, session, adapters):
        from app.pipeline.orchestrator import run_source

        adapters["anthropic"] = lambda s, st, sess=None: FetchResult(metered_usd=0.02)
        budget = self._Budget()

        run_source(session, make_source(), budget=budget)

        assert budget.spent == 0.02

    def test_metered_spend_is_kept_out_of_run_sources(self, session, adapters):
        """Charging the ceiling and writing the row are different questions.
        Writing it here as well would double-count it against the month."""
        from app.pipeline.orchestrator import run_source

        adapters["anthropic"] = lambda s, st, sess=None: FetchResult(metered_usd=0.02)
        run = m.PipelineRun(kind="scheduled", stats={})
        session.add(run)
        session.flush()

        run_source(session, make_source(), run_id=run.id)

        row = session.scalars(select(m.RunSource)).one()
        assert row.cost_usd == 0.0

    def test_both_kinds_of_spend_are_charged(self, session, adapters):
        from app.pipeline.orchestrator import run_source

        adapters["anthropic"] = lambda s, st, sess=None: FetchResult(
            cost_usd=0.01, metered_usd=0.02)
        budget = self._Budget()

        run_source(session, make_source(), budget=budget)

        assert budget.spent == pytest.approx(0.03)
