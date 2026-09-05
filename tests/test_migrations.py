"""Migrations and rebuild semantics.

Two silent failures these catch. First, a model change committed without a
matching migration: the tests and a fresh clone would keep passing on
`create_all` while every deployed database drifted a column behind, and nothing
would say so until a query failed in production. Second, a rebuild quietly
erasing operational history — `bitcap-db rebuild` dropped `pipeline_runs`
before this, so every reload discarded the run history the alerting is supposed
to key off, with no error and no empty-looking table to notice.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from app import models as m
from app.db import SchemaDrift, create_all, drop_all, ensure_schema

ROOT = Path(__file__).parent.parent


def _config(engine) -> Config:
    """Alembic config built in-process, pointed at a throwaway database.

    Deliberately does not read alembic.ini: the tests must not depend on the
    ambient DATABASE_URL, or a migration test would run against whatever the
    developer's .env happens to name.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


def _schema(engine) -> dict:
    """Full shape per table, ignoring alembic's own bookkeeping.

    Columns alone are not enough. A `UniqueConstraint` added to a model but
    omitted from its migration compared equal here and passed, while production
    Postgres silently lacked the constraint — and `alerts.dedupe_key`'s
    uniqueness is what the entire alerting design rests on. Foreign keys matter
    for the same reason: `drop_all` relies on the FK direction being what the
    models say.
    """
    insp = inspect(engine)
    return {
        table: {
            "columns": {c["name"]: (str(c["type"]), c["nullable"])
                        for c in insp.get_columns(table)},
            "unique": sorted(
                tuple(sorted(u["column_names"])) for u in insp.get_unique_constraints(table)
            ),
            "foreign_keys": sorted(
                (fk["referred_table"], tuple(sorted(fk["constrained_columns"])))
                for fk in insp.get_foreign_keys(table)
            ),
        }
        for table in insp.get_table_names()
        if table != "alembic_version"
    }


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


class TestMigrationsMatchTheModels:
    def test_upgrade_head_equals_create_all(self, tmp_path):
        """`alembic upgrade head` and `create_all` must produce one schema.

        This is the test that fails when someone adds a column to models.py and
        forgets the migration.
        """
        migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
        command.upgrade(_config(migrated), "head")

        fresh = create_engine("sqlite://")
        create_all(fresh)

        assert _schema(migrated) == _schema(fresh)

    def test_baseline_holds_only_the_pre_pipeline_schema(self, tmp_path):
        """0001 is the old schema; the ops tables arrive in 0002, not before."""
        engine = create_engine(f"sqlite:///{tmp_path / 'baseline.db'}")
        cfg = _config(engine)
        command.upgrade(cfg, "0001")

        tables = set(inspect(engine).get_table_names())
        assert {"run_sources", "source_state", "alerts"}.isdisjoint(tables)
        assert {"articles", "pipeline_runs", "gold_snapshots"} <= tables


class TestRebuildPreservesOperationalHistory:
    def test_drop_all_keeps_ops_tables_and_their_rows(self, session):
        """The derived layer goes; what actually happened stays."""
        engine = session.get_bind()
        session.add(m.PipelineRun(kind="load", status="succeeded"))
        session.flush()
        session.add_all([
            m.SourceState(leg="announcements", source_id="anthropic", consecutive_failures=2),
            m.Alert(kind="system", rule="source_down", severity="warning", subject="s",
                    body="b", dedupe_key="k1"),
        ])
        session.commit()

        drop_all(engine)

        remaining = set(inspect(engine).get_table_names())
        assert remaining == m.OPS_TABLES
        with Session(engine) as after:
            assert after.scalar(select(func.count()).select_from(m.PipelineRun)) == 1
            assert after.scalar(select(func.count()).select_from(m.SourceState)) == 1
            assert after.scalar(select(func.count()).select_from(m.Alert)) == 1

    def test_drop_then_create_restores_a_full_schema(self, session):
        """A rebuild is still a rebuild: the derived tables come back."""
        engine = session.get_bind()
        drop_all(engine)
        create_all(engine)
        assert "articles" in inspect(engine).get_table_names()

    def test_ops_tables_list_matches_the_models(self):
        """OPS_TABLES must name real tables — a typo would silently drop one."""
        known = {t.name for t in m.Base.metadata.sorted_tables}
        assert m.OPS_TABLES <= known


