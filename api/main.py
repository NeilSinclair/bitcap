"""FastAPI service for the Frontier Lab Intelligence frontend.

Read-only: it queries the database `app/` builds, never writes to it. Run with:

    uv run uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/items")
def list_items() -> list[dict]:
    """Every article with a classification for PROMPT_VERSION, tags and connections inline."""
    session = get_session(engine)
    try:
        return build_items(session, PROMPT_VERSION)
    finally:
        session.close()


@app.get("/api/status")
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


@app.get("/api/runs")
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


@app.get("/api/alerts")
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


@app.get("/api/drift")
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
