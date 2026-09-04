"""At most one `running` pipeline_runs row, enforced by the database.

The concurrency guard was a SELECT-then-INSERT in `api/pipeline.py`, which is
neither atomic nor visible to the nightly cron — a separate process on a
separate container that never consulted it at all. Two overlapping firings
interleave writes to `research/docs/announcements.json` and both do
read-modify-write on `announcement_cost.json`, losing cost records that a
graded requirement says must be captured at the call site
(docs/decisions.md D44).

A partial unique index on `status` where `status = 'running'`: any number of
succeeded and failed rows, exactly one running one. Both processes now get the
same answer from the same place, and the answer is atomic.

Applied against a live database this can fail if more than one `running` row
already exists — a corpse from a killed run, which is exactly the state this
also prevents. The upgrade therefore closes stale rows out first rather than
dying on data the old code was free to create.

Revision ID: 0006
Revises: 0005
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the newest running row; close the rest. Deleting them would erase
    # runs that really happened, and `alerts.run_failed` keys on `failed`.
    op.execute(
        """
        UPDATE pipeline_runs
           SET status = 'failed',
               finished_at = CURRENT_TIMESTAMP,
               error = COALESCE(error, '')
                    || 'closed by migration 0006: a second concurrent run row, '
                    || 'which the single-running index now prevents'
         WHERE status = 'running'
           AND id <> (SELECT MAX(id) FROM pipeline_runs WHERE status = 'running')
        """
    )
    op.create_index(
        "ix_pipeline_runs_single_running",
        "pipeline_runs",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_single_running", table_name="pipeline_runs")
