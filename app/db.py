"""Engine and session plumbing.

The database is fully derived from committed files (research/docs JSON artifacts
and config/ YAML), so there are no migrations: ``rebuild`` drops and recreates
the schema and reloads. Postgres is the deployment target; sqlite is the
no-setup fallback so a fresh clone works without Docker.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_URL = "sqlite:///bitcap.db"


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


def drop_all(engine: Engine) -> None:
    """Drop every table defined in :mod:`app.models`."""
    from app.models import Base

    Base.metadata.drop_all(engine)
