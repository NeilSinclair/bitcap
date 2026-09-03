"""Engine and session plumbing.

Most of the database is derived from committed files (research/docs JSON
artifacts and config/ YAML), and ``rebuild`` drops and reloads that part. The
operational tables are the exception: run history, per-source failure counts,
raised alerts and drift snapshots record things that happened, and no file can
reproduce them. :func:`drop_all` therefore leaves ``models.OPS_TABLES`` alone,
and schema changes go through Alembic (``alembic upgrade head``) rather than
``create_all``, which cannot alter a table that already holds data.

Postgres is the deployment target; sqlite is the no-setup fallback so a fresh
clone works without Docker.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_URL = "sqlite:///bitcap.db"


def load_env() -> None:
    """Read .env into the environment without overriding what is already set.

    Deliberately does not reuse ``research/announcements/providers.load_env``:
    that module is not shipped in the wheel and pulls in the LLM SDKs, and
    neither ``bitcap-db status`` nor an Alembic migration should need an API
    client to reach a database.
    """
    from pathlib import Path

    path = Path(__file__).parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@event.listens_for(Engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """Enforce foreign keys on sqlite, which ignores them by default.

    Without this, the sqlite-backed tests silently pass FK violations that
    Postgres rejects — exactly the dialect gap that hid a real ordering bug.
    """
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


def database_url() -> str:
    """Resolve the database URL.

    Returns:
        ``DATABASE_URL`` from the environment (loaded from .env by the caller),
        falling back to a local sqlite file with a printed note — silence here
        would look like Postgres and not be.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    print(f"DATABASE_URL not set; using {DEFAULT_URL}", file=sys.stderr)
    return DEFAULT_URL


def get_engine(url: str | None = None) -> Engine:
    """Create an engine for the given or resolved URL.

    Args:
        url: Explicit database URL; defaults to :func:`database_url`.

    Returns:
        SQLAlchemy engine.
    """
    return create_engine(url or database_url())


def get_session(engine: Engine) -> Session:
    """Open a session bound to the engine.

    Args:
        engine: Engine from :func:`get_engine`.

    Returns:
        A new session; caller owns commit/close.
    """
    return sessionmaker(bind=engine)()


def create_all(engine: Engine) -> None:
    """Create every table defined in :mod:`app.models`."""
    from app.models import Base

    Base.metadata.create_all(engine)


def ensure_schema(engine: Engine) -> str:
    """Bring the database up to the current schema, however it got here.

    Three cases, and the reason this exists rather than a bare ``create_all``:

    * **Never migrated** (a fresh clone, or a database `create_all` built before
      migrations existed) — create anything missing, then stamp `head`. Without
      the stamp the database is permanently unmigratable: Alembic would try to
      create tables that are already there.
    * **Stamped and behind** — upgrade.
    * **Stamped and current** — no-op.

    The `create_all` in the first case is what keeps `bitcap-db rebuild` a
    one-command path after :func:`drop_all` has removed the derived layer:
    Alembic sees a current stamp and would correctly do nothing, so something
    still has to put the dropped tables back.

    Args:
        engine: Engine to bring up to date.

    Returns:
        What was done — ``stamped`` for a database Alembic had never seen,
        ``upgraded`` otherwise.
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))

    with engine.connect() as conn:
        stamped = MigrationContext.configure(conn).get_current_revision()

    if stamped is None:
        # Never migrated: build everything and record where we are.
        create_all(engine)
        command.stamp(cfg, "head")
        return "stamped"

    # Already stamped. `create_all` MUST NOT run here. `op.create_table` has no
    # checkfirst, so pre-creating the tables a pending migration is about to add
    # makes that migration die on "table already exists" — every firing, until a
    # human intervenes. This is the case the function exists to handle.
    #
    # A rebuild is the one caller that needs tables put back, and it is at head
    # by definition, so `upgrade` is a no-op for it: create_all after the
    # upgrade restores the derived layer without ever racing a migration.
    command.upgrade(cfg, "head")
    create_all(engine)
    return "upgraded"


def drop_all(engine: Engine) -> None:
    """Drop the derived schema, preserving the operational tables.

    Everything dropped here is a pure function of committed files, so losing it
    costs a reload. The tables named in :data:`app.models.OPS_TABLES` are not:
    they record runs that happened, sources that failed, and alerts that were
    raised. A rebuild is a data-loading operation and must not erase them.

    Dropping only children is safe in this direction — the surviving ops tables
    are the FK *parents* (``raw_*.load_run_id`` points at ``pipeline_runs``,
    never the reverse), so no constraint is left dangling.

    Args:
        engine: Engine to drop against.
    """
    from app.models import OPS_TABLES, Base

    doomed = [t for t in Base.metadata.sorted_tables if t.name not in OPS_TABLES]
    Base.metadata.drop_all(engine, tables=doomed)
