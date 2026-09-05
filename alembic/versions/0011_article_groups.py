"""`article_groups`: which articles are the same event as which.

The feed carried one event as several rows. GPT-6 Astra took seven lines over
five days; a single repo ships four releases in five days and each took its
own. This table is where the collapse is recorded.

Silver, so `transform` rebuilds it — it is a pure function of the articles,
their classifications and the thresholds in `config/dedupe.yaml`. The expensive
input, the embeddings, is cached separately in `raw_article_embeddings` and
survives a rebuild.

Every article gets a row even when it groups with nothing, so a singleton is a
group of one. That makes "considered and left alone" a readable state rather
than one indistinguishable from "never looked at", and keeps the read path a
plain join.

`reason` is not nullable on purpose. A merge is a decision the product acts on,
and this schema already requires a citation everywhere else a decision is
recorded — a mechanism tag carries the verbatim quote it was drawn from. A
group carries why it is a group.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "article_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("is_anchor", sa.Boolean(), nullable=False),
        sa.Column("group_size", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline_runs.id"]),
        sa.UniqueConstraint("article_id", name="uq_article_groups_article_id"),
    )
    op.create_index("ix_article_groups_group_id", "article_groups", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_article_groups_group_id", table_name="article_groups")
    op.drop_table("article_groups")
