"""`alerts.acknowledged_at`: clear the health badge without erasing the history.

The header badge counts outstanding system alerts. Nothing resolved them, so one
transient outage left it red indefinitely — the "trains everyone to mute it"
failure the alerting design exists to avoid, made worse by the badge being the
only thing that makes the ops page get opened. (A seven-day window used to hide
that, and hid real unhandled failures with it; this column is what replaced it.)

Acknowledgement rather than deletion: the row stays in the alert history.

The column is cleared again by `alerts.dispatch`, not only set by an operator.
That is what keeps acknowledgement from hiding a live fault, and it has to be
done there because `dedupe_key` identifies an *episode* and deliberately holds
still while a fault continues — an ongoing outage writes no new row, so there is
nothing for a reader to notice. Regenerating an acknowledged row's key withdraws
the acknowledgement instead.

Nullable with no backfill: existing alerts start unacknowledged, which is the
state the badge already assumes.

Renumbered from 0008 to 0009 on 2026-09-05. `0008_fetch_cache` (D53) was already
on `deployment-dev` and claimed the same id off the same parent, so the two
branches produced duplicate revisions rather than a chain: `alembic heads`
reported "0008 (head)" twice and `upgrade head` refused outright with "Multiple
head revisions are present". `ensure_schema` calls exactly that at API and
worker startup, so the merged branch could not boot. This one moves because the
other landed first, and a database already stamped 0008 has the fetch cache
applied, not this column.

Revision ID: 0009
Revises: 0008
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("alerts", "acknowledged_at")