class TestOpsTablesRoundTrip:
    def test_source_state_round_trips(self, session):
        session.add(m.SourceState(
            leg="github", source_id="anthropics", consecutive_failures=3,
            watermark={"max_commit": "2026-09-01"}, last_error="503",
        ))
        session.commit()
        got = session.get(m.SourceState, ("github", "anthropics"))
        assert got.watermark == {"max_commit": "2026-09-01"}
        assert got.consecutive_failures == 3
        assert got.disabled is False

    def test_run_source_is_unique_per_run_and_source(self, session):
        run = m.PipelineRun(kind="scheduled")
        session.add(run)
        session.flush()
        session.add(m.RunSource(run_id=run.id, leg="papers", source_id="mistral",
                                status="failed", error="boom"))
        session.commit()
        session.add(m.RunSource(run_id=run.id, leg="papers", source_id="mistral",
                                status="succeeded"))
        with pytest.raises(Exception):
            session.commit()

    def test_alert_dedupe_key_is_unique(self, session):
        """The constraint is what actually stops a week-long outage alerting nightly."""
        for _ in range(2):
            session.add(m.Alert(kind="system", rule="source_down", severity="warning",
                                subject="mistral is down", body="3 runs",
                                dedupe_key="source_down:papers:mistral:2026-09-03"))
        with pytest.raises(Exception):
            session.commit()


