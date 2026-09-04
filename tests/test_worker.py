"""One scheduled firing: what it runs, and what counts as failure.

The silent failures these catch:

* **A dead source failing the whole firing.** The orchestrator isolates sources
  so one broken lab does not cost six working ones; if the run status and exit
  code do not say the same thing, the alerting contradicts the design one layer
  down and pages nightly for a transient outage (D26).
* **Cadence skipping a leg forever, or ignoring cadence entirely.** Both look
  like a working pipeline — one quietly ingests less than anyone thinks, the
  other spends the GitHub leg's wall-clock every night for nothing.
* **A firing losing its ingestion because a later phase failed.** The fetches
  really happened and, on the LLM legs, were really paid for.
* **A firing that broke exiting zero.** The platform's own cron alerting keys
  off the exit code; a green light on a broken run is the worst outcome here.
* **An article found tonight not being scored until tomorrow.** Phase order
  decides this and nothing downstream complains about it: the run succeeds, the
  article is stored, and it is simply unscored for a day (D38).
"""

from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import digest as digest_mod
from app import models as m
from app.db import create_all
from app.pipeline import worker
from app.pipeline.adapters import FetchResult
from app.pipeline.registry import Source

CONFIG = Path(__file__).parent.parent / "config" / "pipeline.yaml"


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture()
def quiet(monkeypatch, tmp_path):
    """Neutralise everything that touches the network, disk corpus, or wallet."""
    monkeypatch.setattr(worker, "load_sources", lambda legs=(): [
        Source(leg=leg, id=f"{leg}-src", label=leg, stage=1, enabled=True, config={})
        for leg in legs
    ])
    monkeypatch.setattr(
        "app.pipeline.orchestrator.adapter_for", lambda source: lambda s, st, sess=None: FetchResult()
    )
    monkeypatch.setattr(worker, "merge_announcements",
                        lambda items, *a, **k: {"added": 0, "kept": 0, "total": 0})
    monkeypatch.setattr(worker, "load_articles",
                        lambda s, run_id=None: {"inserted": 0, "updated": 0, "unchanged": 0})
    monkeypatch.setattr(worker, "_etl", lambda session, run, pv, stats: stats.update(etl="ok"))
    return tmp_path


def config_file(tmp_path, **overrides) -> Path:
    base = yaml.safe_load(CONFIG.read_text())
    for key, value in overrides.items():
        base[key] = {**base.get(key, {}), **value} if isinstance(value, dict) else value
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(base))
    return path


class TestCadence:
    def test_the_first_firing_runs_every_leg(self):
        """A fresh deployment gets a full sweep, not six days of partial ones."""
        config = {"cadence": {"announcements": 1, "papers": 3, "github": 7}}
        assert worker.due_legs(1, config) == ("announcements", "papers", "github")

    def test_a_slow_leg_is_skipped_between_its_turns(self):
        config = {"cadence": {"announcements": 1, "papers": 3, "github": 7}}
        assert worker.due_legs(2, config) == ("announcements",)
        # papers every 3rd firing: 1, 4, 7, 10...  github every 7th: 1, 8, 15...
        assert worker.due_legs(4, config) == ("announcements", "papers")
        assert worker.due_legs(7, config) == ("announcements", "papers")
        assert worker.due_legs(8, config) == ("announcements", "github")
        assert worker.due_legs(22, config) == ("announcements", "papers", "github")

    def test_an_explicit_override_ignores_cadence(self):
        config = {"cadence": {"github": 7}}
        assert worker.due_legs(2, config, only=("github",)) == ("github",)

    def test_an_empty_override_runs_no_legs_at_all(self):
        """`()` is "none", `None` is "ask cadence" — conflating them ran everything.

        Live on run 16: the pipeline tab sends an empty leg list when only the
        drift box is ticked, `legs or None` turned that into None, and the
        firing fetched 2,000 GitHub repos nobody asked for (D40).
        """
        config = {"cadence": {"announcements": 1, "papers": 1, "github": 1}}
        assert worker.due_legs(1, config, ()) == ()
        assert worker.due_legs(1, config, None) == ("announcements", "papers", "github")

    def test_a_missing_cadence_entry_means_every_firing(self):
        assert worker.due_legs(5, {"cadence": {}}) == ("announcements", "papers", "github")

    def test_the_real_config_names_only_real_legs(self):
        cadence = worker.load_config()["cadence"]
        assert set(cadence) - {"drift"} <= set(worker.LEGS)

    def test_firing_number_counts_scheduled_runs_only(self, session):
        session.add_all([
            m.PipelineRun(kind="scheduled", status="succeeded"),
            m.PipelineRun(kind="load", status="succeeded"),      # not a firing
            m.PipelineRun(kind="rebuild", status="succeeded"),   # not a firing
        ])
        session.commit()
        assert worker.firing_number(session) == 2


