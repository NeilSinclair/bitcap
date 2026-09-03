"""Read model for the operational surface: runs, sources, alerts, drift, spend.

The pipeline already records all of this — `pipeline_runs`, `run_sources`,
`source_state`, `alerts`, `gold_snapshots`. Until now none of it was reachable
by anything but psql, which meant the system-failure alerting CLAUDE.md requires
existed in the database and nowhere a person would look.

Read-only, and deliberately assembled here rather than in `api/queries.py`:
that module builds the *content* read model (articles, tags, connections) and
this one answers "is the pipeline healthy", which is a different question for a
different reader.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as m
from app.pipeline.budget import Budget, month_to_date

# How far back the header badge counts. `alerts` has no resolved/acknowledged
# column and nothing ever deletes a row, so an all-time count can only ever
# rise — one transient outage in week one would leave the badge red forever,
# which is exactly the "trains everyone to mute it" failure the alerting design
# works to avoid. A window is the cheapest honest answer: the badge asks "has
# anything broken lately", not "has anything ever broken".
BADGE_WINDOW_DAYS = 7


def _iso(value: datetime | None) -> str | None:
    """ISO-8601 that always carries an offset.

    sqlite returns naive datetimes even for a `DateTime(timezone=True)` column,
    so an offset-free string reaches the browser and `new Date()` reads it as
    *local* time — every "2h ago" wrong by the local UTC offset. Postgres is
    unaffected, but the README sells sqlite as the no-setup path.
    """
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _run_dict(run: m.PipelineRun) -> dict:
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "cost_usd": run.cost_usd,
        "error": run.error,
        "stats": run.stats or {},
        "watermarks": run.watermarks or {},
    }


def run_history(session: Session, limit: int = 20) -> list[dict]:
    """Recent runs, newest first, each with its per-source detail.

    `run_sources` is what makes "the run succeeded" and "every source succeeded"
    separable claims — a firing that lost one lab is `succeeded` by design
    (docs/decisions.md D27), so the run row alone would hide the failure.

    Args:
        session: Open session.
        limit: How many runs.

    Returns:
        Run dicts with a `sources` list attached.
    """
    runs = list(session.scalars(
        select(m.PipelineRun).order_by(m.PipelineRun.id.desc()).limit(limit)
    ))
    if not runs:
        return []

    ids = [r.id for r in runs]
    by_run: dict[int, list[dict]] = {i: [] for i in ids}
    for row in session.scalars(
        select(m.RunSource).where(m.RunSource.run_id.in_(ids))
        .order_by(m.RunSource.leg, m.RunSource.source_id)
    ):
        by_run[row.run_id].append({
            "leg": row.leg,
            "source_id": row.source_id,
            "status": row.status,
            "items_seen": row.items_seen,
            "cost_usd": row.cost_usd,
            "duration_s": row.duration_s,
            "error": row.error,
        })

    out = []
    for run in runs:
        entry = _run_dict(run)
        entry["sources"] = by_run.get(run.id, [])
        entry["sources_failed"] = sum(1 for s in entry["sources"] if s["status"] == "failed")
        out.append(entry)
    return out


def source_states(session: Session) -> list[dict]:
    """Every source's cross-run state, worst first.

    Sorted by consecutive failures so a reader sees what is broken without
    scanning; a healthy register is a page of zeroes, which is the point.
    """
    rows = session.scalars(select(m.SourceState)).all()
    return sorted(
        (
            {
                "leg": s.leg,
                "source_id": s.source_id,
                "consecutive_failures": s.consecutive_failures,
                "disabled": s.disabled,
                "last_success_at": _iso(s.last_success_at),
                "last_attempt_at": _iso(s.last_attempt_at),
                "last_error": s.last_error,
                "watermark": s.watermark or {},
            }
            for s in rows
        ),
        key=lambda s: (-s["consecutive_failures"], s["leg"], s["source_id"]),
    )


def recent_system_alerts(session: Session, days: int = BADGE_WINDOW_DAYS) -> int:
    """System alerts raised in the last `days` — the header badge's number.

    Windowed, not all-time. Nothing resolves or deletes an alert, so an all-time
    count only ever rises and the badge would stay red forever after a single
    transient outage.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return int(session.scalar(
        select(func.count()).select_from(m.Alert)
        .where(m.Alert.kind == "system", m.Alert.created_at >= since)
    ) or 0)


def health(session: Session, escalation_threshold: int | None = None) -> dict:
    """One object answering "is this pipeline working, and what has it cost".

    Args:
        session: Open session.
        escalation_threshold: Consecutive failures before a source counts as
            failing. Defaults to `alerts.source_down_runs`, so the number the
            page shows in red is the same one that raises an alert — showing
            red at the first blip while the page's own note says "escalated
            after three" is a contradiction a reader has to resolve.

    Returns:
        Latest run, per-source state, month-to-date spend against the ceiling,
        recent system alerts, and the corpus counts a reader needs to tell a
        healthy quiet run from an empty one.
    """
    if escalation_threshold is None:
        from app.pipeline.alerts import settings as alert_settings

        escalation_threshold = int(alert_settings().get("source_down_runs", 3))
    latest = session.scalars(
        select(m.PipelineRun).order_by(m.PipelineRun.id.desc()).limit(1)
    ).first()
    budget = Budget.from_config(session)
    states = source_states(session)

    counts = {
        name: session.scalar(select(func.count()).select_from(table))
        for name, table in (
            ("articles", m.Article),
            ("classifications", m.Classification),
            ("connections", m.Connection),
            ("people", m.Person),
            ("unresolved_items", m.UnresolvedItem),
        )
    }

    return {
        "latest_run": _run_dict(latest) if latest else None,
        "sources": states,
        # At the escalation threshold, not at the first blip — the same number
        # that raises `source_down`.
        "sources_failing": [
            s for s in states
            if s["consecutive_failures"] >= escalation_threshold and not s["disabled"]
        ],
        "sources_wobbling": [
            s for s in states
            if 0 < s["consecutive_failures"] < escalation_threshold
        ],
        "escalation_threshold": escalation_threshold,
        "spend": {
            "month_usd": round(month_to_date(session), 6),
            "month_limit": budget.per_month_usd,
            "run_limit": budget.per_run_usd,
        },
        "recent_system_alerts": recent_system_alerts(session),
        "badge_window_days": BADGE_WINDOW_DAYS,
        "counts": counts,
    }
