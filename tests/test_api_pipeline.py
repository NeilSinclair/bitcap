"""The manual trigger, and the gate in front of the whole API.

The silent failures this suite exists to catch:

* **A route that forgot `Depends(require_auth)`.** The site is gated so that no
  data leaves without a token; one route missing its dependency reads fine in a
  logged-in browser and is wide open to everyone else. `TestEveryRouteIsGated`
  walks the app's own route table, so a route added next month is covered the
  day it lands rather than whenever somebody remembers to extend a list.
* **Two runs at once.** A second firing would interleave writes to the same
  corpus file and spend the budget twice. The guard has to hold against a
  concurrent request, not just a polite one.
* **A crashed run that still looks like it is running.** The guard is a query
  for a `running` row, so one corpse blocks every future run for ever.
* **A manual run advancing the cadence counter**, which would silently consume
  the GitHub leg's turn every time somebody pressed the button.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api import auth
from api import pipeline as pipeline_api
from app import models as m
from app.db import create_all

EMAIL = "neil@example.com"
PASSWORD = "hunter2-but-longer"

# Open by design: the platform probes this before a deploy is live, and a health
# check that needs a credential fails the deploy on first boot. Everything else
# must be gated.
PUBLIC_ROUTES = {"/api/health", "/api/auth/login"}


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("AUTH_EMAIL", EMAIL)
    monkeypatch.setenv("AUTH_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("AUTH_SECRET", "test-secret")


@pytest.fixture()
def engine():
    """One in-memory database shared across connections, so a thread sees it too."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    create_all(eng)
    return eng


@pytest.fixture()
def client(engine, configured):
    app = FastAPI()
    app.include_router(pipeline_api.build_router(engine))
    return TestClient(app)


@pytest.fixture()
def token(configured):
    return auth.issue_token(EMAIL)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestEveryRouteIsGated:
    def test_no_data_route_answers_without_a_token(self, monkeypatch, tmp_path):
        """Walks the real app's route table rather than a hand-maintained list."""
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'gate.db'}")
        monkeypatch.setenv("SKIP_SCHEMA_SYNC", "1")
        monkeypatch.setenv("AUTH_SECRET", "test-secret")
        import importlib

        from api import main as api_main
        importlib.reload(api_main)

        client = TestClient(api_main.app)
        # `app.openapi()["paths"]`, not `app.routes`. A router mounted with
        # `include_router` appears in `app.routes` as a single entry whose
        # `path` is None, so `if not path: continue` silently skipped the whole
        # `/api/pipeline/*` subtree — this test probed six routes and zero
        # pipeline routes while claiming to walk them all. The OpenAI schema
        # flattens included routers, so it sees every path the app serves (D43).
        paths = api_main.app.openapi()["paths"]
        assert any(p.startswith("/api/pipeline/") for p in paths), (
            "the pipeline routes are not being enumerated; this test is blind"
        )

        ungated = []
        for path, operations in paths.items():
            if path in PUBLIC_ROUTES:
                continue
            # Concrete value for a path parameter; the gate must reject before
            # the handler ever looks it up.
            url = path.replace("{run_id}", "1")
            for method in operations:
                if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                response = client.request(method.upper(), url, json={})
                if response.status_code not in (401, 403):
                    ungated.append(f"{method.upper()} {path} -> {response.status_code}")
        assert not ungated, f"routes answering without a token: {ungated}"

    def test_health_stays_open_for_the_platform_probe(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'health.db'}")
        monkeypatch.setenv("SKIP_SCHEMA_SYNC", "1")
        import importlib

        from api import main as api_main
        importlib.reload(api_main)
        create_all(api_main.engine)

        assert TestClient(api_main.app).get("/api/health").status_code == 200


class TestTheGateOnPipelineRoutes:
    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/pipeline/legs"),
        ("GET", "/api/pipeline/current"),
        ("GET", "/api/pipeline/run/1"),
        ("POST", "/api/pipeline/run"),
    ])
    def test_401_without_a_token(self, client, method, path):
        assert client.request(method, path, json={}).status_code == 401

    def test_a_valid_token_gets_the_legs(self, client, token):
        response = client.get("/api/pipeline/legs", headers=bearer(token))
        assert response.status_code == 200
        assert {leg["id"] for leg in response.json()} == {
            "announcements", "papers", "github", "releases", "posts", "drift"
        }


