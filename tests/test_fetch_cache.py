"""Tests for the shared papers fetch layer.

Written against the 2026-09-04 incident (docs/decisions.md D53), where four of
six papers sources failed in one cron firing on arXiv 429s. The silent failures
this suite exists to catch are the ones that put us there, plus the worse one
the fix could introduce:

  * a fetch layer with no retry, so one 429 kills a source outright;
  * a backoff shorter than the polite interval, so a 429 is retried faster than
    the rate we already decided was courteous;
  * `Retry-After` ignored when the server says exactly how long to wait;
  * per-harvester throttles that look polite alone and are not in aggregate;
  * **and a discovery URL cached permanently**, which would freeze the register
    on whatever it knew the first night and go on reporting success. That last
    one is the reason `_expiry` exists and is the failure nothing else would
    surface.
"""

import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "papers"))

import fetch_cache as fc

# Captured before the autouse fixture stubs them out, so the tests that do
# exercise the database can put the real ones back.
_REAL_DB_GET, _REAL_DB_PUT = fc.db_get, fc.db_put


@pytest.fixture(autouse=True)
def _no_db_no_sleep(monkeypatch):
    """Every test runs without a database and without real waiting.

    The database path is covered separately in `TestDatabaseRoundTrip`, which
    builds its own sqlite engine.
    """
    monkeypatch.setattr(fc, "db_get", lambda url: None)
    monkeypatch.setattr(fc, "db_put", lambda url, body, expires: False)
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)
    fc._LAST_REQUEST.clear()


class _Response:
    def __init__(self, body=b"body"):
        self._body = body

    def read(self):
        return self._body


def _http_error(url, code, headers=None):
    return urllib.error.HTTPError(url, code, "err", headers or {}, None)


class TestExpiry:
    """Which URLs may be cached forever, and which must not be."""

    def test_versioned_arxiv_paper_is_permanent(self):
        # v2 of a paper is never edited into a different v2, and these are the
        # ~1 MB fetches that made up most of the request volume.
        assert fc._expiry("https://arxiv.org/html/2501.12948v2", fc.AUTO) is None
        assert fc._expiry("https://arxiv.org/abs/2501.12948v1", fc.AUTO) is None

    def test_unversioned_arxiv_id_is_not_permanent(self):
        # An unversioned id resolves to the *latest* version, so it is mutable.
        # This test asserted the opposite until an independent review caught it
        # (D53b). It is reachable, not theoretical: deepmind_harvest.py:66
        # strips the version and line 189 builds `arxiv.org/html/<bare id>`, so
        # caching it forever would pin a paper's byline to whatever v1 said —
        # permanently and silently, on the harvester whose whole output is
        # bylines.
        assert fc._expiry("https://arxiv.org/html/2501.12948", fc.AUTO) is not None
        assert fc._expiry("https://arxiv.org/abs/2501.12948", fc.AUTO) is not None

    def test_discovery_query_expires(self):
        # THE important one. `au:"DeepSeek-AI"` returns a different answer the
        # day DeepSeek posts a paper. Cache it forever and the register stops
        # finding papers while every run still reports success.
        url = 'https://export.arxiv.org/api/query?search_query=au%3A"DeepSeek-AI"&max_results=60'
        expires = fc._expiry(url, fc.AUTO)
        assert expires is not None
        assert expires > datetime.now(timezone.utc)

    def test_listing_page_expires(self):
        url = "https://ai.meta.com/results/?content_types[0]=publication&years[0]=2026&page=2"
        assert fc._expiry(url, fc.AUTO) is not None

    def test_sitemap_expires(self):
        assert fc._expiry("https://deepmind.google/sitemap.xml", fc.AUTO) is not None

    def test_explicit_none_overrides_to_permanent(self):
        assert fc._expiry("https://deepmind.google/sitemap.xml", None) is None

    def test_explicit_hours_honoured(self):
        expires = fc._expiry("https://arxiv.org/html/2501.12948v2", 1)
        assert expires is not None
        assert expires < datetime.now(timezone.utc) + timedelta(hours=2)


