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
def tracked(session: Session, kind: str) -> Iterator[m.PipelineRun]:
    """Open a pipeline_runs row around a unit of work.

    Args:
        session: Open session; the run row is committed on entry so a crash
            leaves a visible `running` corpse rather than nothing.
        kind: One of load_refs | load_raw | transform | connect | rebuild.

    Yields:
        The run row; the caller fills `stats`, `watermarks`, `cost_usd`.

    Raises:
        Whatever the body raised, after recording it on the run row.
    """
    run = m.PipelineRun(kind=kind)
    session.add(run)
    session.commit()
    try:
        yield run
    except Exception as exc:
        # The session may hold an aborted transaction; recording the failure
        # needs a clean one. The run row itself survives — it was committed
        # on entry.
        session.rollback()
        run.status, run.error, run.finished_at = "failed", str(exc), utcnow()
        session.commit()
        raise
    run.status, run.finished_at = "succeeded", utcnow()
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