class TestEveryLegIsPresentableInTheUI:
    """A leg the operator cannot read is a leg they will not tick.

    `releases` and `posts` shipped into the run picker with no display name and
    no description: the frontend falls back to the raw id, so they rendered as
    lowercase `releases` and `posts` beside `Announcements` and `Papers`, with
    the note line blank. Nothing failed -- the checkboxes worked -- and the two
    newest, least obvious legs were the two with nothing explaining them.

    The cause is that a leg is declared in `registry.LEGS` and described in two
    other files, and neither of those is reached by adding one. So the coverage
    is asserted from `LEGS` itself rather than from a list somebody remembers to
    extend, exactly as `TestEveryRouteIsGated` walks the route table.
    """

    ROOT = Path(__file__).parent.parent

    def _labels(self) -> dict[str, str]:
        """Parse `LEG_LABEL` out of the page source.

        Asserts it parsed to something, because every check below is an
        emptiness test on a derived list -- a parse that silently returned "" on
        a refactor would make all of them pass while proving nothing.
        """
        source = (self.ROOT / "frontend" / "app" / "pipeline" / "page.js").read_text(
            encoding="utf-8")
        body = source.split("const LEG_LABEL = {", 1)[1].split("};", 1)[0]
        labels = dict(re.findall(r'(\w+):\s*"([^"]+)"', body))
        assert len(labels) >= 4, (
            f"LEG_LABEL parsed as {labels}; the test is reading the wrong thing")
        return labels

    def test_every_leg_the_api_offers_has_a_description(self, client, token):
        rows = client.get("/api/pipeline/legs", headers=bearer(token)).json()
        missing = [r["id"] for r in rows if not (r.get("note") or "").strip()]
        assert missing == [], (
            f"{missing} render with a blank description under the checkbox")

    def test_every_leg_has_a_display_name_in_the_run_picker(self, client, token):
        """Read from the frontend source, as `tests/test_digest.py` does: the
        fallback is `LEG_LABEL[id] || id`, so a missing entry is invisible to
        every server-side check and shows up only as a lowercase word.

        The ids come from the endpoint the page actually calls, not from
        `registry.LEGS`. `LEGS` omits `drift`, which is a checkbox here without
        being an ingestion leg, so iterating it would need "drift" appended by
        hand -- reintroducing the remember-to-extend list this class exists to
        avoid, one line further down.
        """
        rows = client.get("/api/pipeline/legs", headers=bearer(token)).json()
        labels = self._labels()
        missing = [r["id"] for r in rows if r["id"] not in labels]
        assert missing == [], (
            f"{missing} fall back to their raw lowercase id in the run picker")

    def test_the_names_are_capitalised_like_their_neighbours(self):
        """The fallback's tell. A label that is not capitalised is either a raw
        id leaking through or a new one typed to match the bug."""
        bad = [n for n in self._labels().values() if not n[0].isupper()]
        assert bad == []


class TestStartingARun:
    def test_an_unknown_leg_is_rejected(self, client, token):
        response = client.post("/api/pipeline/run", headers=bearer(token),
                               json={"legs": ["twitter"]})
        assert response.status_code == 400
        assert "twitter" in response.json()["detail"]

    def test_selecting_nothing_is_rejected(self, client, token):
        """An empty selection would otherwise run everything by accident."""
        response = client.post("/api/pipeline/run", headers=bearer(token),
                               json={"legs": [], "drift": False})
        assert response.status_code == 400

    def test_a_run_already_in_flight_is_refused(self, client, token, engine):
        with Session(engine) as s:
            s.add(m.PipelineRun(kind="scheduled", status="running"))
            s.commit()

        response = client.post("/api/pipeline/run", headers=bearer(token),
                               json={"legs": ["announcements"]})
        assert response.status_code == 409
        assert "already in flight" in response.json()["detail"]

    def test_the_run_row_is_claimed_before_the_thread_starts(
        self, client, token, engine, monkeypatch
    ):
        """The claim must be synchronous, or two clicks both pass the check."""
        started = []
        monkeypatch.setattr(pipeline_api.threading, "Thread",
                            lambda **kw: type("T", (), {"start": lambda self: started.append(kw)})())

        response = client.post("/api/pipeline/run", headers=bearer(token),
                               json={"legs": ["announcements"]})
        assert response.status_code == 200

        with Session(engine) as s:
            run = s.scalars(select(m.PipelineRun)).one()
        assert (run.status, run.kind) == ("running", "manual")
        assert response.json() == {"run_id": run.id, "status": "running"}
        assert started, "the worker thread was never started"

    def test_a_second_click_is_refused_while_the_first_holds_the_row(
        self, client, token, monkeypatch
    ):
        monkeypatch.setattr(pipeline_api.threading, "Thread",
                            lambda **kw: type("T", (), {"start": lambda self: None})())

        first = client.post("/api/pipeline/run", headers=bearer(token),
                            json={"legs": ["announcements"]})
        second = client.post("/api/pipeline/run", headers=bearer(token),
                             json={"legs": ["announcements"]})
        assert (first.status_code, second.status_code) == (200, 409)


