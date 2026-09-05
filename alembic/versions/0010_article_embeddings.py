"""`raw_article_embeddings`: the embedding cache the duplicate collapse runs on.

Near-duplicate detection needs a similarity signal that identifier matching
cannot supply — `config/entities.yaml` records its own gap, that it "does NOT
catch a genuinely novel product name carrying no version number". Embeddings
fill it, and Anthropic has no embeddings API, so this is the one place the
pipeline calls OpenAI in production.

Cached because the call costs money and the input rarely changes.
`content_hash` is over the embedded text (title + classifier summary), not the
article payload, so a re-classification that rewrites the summary re-embeds and
an ordinary re-run does not. A second run of an unchanged corpus spends nothing.

The table is added to `models.OPS_TABLES` in the same change, on the reasoning
already recorded there for `fetch_cache`: a rebuild that dropped it would send
the next run back to OpenAI for every article it had already embedded.

The vector is base64 of little-endian float32, not a JSON array — ~6 KB per row
against ~30 KB, and `numpy.frombuffer` rather than a parse on the read side.
`dim` is stored so swapping the embedding model for one of a different width is
caught rather than silently building a ragged matrix.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_article_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("vector", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("url", name="uq_raw_article_embeddings_url"),
    )


def downgrade() -> None:
    op.drop_table("raw_article_embeddings")
