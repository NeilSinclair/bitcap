"""FastAPI service for the Frontier Lab Intelligence frontend.

Read-only: it queries the database `app/` builds, never writes to it. Run with:

    uv run uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app import models as m
from app.cli import PROMPT_VERSION
from app.db import get_engine, get_session
from api.queries import build_items


def _load_env() -> None:
    """Read .env for DATABASE_URL without overriding the environment.

    Duplicates app.cli._load_env deliberately (same reasoning as that
    function's docstring): it is private to the CLI module, so this process
    keeps its own copy rather than depending on it.
    """
    path = Path(__file__).parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_env()

# One engine (and its connection pool) for the process's lifetime. Building a
# fresh engine per request never disposes the previous one, so each request
# leaked a physical Postgres connection until the server exhausted
# max_connections and took the shared database down with it.
engine = get_engine()

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