class TestADeadSourceIsNotADeadRun:
    def test_the_firing_succeeds_when_a_source_fails(self, session, quiet, monkeypatch):
        """D26: source_down escalates it after N runs; this run still completed."""
        def adapter(source):
            if source.leg == "papers":
                return lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("503"))
            return lambda s, st, sess=None: FetchResult()

        monkeypatch.setattr("app.pipeline.orchestrator.adapter_for", adapter)

        run, stats = worker.run_once(
            session, legs=("announcements", "papers"),
            config_path=config_file(quiet), spend=False, deliver=False,
        )

        assert stats["ingest"]["failed"] == 1
        assert run.status == "succeeded"
        assert run.error is None

    def test_the_failed_source_is_still_recorded(self, session, quiet, monkeypatch):
        monkeypatch.setattr(
            "app.pipeline.orchestrator.adapter_for",
            lambda source: lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("503")),
        )
        worker.run_once(session, legs=("announcements",),
                        config_path=config_file(quiet), spend=False, deliver=False)

        row = session.scalars(select(m.RunSource)).one()
        assert row.status == "failed"
        assert "503" in row.error
        assert session.get(m.SourceState, ("announcements", "announcements-src")).consecutive_failures == 1

    def test_the_exit_code_is_zero_when_only_a_source_failed(self, session, quiet, monkeypatch):
        """A nightly red cron for a transient outage makes the platform the noisy channel."""
        monkeypatch.setattr(
            "app.pipeline.orchestrator.adapter_for",
            lambda source: lambda s, st, sess=None: (_ for _ in ()).throw(RuntimeError("503")),
        )
        monkeypatch.setattr(worker, "get_session", lambda engine: session)
        monkeypatch.setattr(worker, "get_engine", lambda: session.get_bind())
        monkeypatch.setattr(worker, "ensure_schema", lambda engine: "current")

        assert worker.main(["--legs", "announcements", "--dry-run"]) == 0


class TestAFiringThatBrokeFails:
    def test_an_etl_failure_marks_the_run_failed(self, session, quiet, monkeypatch):
        def boom(session_, run, pv, stats):
            raise RuntimeError("transform exploded")

        monkeypatch.setattr(worker, "_etl", boom)

        with pytest.raises(RuntimeError, match="transform exploded"):
            worker.run_once(session, legs=("announcements",),
                            config_path=config_file(quiet), spend=False, deliver=False)

        run = session.scalars(select(m.PipelineRun)).one()
        assert run.status == "failed"

    def test_the_exit_code_is_non_zero_when_the_firing_broke(self, session, quiet, monkeypatch):
        monkeypatch.setattr(worker, "_etl", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(worker, "get_session", lambda engine: session)
        monkeypatch.setattr(worker, "get_engine", lambda: session.get_bind())
        monkeypatch.setattr(worker, "ensure_schema", lambda engine: "current")

        assert worker.main(["--legs", "announcements", "--dry-run"]) == 1

    def test_ingestion_survives_an_etl_failure(self, session, quiet, monkeypatch):
        """The fetches happened. Re-doing them costs money on the LLM legs."""
        monkeypatch.setattr(
            "app.pipeline.orchestrator.adapter_for",
            lambda source: lambda s, st, sess=None: FetchResult(items=[{"url": "https://a/1"}]),
        )
        monkeypatch.setattr(worker, "_etl", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))

        with pytest.raises(RuntimeError):
            worker.run_once(session, legs=("announcements",),
                            config_path=config_file(quiet), spend=False, deliver=False)

        with Session(session.get_bind()) as fresh:
            state = fresh.get(m.SourceState, ("announcements", "announcements-src"))
            assert state.last_success_at is not None
            assert fresh.scalar(select(func.count()).select_from(m.RunSource)) == 1


