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
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select

from api import pipeline as pipeline_api
from api.auth import (
    AuthNotConfigured,
    TooManyAttempts,
    login as do_login,
    require_auth,
)
from api.ops import health as ops_health
from api.ops import run_history
from api.queries import build_items
from app import digest as digest_mod
from app import people as people_mod
from app import models as m
from app.cli import PROMPT_VERSION, PROMPT_VERSIONS
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

# `openapi_url=None` also removes /docs and /redoc, which derive from it. They
# answered unauthenticated while the docstring above and the README both claimed
# only /api/health and /api/auth/login were open — route shapes rather than data,
# but the documentation was wrong, and the honest fix is to make it true (D44).
# `app.openapi()` the method still works, which is what the route-gate test
# enumerates.
app = FastAPI(
    title="Frontier Lab Intelligence API",
    openapi_url=None if os.environ.get("HIDE_API_DOCS", "1").lower() in ("1", "true", "yes")
    else "/openapi.json",
)

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
def auth_login(body: Credentials, request: Request) -> dict:
    """Exchange the one account's email and password for a session token.

    Raises:
        HTTPException: 401 on bad credentials; 429 when this address has failed
            too many attempts recently; 503 when the deployment has no account
            configured, which is an operator error and must not be reported as
            a wrong password.
    """
    source = request.client.host if request.client else "-"
    try:
        return {"token": do_login(body.email, body.password, source=source)}
    except TooManyAttempts as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
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
    """Every classified document, tags and connections inline.

    Spans both prompt versions: announcements at PROMPT_VERSION and papers at
    PAPER_PROMPT_VERSION reach one list, distinguished by each item's `docType`.
    """
    session = get_session(engine)
    try:
        return build_items(session, PROMPT_VERSIONS)
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


@app.post("/api/alerts/acknowledge", dependencies=[Depends(require_auth)])
def acknowledge_alerts() -> dict:
    """Clear the health badge by marking outstanding alerts as seen.

    Nothing is deleted — the alert history below the button is unchanged, each
    acknowledged row still listed and now marked. What resets is the badge,
    which counts *unacknowledged* system alerts (api/ops.py).

    Safe to expose because acknowledgement is withdrawn automatically: an
    unresolved fault makes the rules emit the same episode key on the next
    firing, and `alerts.dispatch` clears `acknowledged_at` when it sees one.
    The badge therefore reddens again on its own for anything still broken —
    it does not depend on the operator judging what has settled.

    System alerts only, and deliberately not parameterised.
    `alerts.acknowledge` takes a `kind` because the column is general, but there
    is nothing coherent to expose here for `content`: the badge does not count
    content alerts, and the content rules regenerate their candidates every
    firing, so acknowledging the findings would achieve nothing except having
    the next cron run reopen all of them. A parameter whose only effect is to be
    undone is worse than no parameter.
    """
    session = get_session(engine)
    try:
        count = alerts_mod.acknowledge(session, kind="system")
        session.commit()
        return {"acknowledged": count, "kind": "system"}
    finally:
        session.close()


@app.get("/api/digests", dependencies=[Depends(require_auth)])
def digest_feed(kind: str | None = None, limit: int = 20) -> list[dict]:
    """Published digests, newest first — the brief's "read past reports".

    Served from the stored payload, never rebuilt on read. A digest is a dated
    claim about what mattered; recomputing it against today's corpus would
    restate last week's edition in this week's terms (app/digest.py).
    """
    if kind is not None and kind not in digest_mod.KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"kind must be one of {', '.join(digest_mod.KINDS)}",
        )
    session = get_session(engine)
    try:
        return digest_mod.history(session, kind=kind, limit=min(max(limit, 1), 100))
    finally:
        session.close()


@app.get("/api/digests/preview", dependencies=[Depends(require_auth)])
def digest_preview(kind: str = digest_mod.INVESTMENT, hours: int | None = None) -> dict:
    """What a digest published right now would say, without publishing it.

    Read-only and unpersisted, so opening the tab on a day with no firing still
    shows the current window rather than an empty page. `hours` overrides the
    configured window for a reader who wants to widen it; the published edition
    always uses the configured value.

    Rolling, not quantised (`quantise=False`). Published editions snap to a
    fixed grid so re-runs update one edition instead of issuing several; the
    preview writes nothing, so it has no such constraint, and the grid cost it
    real freshness — the newest day in a quantised preview is always the last
    one that *closed*, so the live view claimed "the 48 hours to the 3rd" while
    the reader was looking at it on the 5th. The rolling window ends now.
    """
    if kind not in digest_mod.KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"kind must be one of {', '.join(digest_mod.KINDS)}",
        )
    config = digest_mod.settings()
    if hours is not None:
        config = {**config, "window_hours": min(max(hours, 1), 24 * 90)}
    session = get_session(engine)
    try:
        built = digest_mod.build(
            session, kind, PROMPT_VERSIONS, datetime.now(timezone.utc), config,
            quantise=False,
        )
        return {
            **built,
            "window_start": built["window_start"].isoformat(),
            "window_end": built["window_end"].isoformat(),
        }
    finally:
        session.close()


@app.get("/api/register", dependencies=[Depends(require_auth)])
def register_view() -> dict:
    """The people register: totals per lab, evidence tiers, and move candidates.

    One response rather than three routes — the page shows all of it at once and
    the payload is a few kilobytes. `moveCandidates` is the reason the tab
    exists; `moveStats` reports how many cross-lab names the evidence gate
    dropped, because a filter whose reduction is invisible is indistinguishable
    from no filter (app/people.py).
    """
    session = get_session(engine)
    try:
        return {
            **people_mod.overview(session),
            "moveCandidates": people_mod.move_candidates(session),
            "moveStats": people_mod.move_candidate_stats(session),
        }
    finally:
        session.close()


@app.get("/api/register/search", dependencies=[Depends(require_auth)])
def register_search(q: str, limit: int = 50) -> list[dict]:
    """People matching `q` by canonical name or by any recorded identity."""
    session = get_session(engine)
    try:
        return people_mod.search(session, q, limit=min(max(limit, 1), 200))
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
