"""A spending ceiling, checked where the money is actually spent.

Cost runaway is one of the failure modes CLAUDE.md names, and until this existed
there was no control on it at all — only a record, written after the fact. That
was tolerable while every LLM run was a human typing a command and watching the
total. It stops being tolerable the moment a cron fires unattended against a
EUR 100 budget.

This is also what makes the `SCORING_ENABLED` gate obsolete as a *safety*
mechanism (D10). That switch was off because an uncontrolled scoring run was
dangerous; the honest fix is not to leave the pipeline's most valuable stage
permanently disabled, but to bound it. The env var stays as a manual kill
switch, which is a different job.

Two ceilings, because they catch different things. `per_run` catches a loop or a
prompt regression inside one firing. `per_month` catches the slow leak — thirty
firings each individually reasonable and collectively half the budget.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as m

CONFIG = Path(__file__).parent.parent.parent / "config" / "pipeline.yaml"

# Seed for the per-call estimate, from the real cost log: $5.44 across the
# 191-article v7 corpus (docs/cost.md). Used only to decide whether to start
# another call, and replaced by the run's own observed mean after the first one.
DEFAULT_CALL_USD = 0.029


class BudgetExceeded(RuntimeError):
    """Raised when a call would take the run past its ceiling.

    Carries the numbers so the alert does not have to reconstruct them.
    """

    def __init__(self, scope: str, spent: float, limit: float):
        self.scope, self.spent, self.limit = scope, spent, limit
        super().__init__(
            f"{scope} budget exceeded: ${spent:.4f} spent against a ${limit:.2f} ceiling"
        )


@dataclass
class Budget:
    """A per-run and per-month spending ceiling.

    Thread-safe: the interactive classifier fans out over a dozen workers that
    all record cost, and an unguarded read-modify-write across them would
    undercount exactly when the total matters most.

    Attributes:
        per_run_usd: Ceiling for this run.
        per_month_usd: Ceiling for the calendar month.
        month_spent_before: What the month had already cost before this run,
            read from the durable cost record rather than assumed to be zero.
        run_spent: What this run has cost so far.
        calls: Billed calls this run, which is what turns `run_spent` into the
            running per-call mean the guard projects with.
    """

    per_run_usd: float
    per_month_usd: float
    month_spent_before: float = 0.0
    run_spent: float = 0.0
    calls: int = 0
    _in_flight: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @classmethod
    def from_config(cls, session: Session | None = None, path: Path = CONFIG) -> "Budget":
        """Build from config/pipeline.yaml, seeded with real month-to-date spend.

        Args:
            session: Open session; when given, month-to-date is read from
                `raw_costs`. Without one the month starts at zero, which is
                right for a test and wrong for production — hence the session
                is passed everywhere it exists.
            path: Config file.

        Returns:
            A budget ready to spend against.
        """
        config = yaml.safe_load(path.read_text(encoding="utf-8"))["budget"]
        return cls(
            per_run_usd=float(config["per_run_usd"]),
            per_month_usd=float(config["per_month_usd"]),
            month_spent_before=month_to_date(session) if session is not None else 0.0,
        )

    @property
    def month_spent(self) -> float:
        """Everything spent this calendar month, including this run."""
        return self.month_spent_before + self.run_spent

    @property
    def run_remaining(self) -> float:
        return max(0.0, self.per_run_usd - self.run_spent)

    @property
    def month_remaining(self) -> float:
        return max(0.0, self.per_month_usd - self.month_spent)

    @property
    def remaining(self) -> float:
        """Whichever ceiling binds first."""
        return min(self.run_remaining, self.month_remaining)

    @property
    def expected_per_call(self) -> float:
        """Running mean cost of a call, seeded until this run has evidence.

        The seed is the measured average from the real cost log (docs/cost.md:
        $5.44 across 191 classifications). It is only ever used to decide
        whether to *start* another call, so being roughly right is enough and
        being wrong is self-correcting after the first call completes.
        """
        return self.run_spent / self.calls if self.calls else DEFAULT_CALL_USD

    @property
    def projected(self) -> float:
        """Spend if every call currently in flight costs the running average.

        Calls already in flight cannot be un-billed, so the ceiling has to be
        tested against what the run is *committed* to, not what it has settled.
        """
        return self.run_spent + self._in_flight * self.expected_per_call

    @property
    def exhausted(self) -> bool:
        """True once either ceiling is reached, counting in-flight calls."""
        with self._lock:
            return self._over(extra=0)

    def _over(self, extra: int) -> bool:
        """Whether `extra` further calls would put the run past either ceiling."""
        committed = self.run_spent + (self._in_flight + extra) * self.expected_per_call
        return (
            committed >= self.per_run_usd
            or self.month_spent_before + committed >= self.per_month_usd
        )

    def begin_call(self) -> bool:
        """Claim headroom for one call. False means do not make it.

        This is the guard, and it is predictive rather than reactive: it asks
        whether *this* call would breach the ceiling, counting everything
        already in flight. Testing a plain running total instead lets a fan-out
        of N workers overshoot by up to N calls, because each worker sees a
        figure none of its peers have contributed to yet — measured live at 6.6x
        a small ceiling before this existed.

        Returns:
            True if the call may proceed; the caller must then call
            :meth:`end_call` exactly once, whatever the outcome.
        """
        with self._lock:
            if self._over(extra=1):
                return False
            self._in_flight += 1
            return True

    def end_call(self, usd: float = 0.0) -> None:
        """Settle a call started with :meth:`begin_call`.

        Args:
            usd: What it actually cost. Zero for a cached hit or a failure that
                was never billed — both still have to release their slot.
        """
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            if usd:
                self.run_spent += usd
                self.calls += 1

    def spend(self, usd: float) -> None:
        """Record spend that has already happened, outside a begin/end pair.

        Called at the point the cost record is written, never estimated ahead of
        the call. Recording *after* the fact is deliberate: the provider's own
        usage figures are the only honest source, and a call that has been made
        has been billed whether or not this pushes the total over.
        """
        with self._lock:
            self.run_spent += usd
            self.calls += 1

    def check(self) -> None:
        """Raise if either ceiling has been reached.

        Raises:
            BudgetExceeded: Naming whichever ceiling bound first, so the alert
                can say "this run" or "this month" rather than just "too much".
        """
        if self.run_remaining <= 0:
            raise BudgetExceeded("per-run", self.run_spent, self.per_run_usd)
        if self.month_remaining <= 0:
            raise BudgetExceeded("per-month", self.month_spent, self.per_month_usd)

    def snapshot(self) -> dict:
        """Numbers for the run record and the health endpoint."""
        return {
            "run_usd": round(self.run_spent, 6),
            "run_limit": self.per_run_usd,
            "month_usd": round(self.month_spent, 6),
            "month_limit": self.per_month_usd,
            "exhausted": self.exhausted,
        }


def month_to_date(session: Session, now: datetime | None = None) -> float:
    """Spend recorded so far this calendar month.

    Reads `raw_costs`, which is the durable copy of every per-call cost record
    the harvesters write. `at` is stored as the ISO string the log produced —
    precision is the log's, not ours — so the month is matched on its prefix
    rather than parsed, which avoids inventing a timezone the record never had.

    Args:
        session: Open session.
        now: Injectable clock.

    Returns:
        USD spent this month, 0.0 if nothing is recorded.
    """
    at = now or datetime.now(timezone.utc)
    month = at.strftime("%Y-%m")
    logged = session.scalar(
        select(func.coalesce(func.sum(m.RawCost.usd), 0.0)).where(
            m.RawCost.at.startswith(month)
        )
    )

    # The papers harvesters record cost at the call site but without a
    # timestamp, so those rows cannot enter `raw_costs` (unique on url+at)
    # without one being invented. Their spend is attributed per source in
    # `run_sources` instead. Non-overlapping by construction: announcement
    # classification and drift never write `run_sources.cost_usd` — that is a
    # fetch-phase column, and their spend is already in the log above.
    start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    ingested = session.scalar(
        select(func.coalesce(func.sum(m.RunSource.cost_usd), 0.0))
        .join(m.PipelineRun, m.PipelineRun.id == m.RunSource.run_id)
        .where(m.PipelineRun.started_at >= start)
    )
    return float(logged or 0.0) + float(ingested or 0.0)