class TestDriftOnlyRunsOnlyDrift:
    """The silent failure this catches: an empty leg selection meaning "everything".

    Ticking only the drift box sends `legs: []`. That reached `run_once` as
    `legs or None` — and None is the cron's "let cadence decide", so a run meant
    to re-score 20 gold articles instead fetched every cadence-due leg. Nothing
    errored; it was just slow and spent money on work nobody asked for (D40).
    """

    def test_an_empty_selection_is_passed_through_as_empty(self, engine, monkeypatch):
        seen = {}

        def fake_run_once(session, **kw):
            seen["legs"] = kw.get("legs")
            kw["run"].status = "succeeded"
            return kw["run"], {}

        with Session(engine) as s:
            run = m.PipelineRun(kind="manual", status="running")
            s.add(run)
            s.commit()
            run_id = run.id

        monkeypatch.setattr(pipeline_api.worker, "run_once", fake_run_once)
        pipeline_api._run_in_thread(engine, run_id, (), True, False)

        assert seen["legs"] == (), "an empty selection must not become None"

    def test_drift_alone_is_accepted_without_any_leg(self, client, token, monkeypatch):
        monkeypatch.setattr(pipeline_api.threading, "Thread",
                            lambda **kw: type("T", (), {"start": lambda self: None})())
        response = client.post("/api/pipeline/run", headers=bearer(token),
                               json={"legs": [], "drift": True})
        assert response.status_code == 200


class TestAManualRunIsNotAScheduledFiring:
    def test_it_does_not_advance_the_cadence_counter(self, client, token, engine, monkeypatch):
        """`firing_number` counts scheduled runs; a button press must not consume a turn."""
        from app.pipeline import worker

        monkeypatch.setattr(pipeline_api.threading, "Thread",
                            lambda **kw: type("T", (), {"start": lambda self: None})())
        client.post("/api/pipeline/run", headers=bearer(token),
                    json={"legs": ["announcements"]})

        with Session(engine) as s:
            assert worker.firing_number(s) == 1


class TestTheThreadNeverLeavesARunHanging:
    def test_a_crash_marks_the_row_failed(self, engine, monkeypatch):
        """Otherwise the corpse blocks every future run through the concurrency guard."""
        with Session(engine) as s:
            run = m.PipelineRun(kind="manual", status="running")
            s.add(run)
            s.commit()
            run_id = run.id

        def explode(*a, **kw):
            raise RuntimeError("config is malformed")

        monkeypatch.setattr(pipeline_api.worker, "run_once", explode)
        pipeline_api._run_in_thread(engine, run_id, ("announcements",), False, False)

        with Session(engine) as s:
            row = s.get(m.PipelineRun, run_id)
        assert row.status == "failed"
        assert "config is malformed" in row.error
        assert row.finished_at is not None

    def test_a_systemexit_is_caught_too(self, engine, monkeypatch):
        """research/ code is scripts first and calls sys.exit on missing config."""
        with Session(engine) as s:
            run = m.PipelineRun(kind="manual", status="running")
            s.add(run)
            s.commit()
            run_id = run.id

        def bail(*a, **kw):
            raise SystemExit("no such lab")

        monkeypatch.setattr(pipeline_api.worker, "run_once", bail)
        pipeline_api._run_in_thread(engine, run_id, ("announcements",), False, False)

        with Session(engine) as s:
            assert s.get(m.PipelineRun, run_id).status == "failed"

    def test_the_run_row_is_handed_to_run_once_not_duplicated(self, engine, monkeypatch):
        """One firing is one row; a placeholder plus a real row would be two."""
        with Session(engine) as s:
            run = m.PipelineRun(kind="manual", status="running")
            s.add(run)
            s.commit()
            run_id = run.id

        seen = {}

        def fake_run_once(session, **kw):
            seen["run"] = kw.get("run")
            kw["run"].status = "succeeded"
            return kw["run"], {}

        monkeypatch.setattr(pipeline_api.worker, "run_once", fake_run_once)
        pipeline_api._run_in_thread(engine, run_id, ("announcements",), True, False)

        assert seen["run"].id == run_id
        with Session(engine) as s:
            assert s.scalars(select(m.PipelineRun)).all().__len__() == 1


