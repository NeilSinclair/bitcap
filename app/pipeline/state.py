"""Per-source state that outlives a single run.

Per-request backoff (3-4 attempts, seconds to tens of seconds, already in every
fetcher) is the right answer to one bad request and the wrong one for a source
that is down for hours. It either gives up inside the run or, tuned generous
enough to ride out a real outage, blocks the whole firing. Neither is what is
needed (docs/planning.md §4b).

What is needed is a slower layer, and this is it. **A failing source is retried
on every firing, normally** — the next run's own per-request retries are exactly
the right thing for a source that was down for ten minutes, and skipping it
would turn a transient outage into a self-inflicted one. What changes is that
the failures are *counted*: a source still down after N consecutive scheduled
runs is an incident, not noise, and `consecutive_failures` is what makes that a
query instead of a guess.

Deliberately absent: cross-run backoff. It was considered and left out — it
would suppress precisely the retry that fixes the common case, to save a few
seconds of a fetch that is cached anyway. `disabled` is the escape hatch for a
source that should genuinely stop being called, and it is set by a human.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app import models as m
from app.models import utcnow

MAX_ERROR_CHARS = 2000


def load(session: Session, leg: str, source_id: str) -> m.SourceState:
    """Fetch a source's state, creating it on first sight.

    Args:
        session: Open session; the caller owns the commit.
        leg: Leg name.
        source_id: Source id within the leg.

    Returns:
        The persistent row. A source seen for the first time starts at zero
        failures with an empty watermark, which is indistinguishable from a
        healthy source — correct, because it has not failed.
    """
    state = session.get(m.SourceState, (leg, source_id))
    if state is None:
        state = m.SourceState(leg=leg, source_id=source_id)
        session.add(state)
        session.flush()
    return state


def record_attempt(state: m.SourceState, now: datetime | None = None) -> None:
    """Stamp the attempt before it is made, so a crash still leaves a trace."""
    state.last_attempt_at = now or utcnow()


def record_success(
    state: m.SourceState, watermark: dict | None = None, now: datetime | None = None
) -> None:
    """Clear the failure streak and advance the watermark.

    Resetting `consecutive_failures` to zero on any success is deliberate: the
    question the counter answers is "is this source down *now*", not "how
    unreliable has it been historically". A source that fails every other run is
    a different problem, visible in `run_sources`, and conflating the two would
    make the alert fire on flakiness rather than on outages.

    Args:
        state: Row to update.
        watermark: Leg-specific high-water mark; ``None`` leaves the previous
            one in place rather than erasing it.
        now: Injectable clock for the tests.
    """
    at = now or utcnow()
    state.last_success_at = at
    state.last_attempt_at = at
    state.consecutive_failures = 0
    state.last_error = None
    if watermark:
        state.watermark = watermark


def record_failure(
    state: m.SourceState, error: BaseException | str, now: datetime | None = None
) -> None:
    """Increment the failure streak and keep the reason.

    The watermark is **not** touched. It records how far this source got the
    last time it worked, and a failed run learned nothing about that — clearing
    it would make the next run refetch a window it already has.
    """
    state.last_attempt_at = now or utcnow()
    state.consecutive_failures += 1
    state.last_error = str(error)[:MAX_ERROR_CHARS]


def failing(session: Session, threshold: int) -> list[m.SourceState]:
    """Sources that have failed at least `threshold` consecutive runs.

    This is the query behind the `source_down` system alert. Manually disabled
    sources are excluded: an operator who switched a source off does not need to
    be told it is not running.

    Args:
        session: Open session.
        threshold: Consecutive-failure count that counts as an incident.

    Returns:
        Matching rows, worst first.
    """
    rows = session.query(m.SourceState).filter(
        m.SourceState.consecutive_failures >= threshold,
        m.SourceState.disabled.is_(False),
    )
    return sorted(rows, key=lambda s: -s.consecutive_failures)
