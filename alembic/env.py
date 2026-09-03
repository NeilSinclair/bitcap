"""Alembic environment.

The URL comes from ``DATABASE_URL`` (loaded from .env the same way every other
entry point loads it), never from alembic.ini — the .ini is committed and the
connection string is not.

``render_as_batch`` is on because the test suite and the no-Docker fallback both
run on sqlite, which cannot ALTER a column in place; batch mode rewrites the
table instead. It is a no-op on Postgres.
"""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.db import database_url, load_env
from app.models import Base

config = context.config

# Logging config is a convenience for the CLI, not a precondition for migrating.
# A Config built in-process (the tests) has no ini file at all, and refusing to
# run without one would make migrations untestable.
if config.config_file_name and os.path.exists(config.config_file_name):
    fileConfig(config.config_file_name)

# A URL set explicitly on the Config wins — that is how the tests point a
# migration at a throwaway database. Only fall back to DATABASE_URL when
# nothing was supplied, or `alembic upgrade head` in a test would silently
# migrate whatever .env happens to point at.
if not config.get_main_option("sqlalchemy.url", None):
    load_env()
    config.set_main_option("sqlalchemy.url", database_url().replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL for the migrations without connecting to a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