class TestOneFiringIsOneRunRow:
    def test_ingestion_and_etl_share_a_run(self, session, quiet):
        worker.run_once(session, legs=("announcements", "papers"),
                        config_path=config_file(quiet), spend=False, deliver=False)

        runs = session.scalars(select(m.PipelineRun)).all()
        assert len(runs) == 1
        assert runs[0].kind == "scheduled"
        sources = session.scalars(select(m.RunSource)).all()
        assert {s.run_id for s in sources} == {runs[0].id}

    def test_stats_accumulate_across_phases(self, session, quiet):
        run, stats = worker.run_once(session, legs=("announcements",),
                                     config_path=config_file(quiet),
                                     spend=False, deliver=False)
        assert {"firing", "legs", "ingest", "register", "alerts"} <= set(run.stats)
        assert run.stats["legs"] == ["announcements"]


class TestDryRun:
    def test_no_llm_stage_runs(self, session, quiet, monkeypatch):
        monkeypatch.setattr(worker, "classify_new",
                            lambda *a, **k: pytest.fail("must not classify"))
        monkeypatch.setattr(worker.drift_mod, "measure",
                            lambda *a, **k: pytest.fail("must not measure drift"))

        run, stats = worker.run_once(session, legs=("announcements",),
                                     config_path=config_file(quiet),
                                     spend=False, deliver=False)

        assert "classify" not in stats
        assert run.cost_usd == 0.0

    def test_alerts_are_recorded_but_not_delivered(self, session, quiet, monkeypatch):
        session.add(m.PipelineRun(kind="load", status="failed", error="earlier failure"))
        session.commit()
        delivered = []
        monkeypatch.setitem(alerts_channels := worker.alerts_mod.CHANNELS, "stdout",
                            lambda alert: delivered.append(alert))

        _, stats = worker.run_once(session, legs=("announcements",),
                                   config_path=config_file(quiet),
                                   spend=False, deliver=False)

        assert stats["alerts"]["raised"] >= 1
        assert delivered == []
        # The alerts were recorded, just not pushed — the row is the record of
        # truth and --dry-run must not lose it.
        assert session.scalar(select(func.count()).select_from(m.Alert)) >= 1


