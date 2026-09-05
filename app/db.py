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


# Bare Postgres schemes, and what SQLAlchemy does with them. `postgresql://`
# resolves to psycopg2, which is not a dependency of this project and never has
# been — `pyproject.toml` pins psycopg 3. `postgres://` SQLAlchemy 2.0 refuses
# outright. Managed providers hand out one or the other.
_BARE_POSTGRES = ("postgres://", "postgresql://")
_DRIVER = "postgresql+psycopg://"


def normalise_url(url: str) -> str:
    """Point a bare Postgres URL at the driver that is actually installed.

    Render's `fromDatabase` (and every other managed Postgres) supplies a
    connection string with no driver in it. SQLAlchemy then picks its default,
    psycopg2, and the container dies at first connect with
    `ModuleNotFoundError: No module named 'psycopg2'` — a deploy-time failure
    that nothing local reproduces, because a developer following the README
    types `postgresql+psycopg://` by hand and never sees it.

    A URL that names its own driver is left alone: `postgresql+psycopg2://`
    from someone who installed psycopg2 deliberately still means that, and so
    does `+asyncpg`. Only the ambiguous case is resolved.

    Args:
        url: Any SQLAlchemy URL. sqlite and everything else pass through.

    Returns:
        The URL, with a bare Postgres scheme replaced by an explicit psycopg 3 one.
    """
    for scheme in _BARE_POSTGRES:
        if url.startswith(scheme):
            return _DRIVER + url[len(scheme):]
    return url


def database_url() -> str:
    """Resolve the database URL.

    Returns:
        ``DATABASE_URL`` from the environment (loaded from .env by the caller),
        normalised onto psycopg 3, falling back to a local sqlite file with a
        printed note — silence here would look like Postgres and not be.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return normalise_url(url)
    print(f"DATABASE_URL not set; using {DEFAULT_URL}", file=sys.stderr)
    return DEFAULT_URL


def get_engine(url: str | None = None) -> Engine:
    """Create an engine for the given or resolved URL.

    Args:
        url: Explicit database URL; defaults to :func:`database_url`.

    Returns:
        SQLAlchemy engine.
    """
    return create_engine(normalise_url(url) if url else database_url())


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


class SchemaDrift(RuntimeError):
    """The database does not have the columns the models declare.

    Raised rather than warned. Every alternative is worse: carrying on means the
    first query touching the missing column fails at some arbitrary later point,
    in whichever of the API, the worker or the CLI happens to reach it first,
    with a DBAPI error that names a column and not a cause.
    """


def _missing_columns(engine: Engine) -> dict[str, list[str]]:
    """Columns the models declare that the live database does not have.

    Presence only — never types or nullability. A column that is absent is
    unambiguous in every dialect; a type that renders differently under sqlite
    and Postgres is not, and a drift check that cries wolf gets deleted.
    Columns the database has and the models do not are ignored: that is a
    downgrade or a hand-added column, neither of which breaks a query we issue.

    Args:
        engine: Engine to inspect.

    Returns:
        table -> sorted missing column names, for tables that have any. Empty
        when the database is consistent with the models.
    """
    from sqlalchemy import inspect

    from app.models import Base

    insp = inspect(engine)
    live = set(insp.get_table_names())
    drift: dict[str, list[str]] = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in live:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        gap = sorted(c.name for c in table.columns if c.name not in have)
        if gap:
            drift[table.name] = gap
    return drift


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

    **Every path runs a drift check, ahead of `create_all`, and the ordering is
    load-bearing rather than incidental.**
    `create_all` creates missing *tables*; it cannot add a missing *column* to a
    table that already exists. So on a database that already had tables but no
    stamp, the first branch used to no-op and then assert `head` — declaring a
    structurally-behind database current, permanently, because no later
    `upgrade` will ever run a revision the stamp says is already applied. The
    column-only migration 0008 landed exactly this way: the stamp read `0008`
    and `alerts.acknowledged_at` did not exist. Nothing failed until a query
    touched the column, far from the cause.

    The check covers the stamped path too, where the same lie can arrive
    pre-existing, and it is what makes the stamp a claim this function verifies
    rather than one it merely makes.

    Args:
        engine: Engine to bring up to date.

    Returns:
        What was done — ``stamped`` for a database Alembic had never seen,
        ``upgraded`` otherwise.

    Raises:
        SchemaDrift: The database is missing columns the models declare, and no
            migration can be run to add them because the version it is stamped
            at already claims to include them.
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
        # Never migrated. The check runs before `create_all`, not just before
        # the stamp, and the ordering is the whole fix. `create_all` has no
        # checkfirst at the migration level: on a database built at an older
        # revision it happily creates the tables that *later* migrations own,
        # and the recovery this function prints — stamp the revision it really
        # matches, then upgrade — then dies on "table already exists", which is
        # D29 all over again. Checking first leaves the database untouched, so
        # the printed recovery actually runs.
        _raise_on_drift(engine)
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
    # Checked after the upgrade (which is what fixes a schema) but before
    # `create_all`, for the same reason as above: a *wrongly* stamped database
    # reaches here, `upgrade` correctly no-ops against it, and letting
    # `create_all` run first would pre-create the tables its real recovery still
    # has to migrate. `create_all` only ever adds whole missing tables, at model
    # shape, so nothing it does could turn a passing check into a failing one.
    _raise_on_drift(engine)
    create_all(engine)
    return "upgraded"


def _raise_on_drift(engine: Engine) -> None:
    """Fail with a recovery instruction, or return.

    The message carries the fix because the person who hits this is not the
    person who wrote the migration, and "column does not exist" three layers
    down a stack trace tells them nothing about `alembic stamp`.

    The revision is re-read here rather than passed in from the top of
    `ensure_schema`. On the stamped path an `upgrade` has run in between, so the
    value captured earlier is stale, and the message would name a revision the
    database has already moved off while asserting `upgrade head` cannot help.
    """
    drift = _missing_columns(engine)
    if not drift:
        return
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        stamped = MigrationContext.configure(conn).get_current_revision()
    detail = "; ".join(f"{t}: {', '.join(cols)}" for t, cols in sorted(drift.items()))
    raise SchemaDrift(
        f"database is missing columns the models declare ({detail}). "
        f"It is stamped {stamped or 'nothing'}, so `alembic upgrade head` will "
        "not add them. Stamp the revision the database actually matches, then "
        "upgrade: `alembic stamp <revision>` && `alembic upgrade head`."
    )


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