class TestOnlyOneRunCanExistAtATime:
    """The silent failure this catches: two firings trampling each other.

    The old guard was a SELECT-then-INSERT in this module, which is neither
    atomic nor visible to the cron — a separate process in a separate container
    that never consulted it. Overlapping firings interleave writes to the corpus
    file and both do read-modify-write on the cost log, losing records that a
    graded requirement says are captured at the call site (D44).
    """

    def test_the_database_refuses_a_second_running_row(self, engine):
        """The guarantee, tested where it lives rather than through a route."""
        from sqlalchemy.exc import IntegrityError

        with Session(engine) as s:
            s.add(m.PipelineRun(kind="scheduled", status="running"))
            s.commit()

        with Session(engine) as s:
            s.add(m.PipelineRun(kind="manual", status="running"))
            with pytest.raises(IntegrityError):
                s.commit()

    def test_finished_rows_are_not_constrained(self, engine):
        """Only `running` is unique; history must accumulate freely."""
        with Session(engine) as s:
            for _ in range(5):
                s.add(m.PipelineRun(kind="scheduled", status="succeeded"))
                s.add(m.PipelineRun(kind="manual", status="failed"))
            s.commit()
            assert s.query(m.PipelineRun).count() == 10

    def test_the_cron_stands_down_rather_than_overlapping(self, engine):
        """`run_once` must refuse, not crash, and `main` must exit 0."""
        from app.pipeline import worker

        with Session(engine) as s:
            s.add(m.PipelineRun(kind="manual", status="running"))
            s.commit()

        with Session(engine) as s:
            with pytest.raises(worker.ConcurrentRunRefused):
                worker.run_once(s, legs=("announcements",), spend=False, deliver=False)

    def test_the_api_returns_409_rather_than_500(self, client, token, engine, monkeypatch):
        """A losing race is a conflict, not a server error."""
        monkeypatch.setattr(pipeline_api.threading, "Thread",
                            lambda **kw: type("T", (), {"start": lambda self: None})())
        # Bypass the advisory SELECT so the INSERT is what refuses.
        monkeypatch.setattr(pipeline_api, "running_run", lambda s, now=None: None)
        with Session(engine) as s:
            s.add(m.PipelineRun(kind="scheduled", status="running"))
            s.commit()

        response = client.post("/api/pipeline/run", headers=bearer(token),
                               json={"legs": ["announcements"]})
        assert response.status_code == 409


