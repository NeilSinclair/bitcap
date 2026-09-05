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

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as m
from app.pipeline.budget import Budget, month_to_date

# The header badge counts unacknowledged system alerts, all of them, with no
# time window.
#
# There used to be a seven-day window here, and its own justification was that
# `alerts` had no acknowledged column and nothing ever deleted a row, so an
# all-time count could only rise and one transient outage would leave the badge
# red forever — the "trains everyone to mute it" failure. `acknowledged_at` and
# the clear button retired that premise, and the window then did active harm in
# two ways, one of them new:
#
# * An alert nobody acknowledged fell out of the badge after a week on its own.
#   A real, unhandled failure went quiet by the passage of time.
# * `dispatch` withdrawing an acknowledgement changed nothing a reader saw once
#   the row was older than the window, because reopening does not move
#   `created_at`. Probed: day 10 of an unresolved outage reported
#   `reopened: 1` nightly with the badge sitting at 0 — the safety property
#   working perfectly and being invisible, at exactly the "week-long outage"
#   length the alerting design uses as its case.
#
# Age is not evidence a fault was handled. Acknowledgement is, and it is now a
# thing an operator can actually express.


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


def unacknowledged_system_alerts(session: Session) -> int:
    """Unacknowledged system alerts — the header badge's number.

    Not time-windowed; see the note on that above. A badge that is red on a
    healthy pipeline is a badge nobody reads, so it has to be clearable — and
    `alerts.acknowledge` clears it without deleting anything, the rows staying
    in the history below, still listed and marked.

    Acknowledging cannot silence a live fault, but not for the reason it is
    tempting to give: a still-failing source does *not* raise a fresh alert,
    because `dedupe_key` identifies the episode and holds still for as long as
    the fault lasts. `alerts.dispatch` is what closes it — regenerating an
    acknowledged row's key clears `acknowledged_at`, so this count rises again
    on the next firing, however old the row is.
    """
    return int(session.scalar(
        select(func.count()).select_from(m.Alert)
        .where(m.Alert.kind == "system", m.Alert.acknowledged_at.is_(None))
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
        "unacknowledged_system_alerts": unacknowledged_system_alerts(session),
        # Transitional duplicate of the line above, under the key this field had
        # before it was renamed. `render.yaml` deploys the API and the static
        # frontend as two independent services, so on a blueprint sync there is
        # a window where one is live and the other is not. A browser holding the
        # old bundle reads `recent_system_alerts`, and without this it would get
        # `undefined`, fall through the `?? 0`, and paint the Health badge green
        # while system alerts were outstanding — a false green on the one
        # indicator this whole feature exists to keep honest. Drop it on the
        # next release that touches this file; the frontend already prefers the
        # new key and only falls back.
        "recent_system_alerts": unacknowledged_system_alerts(session),
        "counts": counts,
    }