class TestThrottleFamily:
    def test_export_and_bare_arxiv_share_one_bucket(self):
        # Five harvesters each holding a private opinion about arXiv's limit is
        # how they were polite alone and impolite together.
        assert fc._family("https://export.arxiv.org/api/query?x=1") == "arxiv.org"
        assert fc._family("https://arxiv.org/html/2501.12948v2") == "arxiv.org"
        assert fc._interval("arxiv.org") == 3.0

    def test_other_hosts_get_the_default_interval(self):
        assert fc._interval(fc._family("https://ai.meta.com/results/")) == 1.0

    def test_waits_between_two_calls_to_one_family(self, monkeypatch):
        slept = []
        monkeypatch.setattr(fc.time, "sleep", slept.append)
        fc._wait_turn("arxiv.org", 3.0)
        fc._wait_turn("arxiv.org", 3.0)
        assert slept and slept[0] > 0

    def test_first_call_does_not_wait(self, monkeypatch):
        # The old code slept *after* a successful fetch, so the very first
        # request of a process fired with no spacing -- exactly the request
        # that fails when the previous firing left the address rate-limited.
        slept = []
        monkeypatch.setattr(fc.time, "sleep", slept.append)
        fc._wait_turn("arxiv.org", 3.0)
        assert slept == []


class TestRetryAfter:
    def test_reads_integer_seconds(self):
        assert fc._retry_after(_http_error("u", 429, {"Retry-After": "42"})) == 42.0

    def test_reads_http_date(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=30)
        stamp = when.strftime("%a, %d %b %Y %H:%M:%S GMT")
        assert 0 < fc._retry_after(_http_error("u", 429, {"Retry-After": stamp})) <= 31

    def test_absent_header_is_none(self):
        assert fc._retry_after(_http_error("u", 429, {})) is None