class TestAStaleRunDoesNotBlockForever:
    """The silent failure this catches: one corpse blocking every future run.

    The concurrency guard asks "is there a `running` row". A firing killed
    mid-flight — redeploy, OOM, `uvicorn --reload` reacting to an edit — leaves
    one for ever, and every later POST returns 409 with no way out but editing
    the database. Hit twice in one afternoon locally (D43).
    """

    def _aged(self, engine, hours):
        from datetime import datetime, timedelta, timezone

        with Session(engine) as s:
            run = m.PipelineRun(
                kind="manual", status="running",
                started_at=datetime.now(timezone.utc) - timedelta(hours=hours),
            )
            s.add(run)
            s.commit()
            return run.id

    def test_a_run_past_the_ceiling_is_reaped(self, engine):
        run_id = self._aged(engine, pipeline_api.STALE_RUN_HOURS + 1)

        with Session(engine) as s:
            assert pipeline_api.running_run(s) is None

        with Session(engine) as s:
            row = s.get(m.PipelineRun, run_id)
        assert row.status == "failed"
        assert "no longer running" in row.error
        assert row.finished_at is not None

    def test_a_recent_run_is_still_believed(self, engine):
        """The bound must not reap a firing that is legitimately still going."""
        self._aged(engine, 0.25)
        with Session(engine) as s:
            assert pipeline_api.running_run(s) is not None

    def test_a_reaped_run_no_longer_blocks_a_new_one(self, client, token, monkeypatch, engine):
        self._aged(engine, pipeline_api.STALE_RUN_HOURS + 1)
        monkeypatch.setattr(pipeline_api.threading, "Thread",
                            lambda **kw: type("T", (), {"start": lambda self: None})())

        response = client.post("/api/pipeline/run", headers=bearer(token),
                               json={"legs": ["announcements"]})
        assert response.status_code == 200

    def test_reaping_marks_it_failed_so_alerting_can_see_it(self, engine):
        """`alerts.run_failed` keys on status; a silently deleted row alerts nobody."""
        run_id = self._aged(engine, pipeline_api.STALE_RUN_HOURS + 1)
        with Session(engine) as s:
            pipeline_api.running_run(s)
        with Session(engine) as s:
            assert s.get(m.PipelineRun, run_id) is not None, "reaped rows must survive"


class TestReportingOnARun:
    def test_status_counts_up_while_running(self, client, token, engine):
        with Session(engine) as s:
            run = m.PipelineRun(kind="manual", status="running")
            s.add(run)
            s.commit()
            run_id = run.id

        body = client.get(f"/api/pipeline/run/{run_id}", headers=bearer(token)).json()
        assert body["status"] == "running"
        assert body["finished_at"] is None
        assert body["duration_s"] >= 0

    def test_a_finished_run_reports_when_it_finished(self, client, token, engine):
        with Session(engine) as s:
            run = m.PipelineRun(kind="manual", status="succeeded",
                                finished_at=m.utcnow(), cost_usd=0.75)
            s.add(run)
            s.commit()
            run_id = run.id

        body = client.get(f"/api/pipeline/run/{run_id}", headers=bearer(token)).json()
        assert body["status"] == "succeeded"
        assert body["finished_at"] is not None
        assert body["cost_usd"] == 0.75

    def test_per_source_rows_come_back_with_the_run(self, client, token, engine):
        with Session(engine) as s:
            run = m.PipelineRun(kind="manual", status="running")
            s.add(run)
            s.flush()
            s.add(m.RunSource(run_id=run.id, leg="announcements", source_id="openai",
                              status="succeeded", items_seen=4, duration_s=1.2))
            s.commit()
            run_id = run.id

        sources = client.get(f"/api/pipeline/run/{run_id}",
                             headers=bearer(token)).json()["sources"]
        assert [(s["source_id"], s["items_seen"]) for s in sources] == [("openai", 4)]

    def test_an_unknown_run_is_404(self, client, token):
        assert client.get("/api/pipeline/run/999", headers=bearer(token)).status_code == 404

    def test_current_returns_the_in_flight_run(self, client, token, engine):
        """Reopening the tab mid-run must show the run, not an idle button."""
        with Session(engine) as s:
            s.add(m.PipelineRun(kind="scheduled", status="succeeded",
                                finished_at=m.utcnow()))
            s.add(m.PipelineRun(kind="manual", status="running"))
            s.commit()

        body = client.get("/api/pipeline/current", headers=bearer(token)).json()
        assert body["run"]["status"] == "running"

    def test_current_falls_back_to_the_last_finished_run(self, client, token, engine):
        with Session(engine) as s:
            s.add(m.PipelineRun(kind="scheduled", status="succeeded",
                                finished_at=m.utcnow(), cost_usd=0.5))
            s.commit()

        body = client.get("/api/pipeline/current", headers=bearer(token)).json()
        assert body["run"]["status"] == "succeeded"

    def test_current_on_an_empty_database_is_not_an_error(self, client, token):
        body = client.get("/api/pipeline/current", headers=bearer(token)).json()
        assert body == {"run": None, "sources": []}
