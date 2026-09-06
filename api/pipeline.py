"""Trigger a pipeline run from the browser, and report on it while it runs.

The scheduled worker is a Render cron job and fires once a night. This is the
same `run_once`, started by hand, for the times you do not want to wait for
3am — a source was fixed, a prompt changed, or a demo is in ten minutes.

**It runs in a thread, not in the request.** A firing takes 9 to 30 minutes and
no HTTP client waits that long, so `POST /api/pipeline/run` starts the work and
returns a run id immediately. The browser then polls
`GET /api/pipeline/run/{id}`. The run's real state was already durable — the
worker writes `pipeline_runs` and `run_sources` as it goes — so polling reads
the database rather than any in-process bookkeeping, and a restarted API
process reports the truth about a run it did not start.

**One at a time, enforced by the database.** Two concurrent firings interleave
writes to the same corpus file and both do read-modify-write on the cost log,
silently losing records. A process-local lock cannot help: the cron is a
separate process in a separate container. The guarantee is a partial unique
index on `pipeline_runs.status = 'running'` (migration 0006, D44) — the
`running_run` query here only exists so the 409 can say *which* run is in the
way, and the cron obeys the same constraint without knowing this module exists.

**A crashed run must not look like a running one.** The thread wraps `run_once`
so that an exception marks the row `failed`; without it a dead thread leaves a
`running` row for ever and the concurrency guard blocks every later attempt.
"""

from __future__ import annotations

import threading
import traceback
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from api.auth import require_auth
from app import models as m
from app.db import get_session
from app.pipeline import worker
from app.pipeline.registry import LEGS, load_sources

# What the run row's `kind` is set to. Distinct from the cron's "scheduled" so
# the two are separable in the run history and in `firing_number`, which counts
# scheduled firings to decide cadence — a manual run must not advance that
# counter, or clicking the button would silently skip the GitHub leg's turn.
KIND = "manual"

# Human-facing copy for the four checkboxes. Kept here rather than in the
# frontend so the page cannot drift out of step with what the backend accepts.
_LEG_NOTES = {
    "announcements": "Lab blogs and newsrooms. The insight stream the digest is built from.",
    "papers": "arXiv and lab publications, with byline extraction for the people register.",
    "github": "Commit history across each lab's orgs. The slow one — around 14 minutes.",
    "releases": "Release notes from each lab's watched repositories. Runs after GitHub, which supplies the repository list.",
    "posts": "X posts from the handles in the people register. Billed per post read, so it asks only for what is new.",
    "drift": "Re-scores 20 gold articles and compares. Costs about $0.75 every time.",
}


class RunRequest(BaseModel):
    """What the browser asks for."""

    legs: list[str] = Field(default_factory=list)
    drift: bool = False
    dry_run: bool = False


# How long a `running` row is believed before it is treated as a corpse. The
# longest real firing measured is ~30 minutes (GitHub leg, 2,074 repos), so two
# hours is generous. Without this bound a run killed mid-flight — a redeploy, an
# OOM, `uvicorn --reload` picking up an edit — leaves a row `running` for ever,
# and since the concurrency guard is "is there a running row", that one corpse
# blocks every future run until somebody edits the database by hand. Observed
# twice in one afternoon of local development (D43).
STALE_RUN_HOURS = 2.0


