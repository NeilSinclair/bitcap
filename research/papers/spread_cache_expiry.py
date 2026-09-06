"""One-off: stagger the expiry dates already sitting in `fetch_cache`.

`fetch_cache._ttl` now spreads a discovery URL's expiry per URL, but only as it
is *written*. Rows cached before that change still carry the flat TTL they were
stamped with, so they still all come due on the same night -- and that night is
the burst this was written to stop. Measured on 2026-09-06: 35 rows, one expiry
date.

So this rewrites what is already there. What it does to each row, exactly:

* **Marked listings are skipped entirely.** They are past due in production, so
  the next firing refetches them under the 24h listing policy -- which is the
  behaviour wanted, and spreading them would put the three URLs that decide how
  fast a new paper is found back on a multi-day lag.
* **A row already past due is spread forward**, across its own recorded TTL
  (capped at the discovery window), at a position taken from a stable hash of
  its URL. Leaving it where it is would have every one of them refetch on the
  very next firing -- the burst, one more time.
* **A row not yet due is only ever pulled earlier, never pushed out.** This
  breaks a herd; it does not grant rows a longer life.

Each row then re-jitters normally the next time it is fetched.

**Run this once, by hand.** It is deliberately not a migration -- it is data
hygiene on a cache, not a schema change, and a migration would run on every
deployment including ones that never had the herd. It must also never be wired
into a recurring path: the "past due -> spread forward" rule has no memory, so a
row that genuinely lapses would be pushed out another window every time the
script ran.

    uv run python research/papers/spread_cache_expiry.py --dry-run
    uv run python research/papers/spread_cache_expiry.py
"""

from __future__ import annotations

import argparse
import collections
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

import fetch_cache  # noqa: E402


# URLs the harvesters now mark `fetch_cache.LISTING`. Left alone entirely: they
# are already past due in production, and refetching them on the next firing is
# exactly what should happen — they come back under the 24h listing policy, and
# DeepSeek is fresh the next morning. Spreading them instead would push the
# three URLs that decide how fast a new paper is found out by up to a fortnight,
# which is the lag this change exists to remove.
#
# Duplicated from the three call sites rather than derived, because a URL's
# policy is decided by the caller and the database records only the URL. The
# duplication is pinned by a test that fails if the call sites move.
LISTING_MARKERS = (
    'search_query=au%3A%22DeepSeek-AI%22',   # deepseek_harvest.collect
    'search_query=au%3A"DeepSeek-AI"',       # the same, unencoded quotes
    "deepmind.google/sitemap.xml",           # deepmind_harvest.list_publication_urls
    "ai.meta.com/results/",                  # meta_harvest.list_paper_candidates
)


def is_listing(url: str) -> bool:
    """Whether this row is one the harvesters now refresh daily.

    Args:
        url: The row's URL.

    Returns:
        True if the URL is one of the marked listings.
    """
    return any(marker in url for marker in LISTING_MARKERS)


def spread(fetched_at: datetime, expires_at: datetime, url: str,
           now: datetime, window: timedelta) -> datetime:
    """Where this row should fall due, given where it falls due now.

    Three rules, and the second is what makes re-running this safe:

    **A row already past due is spread forward.** Leaving it where it is would
    have every one of them refetch on the very next firing — the burst, one more
    time, which is the thing being fixed.

    **A row not yet due is only ever pulled earlier, never pushed out.** This
    script exists to break a herd, not to quietly grant rows a longer life, and
    without it a second run would add another slice every time it was run. The
    property this gives is monotonicity, not a fixed point: a row with a small
    jitter fraction is pulled in a little on each run until it sits just short
    of due. It never lands in the past, and this is run once.

    **A row whose recorded TTL is not positive is left exactly as it is**, since
    nothing here can reason about it.

    The window is the row's *own* recorded TTL, capped at the discovery window.
    Using the global window for every row would stretch a short-lived row to a
    fortnight — which is how the first version of this quietly undid the 24h
    listing policy it ships alongside.

    Args:
        fetched_at: When the body was fetched.
        expires_at: When the row currently falls due.
        url: The row's URL; decides its position in the spread.
        now: Injectable clock.
        window: Ceiling on the spread, normally the discovery window.

    Returns:
        The new expiry.
    """
    recorded = expires_at - fetched_at
    if recorded <= timedelta(0):
        # An expiry at or before its own fetch. `db_put` cannot write one, so
        # this is a row nothing here understands -- leave it exactly as it is.
        # Falling back to the full discovery window would stretch it, which is
        # the one direction this function must never move a row it cannot
        # reason about.
        return expires_at
    span = min(recorded, window)
    proposed = now + span * fetch_cache._jitter(url)
    if expires_at <= now:
        return proposed
    return min(proposed, expires_at)


def main(argv: list[str] | None = None) -> int:
    """Rewrite `expires_at` on every row that has one, except marked listings.

    Args:
        argv: Command-line arguments; None reads `sys.argv`.

    Returns:
        0 on success, 1 when no database is reachable.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the resulting per-day counts, write nothing")
    args = parser.parse_args(argv)

    engine = fetch_cache._engine()
    if engine is None:
        print("no database reachable -- nothing to spread", file=sys.stderr)
        return 1

    from sqlalchemy import select

    from app import models as m
    from app.db import get_session

    now = datetime.now(timezone.utc)
    window = timedelta(hours=float(fetch_cache.settings()["discovery_ttl_hours"]))

    session = get_session(engine)
    try:
        rows = session.scalars(
            select(m.FetchCache).where(m.FetchCache.expires_at.is_not(None))
        ).all()
        before: collections.Counter = collections.Counter()
        after: collections.Counter = collections.Counter()
        skipped = 0
        for row in rows:
            if is_listing(row.url):
                skipped += 1
                continue
            fetched = row.fetched_at
            if fetched.tzinfo is None:  # sqlite hands these back naive
                fetched = fetched.replace(tzinfo=timezone.utc)
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            new = spread(fetched, expires, row.url, now, window)
            before[expires.date().isoformat()] += 1
            after[new.date().isoformat()] += 1
            if not args.dry_run:
                row.expires_at = new
        if not args.dry_run:
            session.commit()
    finally:
        session.close()

    print(f"{len(rows)} rows with an expiry"
          f"{' (dry run, nothing written)' if args.dry_run else ''}")
    if skipped:
        print(f"  {skipped} listing row(s) left alone -- they refetch on the next "
              "firing under the 24h listing policy, which is the point")
    print(f"  before: {len(before)} distinct date(s), busiest {max(before.values(), default=0)}")
    print(f"  after:  {len(after)} distinct date(s), busiest {max(after.values(), default=0)}")
    for day, count in sorted(after.items()):
        print(f"    {day}  {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
