"""The `digests` table: what the product said, for one audience, at one time.

The brief asks for a periodic digest readable in the app and for past reports
to be readable too. That second half is what forces a table. A digest computed
on read would be recomputed against whatever the corpus says today, so opening
last week's edition would show this week's opinions under last week's date —
the report would quietly change after publication.

So a digest is persisted, and it is an **ops** table (app/models.py
OPS_TABLES): it is not derivable from committed files and `rebuild` must not
drop it, on the same reasoning as pipeline_runs, alerts and gold_snapshots.

Unique on (kind, window_end, prompt_version): re-running a firing updates the
edition it already published rather than issuing a second, subtly different one
for the same period. `prompt_version` is in the key because two classifier
versions are not comparable — a re-scored corpus is a genuinely new edition,
not a correction of the old one.

Revision ID: 0007
Revises: 0006
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONVariant = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "digests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("stats", JSONVariant, nullable=False),
        sa.Column("payload", JSONVariant, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=True),
        sa.UniqueConstraint("kind", "window_end", "prompt_version",
                            name="uq_digests_kind_window"),
    )


def downgrade() -> None:
    op.drop_table("digests")
