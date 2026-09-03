"""Run tracking: every pipeline command records what it did and how it ended.

`status='failed' AND alerted_at IS NULL` is the standing query for a
system-failure alerter — distinct, by design, from content alerts. The
notifier itself is deferred (docs/decisions.md); the state it needs is not.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import func, select
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
