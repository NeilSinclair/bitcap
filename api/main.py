"""FastAPI service for the Frontier Lab Intelligence frontend.

Mostly read-only: it queries the database `app/` builds. The one exception is
`/api/pipeline/run`, which starts a firing — the only route here that writes,
spends money, or takes longer than a request.

**Everything except `/api/auth/login` and `/api/health` requires a session
token** (`api/auth.py`). The frontend is a static export, so its login screen is
a convenience, not a control; the guarantee is that this service returns 401
without a token and the page therefore has nothing to render.

`/api/health` is deliberately open: it is the platform's health check, and a
deploy that cannot be probed without a credential fails on the first boot.
It reports pipeline liveness and month-to-date spend, nothing about the labs.

Run with:

    uv run uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select

from api import pipeline as pipeline_api
from api.auth import AuthNotConfigured, login as do_login, require_auth
from api.ops import health as ops_health
from api.ops import run_history
from api.queries import build_items
from app import models as m
from app.cli import PROMPT_VERSION
from app.db import ensure_schema, get_engine, get_session, load_env
from app.pipeline import alerts as alerts_mod
from app.pipeline import drift as drift_mod


load_env()

# One engine (and its connection pool) for the process's lifetime. Building a
# fresh engine per request never disposes the previous one, so each request
# leaked a physical Postgres connection until the server exhausted
# max_connections and took the shared database down with it.
engine = get_engine()

# A managed database is provisioned empty. Without this the first request hits
# a table that does not exist, `/api/status` 500s, and the platform fails the
# deploy on its health check before the worker ever fires. Idempotent: an
# already-current database is a no-op.
if os.environ.get("SKIP_SCHEMA_SYNC", "").lower() not in ("1", "true", "yes"):
    ensure_schema(engine)

app = FastAPI(title="Frontier Lab Intelligence API")

# Comma-separated, so the deployed origin and a local dev server can both be
# allowed at once. A single value still works and is the common case.
_origins = [
    o.strip().rstrip("/")
    for o in os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # POST for login and for starting a run; the Authorization header carries
    # the session token, so it has to survive the preflight.
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class Credentials(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def auth_login(body: Credentials) -> dict:
    """Exchange the one account's email and password for a session token.

    Raises:
        HTTPException: 401 on bad credentials; 503 when the deployment has no
            account configured, which is an operator error and must not be
            reported as a wrong password.
    """
    try:
        return {"token": do_login(body.email, body.password)}
    except AuthNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/auth/me")
def auth_me(email: str = Depends(require_auth)) -> dict:
    """Whether the caller's stored token is still valid, and who it belongs to.

    The page calls this on load: a token in localStorage may have expired since
    it was issued, and showing the dashboard shell before finding that out
    means every panel fails at once instead of the login form appearing.
    """
    return {"email": email}


app.include_router(pipeline_api.build_router(engine))


@app.get("/api/items", dependencies=[Depends(require_auth)])
def list_items() -> list[dict]:
    """Every article with a classification for PROMPT_VERSION, tags and connections inline."""
    session = get_session(engine)
    try:
        return build_items(session, PROMPT_VERSION)
    finally:
        session.close()


@app.get("/api/status", dependencies=[Depends(require_auth)])
def status() -> dict | None:
    """The most recent pipeline run, for the header's run/cost chip."""
    session = get_session(engine)
    try:
        run = session.scalars(
            select(m.PipelineRun).order_by(m.PipelineRun.id.desc()).limit(1)
        ).first()
        if run is None:
            return None
        return {
            "status": run.status,
            "kind": run.kind,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "cost_usd": run.cost_usd,
        }
    finally:
        session.close()


@app.get("/api/runs", dependencies=[Depends(require_auth)])
def runs(limit: int = 20) -> list[dict]:
    """Run history with per-source detail, newest first.

    A firing that lost one source is still `succeeded` (D27), so the run row
    alone hides the failure — `sources` and `sources_failed` are what make it
    visible.
    """
    session = get_session(engine)
    try:
        return run_history(session, limit=min(max(limit, 1), 200))
    finally:
        session.close()


@app.get("/api/alerts", dependencies=[Depends(require_auth)])
def alert_feed(kind: str | None = None, limit: int = 50) -> list[dict]:
    """Raised alerts, newest first. `kind` filters to `system` or `content`.

    Includes alerts that were recorded but not delivered — the per-run delivery
    cap suppresses the overflow rather than dropping it, and this is where a
    reader sees the ones the channel did not push.
    """
    if kind is not None and kind not in ("system", "content"):
        raise HTTPException(status_code=400, detail="kind must be 'system' or 'content'")
    session = get_session(engine)
    try:
        return alerts_mod.recent(session, kind=kind, limit=min(max(limit, 1), 200))
    finally:
        session.close()


@app.get("/api/health")
def health_view() -> dict:
    """Is the pipeline working, and what has it cost this month."""
    session = get_session(engine)
    try:
        return ops_health(session)
    finally:
        session.close()


@app.get("/api/drift", dependencies=[Depends(require_auth)])
def drift_view(prompt_version: str | None = None, limit: int = 30) -> list[dict]:
    """Gold-set agreement over time, oldest first, for the reliability chart.

    Defaults to the current prompt version: comparing agreement across
    classifier versions is comparing two different questions, so widening it is
    something a caller asks for deliberately.
    """
    session = get_session(engine)
    try:
        return drift_mod.history(
            session,
            prompt_version=prompt_version if prompt_version is not None else PROMPT_VERSION,
            limit=min(max(limit, 1), 200),
        )
    finally:
        session.close()