class TestFetchRetryPolicy:
    def test_retries_then_succeeds(self, monkeypatch, tmp_path):
        calls = {"n": 0}

        def urlopen(req, timeout=90):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _http_error(req.full_url, 429)
            return _Response(b"ok")

        monkeypatch.setattr(fc.urllib.request, "urlopen", urlopen)
        assert fc.fetch("https://arxiv.org/html/2501.11111", cache_dir=tmp_path) == "ok"
        assert calls["n"] == 3

    def test_a_single_429_no_longer_kills_a_source(self, monkeypatch, tmp_path):
        # deepseek_harvest.fetch had no retry at all, which is why DeepSeek and
        # OpenAI both died on their first request while meta and mistral got
        # three attempts each.
        import deepseek_harvest as dsh

        monkeypatch.setattr(dsh, "CACHE", tmp_path)
        calls = {"n": 0}

        def urlopen(req, timeout=90):
            calls["n"] += 1
            if calls["n"] < 2:
                raise _http_error(req.full_url, 429)
            return _Response(b"recovered")

        monkeypatch.setattr(fc.urllib.request, "urlopen", urlopen)
        assert dsh.fetch("https://arxiv.org/html/2501.22222") == "recovered"

    def test_backoff_is_never_shorter_than_the_polite_interval(self, monkeypatch, tmp_path):
        # The old arxiv_resolve.fetch backed off 1s then 2s against its own
        # 3.0s pause: it retried a 429 faster than the rate it had already
        # committed to, turning one 429 into three.
        slept = []
        monkeypatch.setattr(fc.time, "sleep", slept.append)
        # Silence the throttle so only the *backoff* waits are recorded; its
        # own spacing sleep is a shade under the interval and would otherwise
        # be indistinguishable from a too-short backoff.
        monkeypatch.setattr(fc, "_wait_turn", lambda family, interval: None)
        monkeypatch.setattr(
            fc.urllib.request, "urlopen",
            lambda req, timeout=90: (_ for _ in ()).throw(_http_error(req.full_url, 429)),
        )
        with pytest.raises(RuntimeError):
            fc.fetch("https://arxiv.org/html/2501.33333", cache_dir=tmp_path, retries=3)
        assert slept, "expected at least one backoff"
        assert min(slept) >= 3.0
        assert slept == [3.0, 6.0], "backoff should double from the polite interval"

    def test_honours_retry_after_over_its_own_backoff(self, monkeypatch, tmp_path):
        slept = []
        monkeypatch.setattr(fc.time, "sleep", slept.append)
        monkeypatch.setattr(
            fc.urllib.request, "urlopen",
            lambda req, timeout=90: (_ for _ in ()).throw(
                _http_error(req.full_url, 429, {"Retry-After": "45"})
            ),
        )
        with pytest.raises(RuntimeError):
            fc.fetch("https://arxiv.org/html/2501.44444", cache_dir=tmp_path, retries=2)
        assert max(slept) == 45.0

    def test_retry_after_is_capped(self, monkeypatch, tmp_path):
        # A server asking for a day off must not hang the firing.
        slept = []
        monkeypatch.setattr(fc.time, "sleep", slept.append)
        monkeypatch.setattr(
            fc.urllib.request, "urlopen",
            lambda req, timeout=90: (_ for _ in ()).throw(
                _http_error(req.full_url, 429, {"Retry-After": "86400"})
            ),
        )
        with pytest.raises(RuntimeError):
            fc.fetch("https://arxiv.org/html/2501.55555", cache_dir=tmp_path, retries=2)
        assert max(slept) == fc.settings()["max_backoff_seconds"]

    def test_error_names_the_url(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            fc.urllib.request, "urlopen",
            lambda req, timeout=90: (_ for _ in ()).throw(_http_error(req.full_url, 429)),
        )
        with pytest.raises(RuntimeError, match="2501.66666"):
            fc.fetch("https://arxiv.org/html/2501.66666", cache_dir=tmp_path, retries=1)


class TestDiskCache:
    def test_existing_disk_file_is_a_hit_and_never_fetched(self, monkeypatch, tmp_path):
        # The 30 MB of populated `research/docs/*_cache/` on a laptop must keep
        # counting, or every local run re-downloads what it already has.
        url = "https://arxiv.org/html/2501.77777"
        (tmp_path / fc.cache_key(url, ".html")).write_text("from disk", encoding="utf-8")

        def boom(req, timeout=90):
            raise AssertionError("must not fetch when disk has it")

        monkeypatch.setattr(fc.urllib.request, "urlopen", boom)
        assert fc.fetch(url, cache_dir=tmp_path, suffix=".html") == "from disk"

    def test_disk_hit_is_promoted_into_the_database(self, monkeypatch, tmp_path):
        # This is how the deployment gets seeded without paying the cold cost:
        # run locally against the deployment's DATABASE_URL and every disk hit
        # lands in Postgres having made no request at all.
        url = "https://arxiv.org/html/2501.88888v1"
        (tmp_path / fc.cache_key(url, ".html")).write_text("body", encoding="utf-8")
        written = []
        monkeypatch.setattr(fc, "db_put", lambda u, b, e: written.append((u, b, e)) or True)
        fc.fetch(url, cache_dir=tmp_path, suffix=".html")
        assert written == [(url, "body", None)]

    def test_promotion_carries_a_ttl_for_a_discovery_url(self, monkeypatch, tmp_path):
        # The companion to the case above, and its absence is why the disk-TTL
        # bug shipped: every promotion test used an immutable URL, so no test
        # could see that the disk path never consulted `_expiry` at all.
        url = "https://ai.meta.com/results/?years[0]=2026&page=1"
        (tmp_path / fc.cache_key(url, ".html")).write_text("listing", encoding="utf-8")
        written = []
        monkeypatch.setattr(fc, "db_put", lambda u, b, e: written.append(e) or True)
        fc.fetch(url, cache_dir=tmp_path, suffix=".html")
        assert written[0] is not None, "a listing page must not be promoted as permanent"

    def test_cache_key_matches_what_the_harvesters_wrote(self):
        # Reproduces the inline key each of the six computed for itself. If
        # this drifts, every existing cache directory silently misses.
        url = "https://export.arxiv.org/api/query?search_query=ti%3A%22Mistral%22"
        assert fc.cache_key(url, ".xml") == (
            "https_export_arxiv_org_api_query_search_query_ti_3A_22Mistral_22.xml"
        )

    def test_database_hit_short_circuits_disk_and_network(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fc, "db_get", lambda url: "from db")

        def boom(req, timeout=90):
            raise AssertionError("must not fetch when the database has it")

        monkeypatch.setattr(fc.urllib.request, "urlopen", boom)
        assert fc.fetch("https://arxiv.org/html/2501.99999", cache_dir=tmp_path) == "from db"


