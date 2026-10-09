"""Run tracking: every pipeline command records what it did and how it ended.

`status='failed' AND alerted_at IS NULL` is the standing query for a
system-failure alerter — distinct, by design, from content alerts. The
notifier itself is deferred (docs/decisions.md); the state it needs is not.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app import models as m
from app.models import utcnow


@contextmanager
def tracked(
    session: Session, kind: str, stats: dict | None = None,
    run: m.PipelineRun | None = None,
) -> Iterator[m.PipelineRun]:
    """Open a pipeline_runs row around a unit of work, as one transaction.

    The body's stages flush rather than commit, so this context manager owns the
    single commit. That matters because a load begins by deleting the whole
    derived layer: without one transaction spanning the wipe and the rebuild, a
    failure anywhere downstream would leave the database empty rather than
    stale, and empty is the worse of the two.

    Args:
        session: Open session; the run row is committed on entry so a crash
            leaves a visible `running` corpse rather than nothing.
        kind: What ran — load | rebuild | connect | scheduled.
        stats: Mutable dict the caller fills stage by stage. Recorded on both
            the success and the failure path, so a failed run says how far it
            got instead of reporting an empty `{}`.
        run: An existing run row to record against instead of opening one. The
            scheduled worker uses this: its firing is a single run that spans
            ingestion (which commits per source, because a fetch that happened
            is a fact and must survive a later failure) and the ETL (which must
            stay atomic). Passing the row in keeps that one firing as one row
            rather than two. When reused, the caller owns marking it succeeded —
            the ETL finishing is not the whole firing finishing.

    Yields:
        The run row; the caller fills `watermarks` and `cost_usd`.

    Raises:
        Whatever the body raised, after recording it on the run row.
    """
    owned = run is None
    if owned:
        run = m.PipelineRun(kind=kind)
        session.add(run)
        session.commit()
    try:
        yield run
    except Exception as exc:
        # Rolling back discards the body's whole transaction — including the
        # ref wipe — so the previous good state survives the failure. The run
        # row itself is safe: it was committed on entry, and anything an earlier
        # phase committed against it (ingestion) is equally untouched.
        session.rollback()
        run.status, run.error, run.finished_at = "failed", str(exc), utcnow()
        if stats is not None:
            run.stats = {**(run.stats or {}), **stats}
        session.commit()
        raise
    if owned:
        run.status, run.finished_at = "succeeded", utcnow()
    if stats is not None:
        run.stats = {**(run.stats or {}), **stats}
    session.commit()


def watermarks(session: Session) -> dict:
    """Per-lab high-water marks from the clean articles table.

    Returns:
        ``{lab: {"max_published": iso-date, "articles": count}}``. Recorded on
        every run; not yet consumed by fetch (flagged in docs/decisions.md).
    """
    rows = session.execute(
        select(m.Article.lab, func.max(m.Article.published_on), func.count())
        .group_by(m.Article.lab)
    ).all()
    return {lab: {"max_published": str(latest), "articles": n} for lab, latest, n in rows}


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
