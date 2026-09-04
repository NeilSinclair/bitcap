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
PUBLIC_ROUTES = {"/api/health", "/api/auth/login", "/openapi.json", "/docs",
                 "/docs/oauth2-redirect", "/redoc"}


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
        ungated = []
        for route in api_main.app.routes:
            path, methods = getattr(route, "path", None), getattr(route, "methods", set())
            if not path or path in PUBLIC_ROUTES:
                continue
            # Concrete value for a path parameter; the gate must reject before
            # the handler ever looks it up.
            url = path.replace("{run_id}", "1")
            for method in methods & {"GET", "POST"}:
                response = client.request(method, url, json={})
                if response.status_code not in (401, 403):
                    ungated.append(f"{method} {path} -> {response.status_code}")
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
            "announcements", "papers", "github", "drift"
        }


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
