"""A digest is identified by its whole span, not just where the span ends.

`uq_digests_kind_window` was `(kind, window_end, prompt_version)` — width-blind.
That held while there was one window width in the system. D79 gave the product
two, and changed the published grid from 48 hours to 24, at which point the
omission became destructive rather than theoretical.

A 48-hour edition covering 04→06 Sep and a 24-hour edition covering 05→06 Sep
are different reports over different periods. Under the old key they were the
same row. `publish` looked one up by `window_end`, found the older edition,
and overwrote its payload — while leaving `window_start` untouched, because the
start is only assigned when a row is *created*. The result was a published
record that claimed 48 hours and contained 24: measured on the live database,
editions 97 and 98 went from 1 item each to 0, silently, with nothing failing.

It is not an edge case at this grid width. Every 48-hour edition ends on a
midnight that is also a 24-hour boundary, so a daily cron walking forward
collides with the archive it is supposed to sit beside — once per old edition,
for as long as the archive holds one.

Adding `window_start` to the key makes the two distinguishable, which is what
they always were. It also removes the whole class: any future change of window
width now creates editions alongside the old ones instead of through them.

No data migration. Existing rows already carry distinct `window_start` values,
so every one of them satisfies the wider constraint the moment it exists.
"""

from __future__ import annotations

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels = None
depends_on = None


# Batch mode, not a bare ALTER. The deployment is Postgres, where either works,
# but the tests and the documented `clone-to-running` path build the schema on
# SQLite -- which has no ALTER for constraints at all and needs alembic's
# copy-and-move strategy. A plain `op.drop_constraint` here raises
# NotImplementedError on SQLite, which `tests/test_migrations.py` catches.
def upgrade() -> None:
    with op.batch_alter_table("digests") as batch:
        batch.drop_constraint("uq_digests_kind_window", type_="unique")
        batch.create_unique_constraint(
            "uq_digests_kind_window",
            ["kind", "window_start", "window_end", "prompt_version"],
        )


def downgrade() -> None:
    """Narrowing back can fail, and that is correct.

    Two editions sharing an end over different spans are legal under the wider
    key and violate the narrower one. A downgrade against a database that has
    published both must fail loudly rather than pick one to discard.
    """
    with op.batch_alter_table("digests") as batch:
        batch.drop_constraint("uq_digests_kind_window", type_="unique")
        batch.create_unique_constraint(
            "uq_digests_kind_window", ["kind", "window_end", "prompt_version"],
        )