class TestSystemExitIsNotAnEscapeHatch:
    """Code under research/ is scripts first, and scripts call sys.exit().

    `harvest_github.load_token` exits when GITHUB_TOKEN is unset — and
    `SystemExit` derives from BaseException, so a bare `except Exception` let it
    past both the orchestrator's per-source handler and the worker's. The
    observed result was the worst available: the firing aborted mid-ingest, the
    run stayed `running` forever, `record_failure` was never reached so
    `consecutive_failures` stayed 0, and neither `run_failed` (which matches
    `failed`) nor `source_down` could ever see it.
    """

    def test_a_source_calling_sys_exit_is_recorded_as_a_failed_source(self, session, quiet, monkeypatch):
        import sys as _sys

        monkeypatch.setattr(
            "app.pipeline.orchestrator.adapter_for",
            lambda source: lambda s, st, sess=None: _sys.exit("GITHUB_TOKEN not set"),
        )

        run, stats = worker.run_once(session, legs=("github",),
                                     config_path=config_file(quiet),
                                     spend=False, deliver=False)

        assert stats["ingest"]["failed"] == 1
        assert run.status == "succeeded"          # a dead source is not a dead run
        assert run.status != "running"            # and never a corpse
        row = session.scalars(select(m.RunSource)).one()
        assert row.status == "failed"
        assert "GITHUB_TOKEN" in row.error
        assert session.get(m.SourceState, ("github", "github-src")).consecutive_failures == 1

    def test_a_sys_exit_outside_ingestion_still_closes_the_run(self, session, quiet, monkeypatch):
        import sys as _sys

        monkeypatch.setattr(worker, "_etl",
                            lambda *a: _sys.exit("something under research/ bailed"))

        with pytest.raises(SystemExit):
            worker.run_once(session, legs=("announcements",),
                            config_path=config_file(quiet), spend=False, deliver=False)

        run = session.scalars(select(m.PipelineRun)).one()
        assert run.status == "failed"
        assert run.finished_at is not None

    def test_keyboard_interrupt_is_not_swallowed(self, session, quiet, monkeypatch):
        """Ctrl-C means stop, not 'mark this source failed'."""
        monkeypatch.setattr(
            "app.pipeline.orchestrator.adapter_for",
            lambda source: lambda s, st, sess=None: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            worker.run_once(session, legs=("announcements",),
                            config_path=config_file(quiet), spend=False, deliver=False)


class TestAnArticleIsScoredTheFiringItIsFound:
    """The silent failure this catches: bronze loaded *after* the classifier.

    `classify_new` picks its work list with `pending_urls`, a LEFT JOIN of
    `raw_articles` against `raw_llm_responses`. While `load_articles` ran in the
    ETL (phase 5) and the classifier in phase 3, that join could only ever see
    articles from a *previous* firing. Nothing failed — the run succeeded, the
    article was stored, and it was simply unscored until the next night. Live on
    run 12: four OpenAI articles ingested, `classify` reported `pending: 0`, the
    ETL then reported `classifications.missing: 4` (D38).
    """

    def test_the_classifier_sees_articles_landed_this_firing(
        self, session, quiet, monkeypatch
    ):
        from app.pipeline.classify import pending_urls

        url = "https://lab.example/found-tonight"

        def land_one(s, run_id=None):
            s.add(m.RawArticle(url=url, payload={"title": "t"}, content_hash="h",
                               source_file="announcements.json", load_run_id=run_id))
            s.flush()
            return {"inserted": 1, "updated": 0, "unchanged": 0}

        saw = {}

        def spy(s, prompt_version, budget=None, config_path=None):
            saw["pending"] = pending_urls(s, prompt_version)
            return {"pending": len(saw["pending"]), "classified": 0,
                    "skipped_for_budget": 0, "failures": [], "cost_usd": 0.0,
                    "bands": {}, "budget": None}

        monkeypatch.setattr(worker, "load_articles", land_one)
        monkeypatch.setattr(worker, "classify_new", spy)
        monkeypatch.setattr(worker.drift_mod, "measure", lambda **kw: {})

        worker.run_once(session, legs=("announcements",),
                        config_path=config_file(quiet), spend=True, deliver=False)

        assert saw["pending"] == [url], (
            "the classifier ran before this firing's articles reached raw_articles"
        )


class TestPhaseSixPublishesTheDigest:
    """The firing's own product output.

    Deleting `digest_mod.publish` from `_phases` left the whole suite green
    before these existed, and the page would not have shown it: the digest tab
    opens on the live *preview*, which is computed on read and never depended on
    the firing at all. The only symptom of the pipeline silently ceasing to
    publish is an archive that stops growing.
    """

    def test_a_firing_publishes_an_edition_for_each_audience(self, session, quiet, monkeypatch):
        run, stats = worker.run_once(
            session, legs=(), config_path=config_file(quiet), spend=False, deliver=False)

        published = session.scalars(select(m.Digest)).all()
        assert {d.kind for d in published} == set(digest_mod.KINDS)
        assert set(stats["digest"]) == set(digest_mod.KINDS)
        assert all(d.run_id == run.id for d in published)

    def test_every_edition_records_what_it_suppressed(self, session, quiet, monkeypatch):
        """The cut is the product; a firing that published items without the
        count would have dropped the only auditable part of it."""
        worker.run_once(session, legs=(), config_path=config_file(quiet),
                        spend=False, deliver=False)

        for d in session.scalars(select(m.Digest)):
            assert {"considered", "surfaced", "suppressed"} <= set(d.stats)

    def test_two_firings_in_one_period_update_the_edition_rather_than_fork_it(
        self, session, quiet, monkeypatch
    ):
        """Two runs a day apart share a 48h period. Before the window was
        quantised, `run.started_at` made the idempotence key unique per firing
        and the archive grew by two rows a night, forever."""
        first, _ = worker.run_once(session, legs=(), config_path=config_file(quiet),
                                   spend=False, deliver=False)
        first.status = "succeeded"
        session.commit()
        ids = sorted(d.id for d in session.scalars(select(m.Digest)))

        worker.run_once(session, legs=(), config_path=config_file(quiet),
                        spend=False, deliver=False)

        assert sorted(d.id for d in session.scalars(select(m.Digest))) == ids
        assert len(ids) == len(digest_mod.KINDS)