class TestDatabaseRoundTrip:
    """The Postgres path itself, against sqlite."""

    @pytest.fixture
    def engine(self, monkeypatch):
        from sqlalchemy import create_engine

        from app import models as m

        eng = create_engine("sqlite://")
        m.FetchCache.__table__.create(bind=eng)
        monkeypatch.setattr(fc, "_engine", lambda: eng)
        monkeypatch.setattr(fc, "_DB_CHECKED", True)
        monkeypatch.setattr(fc, "db_get", _REAL_DB_GET)
        monkeypatch.setattr(fc, "db_put", _REAL_DB_PUT)
        return eng

    def test_put_then_get(self, engine):
        fc.db_put("https://arxiv.org/html/2501.10101", "stored", None)
        assert fc.db_get("https://arxiv.org/html/2501.10101") == "stored"

    def test_missing_url_is_none(self, engine):
        assert fc.db_get("https://arxiv.org/html/2501.10102") is None

    def test_expired_row_is_a_miss(self, engine):
        url = "https://export.arxiv.org/api/query?search_query=au%3A%22DeepSeek-AI%22"
        fc.db_put(url, "stale", datetime.now(timezone.utc) - timedelta(hours=1))
        assert fc.db_get(url) is None, "an expired discovery answer must be re-fetched"

    def test_unexpired_row_is_a_hit(self, engine):
        url = "https://export.arxiv.org/api/query?search_query=au%3A%22DeepSeek-AI%22&x=2"
        fc.db_put(url, "fresh", datetime.now(timezone.utc) + timedelta(hours=1))
        assert fc.db_get(url) == "fresh"

    def test_second_put_replaces_rather_than_duplicating(self, engine):
        from sqlalchemy import func, select

        from app import models as m
        from app.db import get_session

        url = "https://arxiv.org/html/2501.10103"
        fc.db_put(url, "first", None)
        fc.db_put(url, "second", None)
        session = get_session(engine)
        try:
            assert fc.db_get(url) == "second"
            assert session.scalar(select(func.count()).select_from(m.FetchCache)) == 1
        finally:
            session.close()