def running_run(session, now: datetime | None = None) -> m.PipelineRun | None:
    """The firing currently in flight, if any — manual or scheduled.

    Reaps as it reads: a `running` row older than :data:`STALE_RUN_HOURS` cannot
    be a live firing, so it is marked failed and ignored rather than blocking
    the caller for ever. Recording it as `failed` rather than deleting it also
    lets `alerts.run_failed` see it, which a silently-cleared row never would.

    Args:
        session: Open session.
        now: Injectable clock for the tests.

    Returns:
        The in-flight run, or None.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=STALE_RUN_HOURS)

    live = None
    for run in session.scalars(
        select(m.PipelineRun)
        .where(m.PipelineRun.status == "running")
        .order_by(desc(m.PipelineRun.id))
    ):
        started = run.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started is not None and started < cutoff:
            run.status = "failed"
            run.error = (
                f"no longer running: started {started.isoformat()} and exceeded "
                f"the {STALE_RUN_HOURS}h ceiling without finishing. The process "
                "was almost certainly killed (redeploy, restart, or OOM)."
            )
            run.finished_at = now
            continue
        if live is None:
            live = run
    session.commit()
    return live


def _describe(run: m.PipelineRun) -> dict:
    """Serialise a run row for the pipeline tab.

    Returns:
        Status, timings, cost and per-source rows. `duration_s` is computed
        against *now* while a run is still going, so the page can count up
        without having to know when it started.
    """
    started, finished = run.started_at, run.finished_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished is not None and finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    end = finished or datetime.now(timezone.utc)
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat() if finished else None,
        "duration_s": round((end - started).total_seconds(), 1) if started else None,
        "cost_usd": round(run.cost_usd or 0.0, 4),
        "error": run.error,
        "stats": run.stats or {},
    }


def _sources_of(run_id: int, session) -> list[dict]:
    """Per-source outcomes for one run, in the order they were recorded."""
    rows = session.scalars(
        select(m.RunSource).where(m.RunSource.run_id == run_id).order_by(m.RunSource.id)
    )
    return [
        {
            "leg": r.leg, "source_id": r.source_id, "status": r.status,
            "items_seen": r.items_seen, "duration_s": r.duration_s,
            "cost_usd": round(r.cost_usd or 0.0, 4), "error": r.error,
        }
        for r in rows
    ]


def _run_in_thread(engine: Engine, run_id: int, legs: tuple[str, ...],
                   drift: bool, spend: bool) -> None:
    """Execute one firing off the request thread, and never leave it `running`.

    `run_once` is handed the row this thread's caller already claimed, so the
    firing is one row rather than a placeholder plus a real one, and it closes
    the row out itself on both the success and the failure path.

    The wrapper here is for the narrow case `run_once` cannot cover: a failure
    *before* it reaches its own try block — a malformed `config/pipeline.yaml`,
    say. Without this the row stays `running` for ever, and since the
    concurrency guard is a query for a `running` row, one such corpse blocks
    every future run until somebody edits the database by hand.

    Args:
        engine: The app's engine; the thread opens its own session because a
            SQLAlchemy Session is not safe to share across threads.
        run_id: The row claimed synchronously by the request.
        legs: Ingestion legs to run.
        drift: Whether to run the drift check.
        spend: False for a dry run.
    """
    session = get_session(engine)
    try:
        run = session.get(m.PipelineRun, run_id)
        try:
            # `legs`, never `legs or None`: an empty selection means "no
            # ingestion legs", and None would hand the decision back to cadence.
            worker.run_once(session, legs=legs, spend=spend,
                            deliver=spend, drift=drift, run=run)
        except (Exception, SystemExit):
            # A thread's exception goes nowhere by default: no request is
            # waiting on it and nothing else will ever see it. Recording it on
            # the row is the only way the browser, or anyone reading the run
            # history later, learns the firing died.
            traceback.print_exc()
            session.rollback()
            if run is not None and run.status == "running":
                run.status = "failed"
                run.error = traceback.format_exc()[-2000:]
                run.finished_at = datetime.now(timezone.utc)
                session.commit()
    finally:
        session.close()


def build_router(engine: Engine) -> APIRouter:
    """Wire the routes to the process's engine.

    A factory rather than module-level globals so the tests can build a router
    against a throwaway database without monkeypatching import-time state.

    The `APIRouter` is created *here*, not at module scope. A shared one
    accumulates a fresh copy of every route on each call, each closed over a
    different engine, and FastAPI answers with whichever was registered first —
    so a second call silently binds the app to the first call's database.

    Args:
        engine: The engine every route and background thread should use.

    Returns:
        The configured router.
    """
    router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

    @router.get("/legs", dependencies=[Depends(require_auth)])
    def list_legs() -> list[dict]:
        """The checkboxes, with how many sources each one covers."""
        out = []
        for leg in LEGS:
            sources = load_sources(legs=(leg,))
            out.append({
                "id": leg,
                "sources": len(sources),
                "enabled_sources": sum(1 for s in sources if s.enabled),
                "note": _LEG_NOTES.get(leg, ""),
            })
        # Drift is not an ingestion leg — it re-scores a fixed gold set — but it
        # is a phase an operator chooses, so it is a checkbox like the others.
        out.append({"id": "drift", "sources": 0, "enabled_sources": 0,
                    "note": _LEG_NOTES["drift"]})
        return out

    @router.post("/run", dependencies=[Depends(require_auth)])
    def start_run(body: RunRequest) -> dict:
        """Start a firing and return immediately with its run id.

        Raises:
            HTTPException: 400 on an unknown leg or an empty selection; 409 when
                a run is already in flight.
        """
        unknown = set(body.legs) - set(LEGS)
        if unknown:
            raise HTTPException(400, f"unknown leg(s): {sorted(unknown)}")
        if not body.legs and not body.drift:
            raise HTTPException(400, "select at least one leg")

        session = get_session(engine)
        try:
            in_flight = running_run(session)
            if in_flight is not None:
                raise HTTPException(
                    409,
                    f"run {in_flight.id} ({in_flight.kind}) is already in flight",
                )
            # Claimed synchronously, before the thread starts. The `running_run`
            # check above is for the *message* — it can name the run that is in
            # the way — but the guarantee is the partial unique index on
            # `status='running'` (D44), which is the only thing that also binds
            # the cron in its own container. Two requests in the same instant
            # both pass the SELECT; only one survives the INSERT.
            claimed = m.PipelineRun(kind=KIND, status="running")
            session.add(claimed)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(
                    409, "another pipeline run started first"
                ) from exc
            run_id = claimed.id
        finally:
            session.close()

        threading.Thread(
            target=_run_in_thread,
            args=(engine, run_id, tuple(body.legs), body.drift, not body.dry_run),
            daemon=True,
            name=f"pipeline-run-{run_id}",
        ).start()
        return {"run_id": run_id, "status": "running"}

    @router.get("/run/{run_id}", dependencies=[Depends(require_auth)])
    def run_status(run_id: int) -> dict:
        """One run's live state, including the sources it has finished."""
        session = get_session(engine)
        try:
            run = session.get(m.PipelineRun, run_id)
            if run is None:
                raise HTTPException(404, f"no run {run_id}")
            return {**_describe(run), "sources": _sources_of(run_id, session)}
        finally:
            session.close()

    @router.get("/current", dependencies=[Depends(require_auth)])
    def current() -> dict:
        """Whatever is in flight now, or the last run that finished.

        What the page loads with: reopening the tab mid-run has to show the run,
        not an idle button that invites a second one.
        """
        session = get_session(engine)
        try:
            run = running_run(session) or session.scalars(
                select(m.PipelineRun).order_by(desc(m.PipelineRun.id)).limit(1)
            ).first()
            if run is None:
                return {"run": None, "sources": []}
            return {"run": _describe(run), "sources": _sources_of(run.id, session)}
        finally:
            session.close()

    return router
