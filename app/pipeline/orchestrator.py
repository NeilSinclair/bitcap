"""The ingestion loop: run every source, and survive the ones that fail.

Every harvester already isolates failures *within* itself, per item. Nothing
isolated them *across* sources, so a single dead lab took the whole leg with it
(docs/handover.md §6). That is what this fixes, and it is most of what "build the
production pipeline" means here.

Two properties are load-bearing and both are tested:

* **A failing source costs exactly itself.** Its exception is caught, counted
  against its own state, recorded on the run, and the loop continues.
* **Ingestion commits as it goes.** Deliberately unlike the ETL, which
  `app.runs.tracked` wraps in one transaction so a mid-load failure leaves the
  database stale rather than empty. A fetch that succeeded is a fact about the
  world; rolling it back because a *later* source failed would throw away work
  that was really done and re-spend money to redo it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app import models as m
from app.pipeline import state as state_mod
from app.pipeline.adapters import FetchResult, adapter_for
from app.pipeline.registry import Source, load_sources

SUCCEEDED, FAILED, SKIPPED = "succeeded", "failed", "skipped"


@dataclass
class SourceOutcome:
    """What happened to one source on one run."""

    source: Source
    status: str
    result: FetchResult | None = None
    error: str | None = None
    duration_s: float = 0.0

    @property
    def items(self) -> list[dict]:
        return self.result.items if self.result else []

    def leg_is(self, leg: str) -> bool:
        return self.source.leg == leg


@dataclass
class RunReport:
    """Everything the ingestion phase did, for the caller to act on."""

    outcomes: list[SourceOutcome] = field(default_factory=list)

    def by_status(self, status: str) -> list[SourceOutcome]:
        return [o for o in self.outcomes if o.status == status]

    def items_for(self, leg: str) -> list[dict]:
        """Every item produced by successful sources in one leg."""
        return [
            item
            for o in self.outcomes
            if o.leg_is(leg) and o.status == SUCCEEDED
            for item in o.items
        ]

    def raw_repos(self) -> list[dict]:
        """Repository histories fetched this run, from every successful source.

        Bronze for the github leg. `items_for` returns the people aggregate,
        which is derived; this is the material it was derived from (D32).
        """
        return [
            repo
            for o in self.outcomes
            if o.status == SUCCEEDED and o.result
            for repo in o.result.raw_repos
        ]

    @property
    def stats(self) -> dict:
        """Counts suitable for `pipeline_runs.stats`."""
        return {
            "sources": len(self.outcomes),
            "succeeded": len(self.by_status(SUCCEEDED)),
            "failed": len(self.by_status(FAILED)),
            "skipped": len(self.by_status(SKIPPED)),
            "items": sum(len(o.items) for o in self.outcomes),
            "cost_usd": round(sum(o.result.cost_usd for o in self.outcomes if o.result), 6),
        }

    @property
    def ok(self) -> bool:
        """True when nothing failed. Skipped sources are a choice, not a fault."""
        return not self.by_status(FAILED)


BUDGET_SKIPPED = "budget exhausted before this source ran"


def run_source(
    session: Session, source: Source, run_id: int | None = None, budget=None
) -> SourceOutcome:
    """Attempt one source, recording the outcome whichever way it goes.

    Never raises for a source-level failure — that is the whole point. A bug in
    this function itself will still propagate, which is correct: an orchestrator
    that silently swallows its own defects is worse than one that stops.

    `SystemExit` is caught alongside `Exception` because the harvesters are
    scripts first: `harvest_github.load_token` calls `sys.exit()` when
    `GITHUB_TOKEN` is unset, and `SystemExit` derives from `BaseException`, so a
    bare `except Exception` lets it past both this handler and the worker's.
    The observed result was the worst available: the firing aborted mid-ingest,
    the run row stayed `running` forever, `record_failure` was never reached so
    `consecutive_failures` stayed 0, and neither `run_failed` nor `source_down`
    could ever match it. `KeyboardInterrupt` is deliberately NOT caught — an
    operator pressing ctrl-C means stop, not "mark this source failed".

    Args:
        session: Open session. Committed here, per the module docstring.
        source: The source to attempt.
        run_id: Run to attribute the attempt to, if there is one.
        budget: Optional ceiling. The papers leg spends on LLM byline
            extraction, so ingestion is not a free phase and cannot be left
            outside the ceiling — a cold container re-extracts every lab from
            scratch. Checked before a source starts and charged after it
            finishes; the announcements and github legs simply report zero.

    Returns:
        The outcome, including the fetched items on success.
    """
    st = state_mod.load(session, source.leg, source.id)

    if not source.enabled or st.disabled:
        reason = "disabled in config" if not source.enabled else "disabled by operator"
        outcome = SourceOutcome(source=source, status=SKIPPED, error=reason)
        _record(session, outcome, run_id)
        return outcome

    if budget is not None and budget.exhausted:
        outcome = SourceOutcome(source=source, status=SKIPPED, error=BUDGET_SKIPPED)
        _record(session, outcome, run_id)
        return outcome

    state_mod.record_attempt(st)
    started = time.monotonic()
    try:
        result = adapter_for(source)(source, st, session)
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        state_mod.record_failure(st, exc)
        outcome = SourceOutcome(
            source=source,
            status=FAILED,
            error=f"{type(exc).__name__}: {exc}",
            duration_s=time.monotonic() - started,
        )
    else:
        state_mod.record_success(st, result.watermark)
        # Both, and the difference matters. `cost_usd` is spend recorded nowhere
        # else and goes on to `run_sources`; `metered_usd` is spend the source
        # already wrote to the cost log, which must still be charged to *this*
        # run's ceiling or the firing that spends the money sees none of it —
        # `month_spent_before` is snapshotted at construction, so it would only
        # surface on the next run.
        spent = result.cost_usd + result.metered_usd
        if budget is not None and spent:
            budget.spend(spent)
        outcome = SourceOutcome(
            source=source,
            status=SUCCEEDED,
            result=result,
            duration_s=time.monotonic() - started,
        )
    _record(session, outcome, run_id)
    return outcome


def _record(session: Session, outcome: SourceOutcome, run_id: int | None) -> None:
    """Persist the attempt, then commit so it survives a later source dying."""
    if run_id is not None:
        session.add(
            m.RunSource(
                run_id=run_id,
                leg=outcome.source.leg,
                source_id=outcome.source.id,
                status=outcome.status,
                items_seen=len(outcome.items),
                cost_usd=outcome.result.cost_usd if outcome.result else 0.0,
                duration_s=round(outcome.duration_s, 3),
                error=outcome.error,
            )
        )
    session.commit()


def ingest(
    session: Session,
    sources: list[Source] | None = None,
    run_id: int | None = None,
    on_progress=None,
    budget=None,
) -> RunReport:
    """Run every source in stage order, isolating failures.

    Args:
        session: Open session.
        sources: Registry to run; defaults to every configured source.
        run_id: Run to attribute each attempt to.
        on_progress: Optional callback taking each :class:`SourceOutcome` as it
            completes, so a long run can report before it finishes.
        budget: Optional ceiling; the papers leg spends on LLM extraction.

    Returns:
        A report over every source attempted.
    """
    report = RunReport()
    for source in sources if sources is not None else load_sources():
        outcome = run_source(session, source, run_id, budget)
        report.outcomes.append(outcome)
        if on_progress:
            on_progress(outcome)
    return report