class TestDegradation:
    def test_unreachable_database_still_fetches(self, monkeypatch, tmp_path):
        # A cache is an optimisation. A database that has *fallen over* must
        # degrade to live fetching, not fail the harvest.
        #
        # This test used to stub db_get/db_put to a quiet None/False, which the
        # autouse fixture already does — so it asserted only "fetch returns the
        # body when urlopen succeeds" and would have stayed green if the whole
        # error-handling path were deleted (D53b). Now they raise, which is what
        # a dead Postgres actually does.
        from sqlalchemy import create_engine

        # The real db_get/db_put, against an engine whose database has no
        # fetch_cache table — every query raises, which is what a dead or
        # unmigrated Postgres looks like from in here. Stubbing the two
        # functions to a quiet None/False instead (as this test used to) skips
        # the error handling entirely and proves nothing about it.
        monkeypatch.setattr(fc, "db_get", _REAL_DB_GET)
        monkeypatch.setattr(fc, "db_put", _REAL_DB_PUT)
        monkeypatch.setattr(fc, "_engine", lambda: create_engine("sqlite://"))
        monkeypatch.setattr(
            fc.urllib.request, "urlopen", lambda req, timeout=90: _Response(b"live")
        )
        assert fc.fetch("https://arxiv.org/html/2501.10104v1", cache_dir=tmp_path) == "live"

    def test_settings_fall_back_when_config_is_unreadable(self, monkeypatch):
        monkeypatch.setattr(fc, "CONFIG", Path("/nonexistent/pipeline.yaml"))
        monkeypatch.setattr(fc, "_CONFIG_CACHE", None)
        try:
            assert fc.settings()["min_interval_seconds"]["arxiv.org"] == 3.0
        finally:
            fc._CONFIG_CACHE = None


class TestConfigIsReal:
    def test_pipeline_yaml_carries_the_fetch_block(self):
        # These values are config, not code (CLAUDE.md non-negotiable 6), and a
        # silently-missing block would drop us back to the fallbacks without
        # anyone noticing.
        import yaml

        cfg = yaml.safe_load((ROOT / "config" / "pipeline.yaml").read_text())
        assert cfg["fetch"]["min_interval_seconds"]["arxiv.org"] >= 3.0
        assert cfg["fetch"]["discovery_ttl_hours"] > 0
        assert cfg["fetch"]["retries"] >= 2


class TestEveryArxivHarvesterUsesTheSharedLayer:
    def test_no_harvester_keeps_a_private_fetch_loop(self):
        # The regression that matters: someone re-inlines a urlopen loop in one
        # harvester and it silently stops sharing the throttle.
        papers = ROOT / "research" / "papers"
        offenders = []
        for name in ("arxiv_resolve.py", "deepseek_harvest.py", "meta_harvest.py",
                     "mistral_harvest.py", "deepmind_harvest.py"):
            src = (papers / name).read_text()
            if "urllib.request.urlopen" in src:
                offenders.append(name)
        assert not offenders, f"private fetch loop reintroduced in: {offenders}"