class TestEnsureSchema:
    def test_stamps_a_database_alembic_has_never_seen(self, tmp_path):
        """A create_all-era database must become migratable, not stay stranded."""
        from alembic.runtime.migration import MigrationContext

        engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
        create_all(engine)  # as bitcap-db built it before migrations existed
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() is None

        assert ensure_schema(engine) == "stamped"

        with engine.connect() as conn:
            stamped = MigrationContext.configure(conn).get_current_revision()
        # Compared against the real head rather than a literal, so adding a
        # migration does not require editing this test to keep it meaningful.
        from alembic.script import ScriptDirectory
        assert stamped == ScriptDirectory.from_config(_config(engine)).get_current_head()

    def test_an_unstamped_database_behind_head_refuses_to_claim_head(self, tmp_path):
        """The bug 0008 exposed, and the reason the stamp is now verified.

        `create_all` creates missing *tables*; it cannot add a missing *column*
        to a table that already exists. So a database that already had tables
        but no stamp took the first branch, no-opped, and then asserted `head` —
        and that assertion is unrecoverable, because no later `upgrade` will run
        a revision the stamp says is already applied. Observed for real: the
        stamp read 0008 while `alerts.acknowledged_at` did not exist, and
        nothing failed until a query touched the column.

        Silent is the whole problem. A drift that raises is a five-minute fix;
        a drift that stamps is a database that lies about itself forever.
        """
        from alembic.runtime.migration import MigrationContext

        # Deliberately far behind head, not one revision behind. At 0007 there
        # is nothing for `create_all` to create, so a check placed *after* it
        # still looked fine -- the first version of this test passed against an
        # `ensure_schema` whose printed recovery did not work. From 0003 later
        # migrations own real tables, which is what exposes the ordering.
        engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
        command.upgrade(_config(engine), "0003")
        with engine.begin() as conn:               # a create_all-era database
            conn.exec_driver_sql("DELETE FROM alembic_version")
        before = set(inspect(engine).get_table_names())

        with pytest.raises(SchemaDrift, match="alerts: acknowledged_at"):
            ensure_schema(engine)

        # Nothing was created on the way to the raise. `create_all` has no
        # migration-level checkfirst, so a table it pre-creates here is one
        # `op.create_table` dies on during the recovery below -- D29 again,
        # inside the guard meant to prevent it.
        assert set(inspect(engine).get_table_names()) == before

        # Left unstamped, which is what makes it recoverable: the operator can
        # stamp the revision it actually matches and upgrade. Stamped `head` it
        # could never be migrated again.
        with engine.connect() as conn:
            assert MigrationContext.configure(conn).get_current_revision() is None

    def test_the_printed_recovery_actually_works(self, tmp_path):
        """The error is only worth raising if its instruction succeeds.

        Asserted by *executing* the two commands the message names, against the
        database that produced it. Verified to fail before the check was moved
        ahead of `create_all`: `OperationalError: table raw_github_repos already
        exists`.
        """
        engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
        command.upgrade(_config(engine), "0003")
        with engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM alembic_version")
        with pytest.raises(SchemaDrift):
            ensure_schema(engine)

        command.stamp(_config(engine), "0003")     # what the message says to do
        command.upgrade(_config(engine), "head")

        assert ensure_schema(engine) == "upgraded"
        fresh = create_engine("sqlite://")
        create_all(fresh)
        assert _schema(engine) == _schema(fresh)

    def test_the_error_names_the_recovery(self, tmp_path):
        """The person who hits this did not write the migration."""
        engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
        command.upgrade(_config(engine), "0007")
        with engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM alembic_version")

        with pytest.raises(SchemaDrift, match="alembic stamp"):
            ensure_schema(engine)

    def test_a_wrongly_stamped_database_is_caught_too(self, tmp_path):
        """The lie can also arrive pre-existing — mine did.

        The stamped branch runs `upgrade`, which is a correct no-op against a
        stamp of head, so it cannot repair a database that was stamped wrongly
        before this function ever saw it. The check has to cover both paths or
        it only catches the drift it creates itself.
        """
        engine = create_engine(f"sqlite:///{tmp_path / 'wrong.db'}")
        command.upgrade(_config(engine), "0007")
        command.stamp(_config(engine), "head")     # asserts a column it lacks

        with pytest.raises(SchemaDrift, match="acknowledged_at"):
            ensure_schema(engine)

    def test_a_consistent_database_passes_the_check(self, tmp_path):
        """The guard must not fire on the paths that are actually fine, or it
        gets deleted the first time it blocks a deploy."""
        engine = create_engine(f"sqlite:///{tmp_path / 'fine.db'}")
        assert ensure_schema(engine) == "stamped"    # empty database
        assert ensure_schema(engine) == "upgraded"   # already at head
        drop_all(engine)
        assert ensure_schema(engine) == "upgraded"   # the rebuild path

    def test_a_column_the_models_do_not_declare_is_not_drift(self, tmp_path):
        """One-directional on purpose. An extra column breaks no query we issue,
        and failing on it would make every rollback a hard outage."""
        engine = create_engine(f"sqlite:///{tmp_path / 'extra.db'}")
        ensure_schema(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE alerts ADD COLUMN scratch TEXT")

        assert ensure_schema(engine) == "upgraded"

    def test_restores_tables_a_rebuild_dropped(self, tmp_path):
        """drop_all + ensure_schema is the rebuild path; it must put them back."""
        engine = create_engine(f"sqlite:///{tmp_path / 'rebuild.db'}")
        ensure_schema(engine)
        with Session(engine) as s:
            s.add(m.PipelineRun(kind="load", status="succeeded"))
            s.commit()

        drop_all(engine)
        assert "articles" not in inspect(engine).get_table_names()

        assert ensure_schema(engine) == "upgraded"
        assert "articles" in inspect(engine).get_table_names()
        with Session(engine) as s:
            assert s.scalar(select(func.count()).select_from(m.PipelineRun)) == 1

    def test_a_database_behind_head_upgrades_instead_of_colliding(self, tmp_path):
        """The case the docstring claims to handle, and used to die on.

        `create_all` ran unconditionally before `upgrade`, so it pre-created the
        tables the pending migration was about to add and `op.create_table`
        (which has no checkfirst) raised "table already exists" — on every
        firing, until someone stamped the database by hand. Reproduced by review
        against a throwaway sqlite file.
        """
        engine = create_engine(f"sqlite:///{tmp_path / 'behind.db'}")
        command.upgrade(_config(engine), "0002")  # deliberately one behind head

        assert "people" not in inspect(engine).get_table_names()
        assert ensure_schema(engine) == "upgraded"

        assert "people" in inspect(engine).get_table_names()
        fresh = create_engine("sqlite://")
        create_all(fresh)
        assert _schema(engine) == _schema(fresh)
        with engine.connect() as conn:
            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory
            head = ScriptDirectory.from_config(_config(engine)).get_current_head()
            assert MigrationContext.configure(conn).get_current_revision() == head
