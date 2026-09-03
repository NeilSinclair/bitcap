"""GitHub gains the bronze layer it never had.

`raw_github_people` stores the *aggregate* — one row per person, already reduced
to commit counts and email domains. The commits behind it lived only in the
on-disk `github_cache/`, so the people register could not be rebuilt from the
database, and a container without a disk re-walked twelve months of history
every firing to recompute a summary it already had.

`raw_github_repos` is the missing layer. One row per (org, repo) holding the
history verbatim, with `pushed_at` as the incremental key: a repository whose
last push has not moved cannot have new commits and is never re-walked.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.sqltypes import Text  # noqa: F401  (used by JSONB variants)

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('raw_github_repos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org', sa.String(), nullable=False),
    sa.Column('repo', sa.String(), nullable=False),
    sa.Column('pushed_at', sa.String(), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('content_hash', sa.String(), nullable=False),
    sa.Column('first_loaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('load_run_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['load_run_id'], ['pipeline_runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org', 'repo')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('raw_github_repos')
