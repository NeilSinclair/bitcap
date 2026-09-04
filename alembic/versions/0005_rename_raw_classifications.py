"""Rename raw_classifications to raw_llm_responses.

Naming only; no column, constraint or data change. The old name described the
row's *meaning* ("a classification"), which reads like processed output and put
the table on the wrong side of the bronze/silver line for anyone reading the
schema cold. What the row actually holds is the provider's verbatim response to
one prompt — an acquisition from an external system, exactly like
`raw_articles` holds a website's verbatim response (docs/decisions.md D37).

`op.rename_table` preserves the rows, so this is safe on a populated database
and needs no reload.

Revision ID: 0005
Revises: 0004
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("raw_classifications", "raw_llm_responses")


def downgrade() -> None:
    op.rename_table("raw_llm_responses", "raw_classifications")
