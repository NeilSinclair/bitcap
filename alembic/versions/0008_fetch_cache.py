"""The `fetch_cache` table: HTTP response bodies, so a re-run does not re-fetch.

The papers harvesters cached to disk under `research/docs/*_cache/`. Those
directories are in both .gitignore and .dockerignore, and the deployment's cron
has no disk, so on Render the cache never existed: every nightly firing started
cold and replayed roughly seventy requests at arXiv. On 2026-09-04 arXiv
rate-limited the egress IP and four of the six papers sources failed at once
(docs/decisions.md D53).

Postgres is the only store that survives a firing there, so the cache moves
here. It is an **ops** table (app/models.py OPS_TABLES): `rebuild` must not drop
it, or the next run goes back to arXiv for everything it already has.

`expires_at` carries the load-bearing distinction. NULL means immutable — a
versioned arXiv id is the same document forever — and is never re-fetched. A
timestamp means the URL is a discovery query whose answer changes when a new
paper appears; caching those permanently would freeze the register and report
success while doing it.

Revision ID: 0008
Revises: 0007
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fetch_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        # NULL = immutable, never re-fetched.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("url", name="uq_fetch_cache_url"),
    )


def downgrade() -> None:
    op.drop_table("fetch_cache")
