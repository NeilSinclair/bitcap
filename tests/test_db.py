"""URL handling, and the deploy-time failure it exists to prevent.

The silent failure here is not silent — it is loud, and it only ever happens in
the deployed container. A managed Postgres hands out a connection string with no
driver in it; SQLAlchemy picks psycopg2, which this project does not install;
the process dies at first connect. Nothing local reproduces it, because the
README tells a developer to type `postgresql+psycopg://` by hand.
"""

import pytest

from app.db import database_url, get_engine, normalise_url


class TestBarePostgresUrlsGetADriver:
    @pytest.mark.parametrize("url", [
        "postgres://u:p@host:5432/db",       # what some providers emit
        "postgresql://u:p@host:5432/db",     # what Render emits
    ])
    def test_a_bare_scheme_is_pointed_at_psycopg3(self, url):
        assert normalise_url(url).startswith("postgresql+psycopg://")

    def test_everything_after_the_scheme_survives(self):
        """A mangled password or a dropped query parameter is a worse bug."""
        url = "postgresql://user:p%40ss@host.internal:5432/bitcap?sslmode=require"
        assert normalise_url(url) == (
            "postgresql+psycopg://user:p%40ss@host.internal:5432/bitcap?sslmode=require"
        )

    @pytest.mark.parametrize("url", [
        "postgresql+psycopg://u@h/db",
        "postgresql+psycopg2://u@h/db",   # someone who installed it and meant it
        "postgresql+asyncpg://u@h/db",
        "sqlite:///bitcap.db",
        "sqlite:///:memory:",
    ])
    def test_a_url_that_names_its_driver_is_left_alone(self, url):
        assert normalise_url(url) == url

    def test_the_engine_really_uses_psycopg3(self):
        """The assertion that would have caught the deploy failing.

        `create_engine` resolves the DBAPI eagerly, so this fails with
        ModuleNotFoundError on a regression rather than passing and dying live.
        It does not connect.
        """
        engine = get_engine("postgresql://u:p@host:5432/db")
        assert engine.dialect.driver == "psycopg"

    def test_database_url_normalises_what_it_reads_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
        assert database_url() == "postgresql+psycopg://u:p@host:5432/db"

    def test_the_sqlite_fallback_still_applies(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert database_url() == "sqlite:///bitcap.db"