class TestReviewFindings:
    """Three defects an independent review found after the first pass.

    Each one is a case the original suite passed while the code was wrong, so
    each gets a test rather than just a fix.
    """

    def test_stale_disk_file_is_not_served(self, monkeypatch, tmp_path):
        # The bad one. The disk branch returned the file without consulting the
        # TTL at all, so a year-old `au:"DeepSeek-AI"` answer was a hit -- and
        # was then written to the database stamped `now + 336h`, laundering a
        # stale body into a fresh one. That is discovery frozen, which is the
        # failure this module exists to avoid, reached by the one path that
        # skipped the check.
        import os

        url = 'https://export.arxiv.org/api/query?search_query=au%3A"DeepSeek-AI"'
        path = tmp_path / fc.cache_key(url, ".xml")
        path.write_text("a year-old answer", encoding="utf-8")
        old = (datetime.now(timezone.utc) - timedelta(days=400)).timestamp()
        os.utime(path, (old, old))

        monkeypatch.setattr(
            fc.urllib.request, "urlopen", lambda req, timeout=90: _Response(b"fresh")
        )
        assert fc.fetch(url, cache_dir=tmp_path, suffix=".xml") == "fresh"

    def test_fresh_disk_file_is_still_served(self, monkeypatch, tmp_path):
        # The other side of it: the fix must not throw away a usable cache.
        url = 'https://export.arxiv.org/api/query?search_query=au%3A"DeepSeek-AI"&v=2'
        (tmp_path / fc.cache_key(url, ".xml")).write_text("recent", encoding="utf-8")

        def boom(req, timeout=90):
            raise AssertionError("must not fetch a file still inside its TTL")

        monkeypatch.setattr(fc.urllib.request, "urlopen", boom)
        assert fc.fetch(url, cache_dir=tmp_path, suffix=".xml") == "recent"

    def test_promoted_disk_file_keeps_its_real_age(self, monkeypatch, tmp_path):
        # Promotion must carry the file's own age into `expires_at`. Stamping
        # `now + ttl` would reset the clock on every run, so a discovery answer
        # could never expire as long as a stale file sat on disk.
        import os

        url = 'https://export.arxiv.org/api/query?search_query=au%3A"X"'
        path = tmp_path / fc.cache_key(url, ".xml")
        path.write_text("body", encoding="utf-8")
        eight_days = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
        os.utime(path, (eight_days, eight_days))

        written = []
        monkeypatch.setattr(fc, "db_put", lambda u, b, e: written.append(e) or True)
        fc.fetch(url, cache_dir=tmp_path, suffix=".xml")
        # 336h TTL minus 8 days already elapsed leaves ~6 days, not ~14.
        remaining = written[0] - datetime.now(timezone.utc)
        assert timedelta(days=5) < remaining < timedelta(days=7)

    def test_immutable_disk_file_never_goes_stale(self, tmp_path, monkeypatch):
        import os

        url = "https://arxiv.org/html/2501.12948v2"
        path = tmp_path / fc.cache_key(url, ".html")
        path.write_text("paper", encoding="utf-8")
        ancient = (datetime.now(timezone.utc) - timedelta(days=3000)).timestamp()
        os.utime(path, (ancient, ancient))

        def boom(req, timeout=90):
            raise AssertionError("a versioned arXiv id is immutable at any age")

        monkeypatch.setattr(fc.urllib.request, "urlopen", boom)
        assert fc.fetch(url, cache_dir=tmp_path, suffix=".html") == "paper"

    def test_404_is_not_retried(self, monkeypatch, tmp_path):
        # Papers with no arXiv HTML rendering are ordinary and already handled
        # (`arxiv_html_unavailable`). Retrying spent four requests and ~30s of
        # backoff each, per firing, adding load to the host that rate-limited
        # us -- on the one path guaranteed to fail.
        calls = {"n": 0}

        def urlopen(req, timeout=90):
            calls["n"] += 1
            raise _http_error(req.full_url, 404)

        monkeypatch.setattr(fc.urllib.request, "urlopen", urlopen)
        with pytest.raises(RuntimeError):
            fc.fetch("https://arxiv.org/html/2501.00000", cache_dir=tmp_path)
        assert calls["n"] == 1, f"404 retried {calls['n']} times"

    def test_429_is_still_retried(self, monkeypatch, tmp_path):
        calls = {"n": 0}

        def urlopen(req, timeout=90):
            calls["n"] += 1
            raise _http_error(req.full_url, 429)

        monkeypatch.setattr(fc.urllib.request, "urlopen", urlopen)
        with pytest.raises(RuntimeError):
            fc.fetch("https://arxiv.org/html/2501.00001", cache_dir=tmp_path, retries=3)
        assert calls["n"] == 3

    def test_503_and_timeouts_are_retried(self, monkeypatch, tmp_path):
        for raiser, label in (
            (lambda u: _http_error(u, 503), "503"),
            (lambda u: urllib.error.URLError("connection reset"), "URLError"),
        ):
            calls = {"n": 0}

            def urlopen(req, timeout=90, _r=raiser):
                calls["n"] += 1
                raise _r(req.full_url)

            monkeypatch.setattr(fc.urllib.request, "urlopen", urlopen)
            with pytest.raises(RuntimeError):
                fc.fetch(f"https://arxiv.org/html/2501.0000{label[0]}",
                         cache_dir=tmp_path, retries=2)
            assert calls["n"] == 2, f"{label} was not retried"

    def test_engine_never_creates_the_table(self):
        # `op.create_table` has no checkfirst, so pre-creating a table a pending
        # migration is about to add kills that migration on every firing until a
        # human intervenes -- documented at app/db.py:181-186. ensure_schema
        # owns the schema; this module only probes.
        src = (ROOT / "research" / "papers" / "fetch_cache.py").read_text()
        assert "__table__.create" not in src
        assert "create_all" not in src

    def test_missing_table_degrades_loudly_not_silently(self, monkeypatch, capsys):
        from sqlalchemy import create_engine

        monkeypatch.setattr(fc, "_DB_CHECKED", False)
        monkeypatch.setattr(fc, "_ENGINE", None)
        # An engine whose database has no fetch_cache table at all.
        monkeypatch.setattr("app.db.get_engine", lambda: create_engine("sqlite://"))
        assert fc._engine() is None
        assert "fetch cache unavailable" in capsys.readouterr().err

    def test_malformed_retry_after_does_not_escape(self, monkeypatch, tmp_path):
        # `email.utils.parsedate_to_datetime` RAISES on unparseable input on
        # 3.10+ (it does not return None), so a `Retry-After: soon` escaped
        # fetch() as a ValueError. The harvesters catch only RuntimeError, so
        # that killed the whole harvest rather than one paper -- the same blast
        # radius this module exists to remove (D53b).
        assert fc._retry_after(_http_error("u", 429, {"Retry-After": "soon"})) is None

        monkeypatch.setattr(
            fc.urllib.request, "urlopen",
            lambda req, timeout=90: (_ for _ in ()).throw(
                _http_error(req.full_url, 429, {"Retry-After": "next tuesday"})
            ),
        )
        # RuntimeError, not ValueError: the caller's contract is preserved.
        with pytest.raises(RuntimeError):
            fc.fetch("https://arxiv.org/html/2501.20202v1", cache_dir=tmp_path, retries=2)


