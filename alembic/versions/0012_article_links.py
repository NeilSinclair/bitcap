"""`article_links`: which articles are *about* the same thing without being it.

The deliberate counterpart to `article_groups` (0011). A group says "these are
one event, show one of them". A link says "these are different documents about
the same thing, show both".

GitHub releases sit outside every similarity gate — `dedupe._apply_gated` keeps
only rows with no repo — so `openai/codex rust-v0.153.3` ("Added GPT-6-Astra to
the Amazon Bedrock model picker") and "GPT-6 Astra: A new generation of
intelligence" took two slots in one AI digest with nothing joining them.

Feeding releases into the union-find instead was measured and rejected: one
codex release reaches six announcements, and union-find is transitive, so the
launch post, the safety overview and two customer stories collapse into a single
group. `config/dedupe.yaml` states the cost — a false merge deletes a claim from
the product — and a link cannot make that mistake, because it folds nothing.

Silver, so `transform` rebuilds it: a pure function of the articles, their
classifications and `config/dedupe.yaml`, with no LLM call anywhere in it.

`evidence` is not nullable, on the same reasoning `article_groups.reason` is
not: a claim the product puts in front of a reader has to say why it is there.
Here it is the shared model identifier, checkable against both documents.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "article_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("from_article_id", sa.Integer(), nullable=False),
        sa.Column("to_article_id", sa.Integer(), nullable=False),
        sa.Column("relation", sa.String(), nullable=False),
        sa.Column("evidence", sa.String(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["from_article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["to_article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["pipeline_runs.id"]),
        sa.UniqueConstraint(
            "from_article_id", "to_article_id", "relation", "evidence",
            name="uq_article_links_pair",
        ),
    )
    # Names match what `create_all` derives from the model's `index=True`, so a
    # migrated Postgres and a `create_all` sqlite test database describe the
    # same schema. `_schema()` compares tables and columns but never indexes, so
    # a divergence here would not be caught by anything.
    op.create_index(
        "ix_article_links_from_article_id", "article_links", ["from_article_id"])
    op.create_index(
        "ix_article_links_to_article_id", "article_links", ["to_article_id"])


def downgrade() -> None:
    op.drop_index("ix_article_links_to_article_id", table_name="article_links")
    op.drop_index("ix_article_links_from_article_id", table_name="article_links")
    op.drop_table("article_links")