class TestThrottleConcurrency:
    def test_lock_is_not_held_across_the_wait(self, monkeypatch):
        # Holding _LOCK across time.sleep would serialise every host behind
        # arXiv's 3s spacing, so a mistral.ai fetch would pay arXiv's rate
        # limit. Nothing calls this concurrently today, which is exactly why it
        # would go unnoticed later (D53b).
        held = []
        monkeypatch.setattr(fc.time, "sleep", lambda s: held.append(fc._LOCK.locked()))
        fc._wait_turn("arxiv.org", 3.0)
        fc._wait_turn("arxiv.org", 3.0)
        assert held, "expected a wait on the second call"
        assert not any(held), "_LOCK was held while sleeping"

    def test_slots_are_reserved_so_callers_queue_rather_than_collide(self, monkeypatch):
        # The clock is pinned, and pinned to *this* value, on purpose. Adding
        # 3.0 to a float in (61, 64) crosses a binade boundary, so the sum
        # rounds and `second - first` comes back as 2.999999999999993. Written
        # as `second - first >= 3.0` this test failed on arithmetic rather than
        # on behaviour. It is not a rare accident: `time.monotonic()` is time
        # since boot, a fresh CI runner reaches this test around a minute in,
        # and the three seconds below every power of two are a failure band.
        # 61.451892749 is the value CI actually went red on.
        #
        # Compare against `first + interval` instead. That is the number
        # `_wait_turn` computes, so the comparison is exact at any clock, and
        # it still fails if the reservation is dropped and `now` recorded
        # instead -- which is the regression this test exists for.
        monkeypatch.setattr(fc, "_LAST_REQUEST", {})
        monkeypatch.setattr(fc.time, "sleep", lambda s: None)
        monkeypatch.setattr(fc.time, "monotonic", lambda: 61.451892749)
        fc._wait_turn("arxiv.org", 3.0)
        first = fc._LAST_REQUEST["arxiv.org"]
        fc._wait_turn("arxiv.org", 3.0)
        second = fc._LAST_REQUEST["arxiv.org"]
        assert second >= first + 3.0, "second caller must be spaced from the first"
