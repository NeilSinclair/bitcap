"""One HTTP fetch layer for the papers harvesters: cache, throttle, retry.

Six harvesters under this directory each grew their own `fetch()`, and all six
were the same twenty lines: key the URL, check a directory under
`research/docs/*_cache/`, else download and write it back. That was fine on a
laptop and did nothing at all in the deployment, which is where it mattered.

**Why this module exists.** `research/docs/*_cache/` is in `.gitignore` and in
`.dockerignore`, and the Render cron has no disk. So on the deployment the
cache never existed: every nightly firing started cold and replayed roughly
seventy requests at arXiv, five of the six harvesters hitting `arxiv.org` for
paper HTML on top of the API queries. On 2026-09-04 arXiv rate-limited the
egress IP and four papers sources failed in the same firing — two of them on
their first request, because `deepseek_harvest.fetch` had no retry at all
(docs/decisions.md D53).

Three things are therefore different here, and each one is load-bearing:

**The cache is in Postgres.** It is the only store that survives a firing on
Render. Disk is still read when a `cache_dir` is given, so an existing local
cache is a hit and gets promoted into the database rather than re-downloaded —
that is also how the deployment is seeded, with no code and no extra requests:
point `DATABASE_URL` at it and run the papers leg locally, and every disk hit
lands in Postgres having fetched nothing.

**Not everything is cacheable forever.** A versioned arXiv id is the same
document for all time and is stored permanently. A *discovery* URL is not:
`au:"DeepSeek-AI"` and Meta's paginated listing return different answers
precisely when a new paper appears, and caching those permanently would freeze
the register on whatever it knew the first night and go on reporting success.
That is a worse failure than the one this module fixes, because nothing would
surface it. Discovery URLs carry a TTL; see :func:`_expiry`.

**The throttle is shared and the backoff is not shorter than it.** The old code
had each harvester holding a private opinion about arXiv's rate limit, and
`arxiv_resolve.fetch` backed off 1s then 2s against its own 3.0s polite pause —
it retried a 429 *faster* than the rate it had already decided was polite.
Here one token bucket covers every `arxiv.org` host, `Retry-After` is honoured
when the server sends it, and no backoff is ever shorter than the interval.
"""

from __future__ import annotations

import email.utils
import hashlib
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
CONFIG = ROOT / "config" / "pipeline.yaml"
UA = "bitcap-case-study research spike (contact: neilaf4@gmail.com)"

# Distinguishes "work out the TTL from the URL" from an explicit "never
# expires". `None` already means the second, so it cannot also mean the first.
AUTO = object()

# "This URL is how we learn a paper EXISTS" — an author search, a sitemap, a
# paginated index. Passed by the caller, because it is a property of the
# question being asked and not of the URL's shape: `deepseek_harvest.arxiv_query`
# issues both `au:"DeepSeek-AI"` (a listing) and `ti:"..."` (a lookup of one
# known paper) against the same endpoint, and only the first is discovery in
# this sense.
#
# These are few, and they are the only URLs whose staleness delays a *finding*.
# Under the single discovery TTL a new DeepSeek paper could go unseen for a
# fortnight, because DeepSeek's paper list has no other source.
LISTING = object()

# Fallback values, used when config/pipeline.yaml is unreadable — a standalone
# research script must not die because a config file moved. The real values,
# and the reasoning, live under `fetch:` in that file.
_DEFAULTS = {
    "min_interval_seconds": {"arxiv.org": 3.0},
    "default_min_interval_seconds": 1.0,
    "discovery_ttl_hours": 336,
    "listing_ttl_hours": 24,
    "discovery_ttl_jitter": 0.25,
    "retries": 4,
    "max_backoff_seconds": 120.0,
}

# A versioned arXiv id names one immutable document: v2 of a paper is never
# edited into a different v2. These are the heavy fetches (full paper HTML, up
# to ~1 MB each) and the bulk of the request volume, so making them permanent
# is most of the win. Anything that does not match here gets a TTL, which is
# the safe direction to be wrong in.
#
# **The version suffix is required, and that is the whole point.** An
# unversioned id resolves to the *latest* version, so it is mutable by
# definition. `deepmind_harvest.py:66` strips the version deliberately and
# line 189 builds `arxiv.org/html/<bare id>` from it — caching that forever
# would pin a paper's byline to whatever v1 said, permanently and silently,
# on the harvester whose entire output is bylines. A preprint gaining authors
# before camera-ready is routine. Unversioned ids take the TTL instead.
_IMMUTABLE = re.compile(
    r"^https?://(?:www\.)?arxiv\.org/(?:html|abs|pdf)/\d{4}\.\d{4,5}v\d+/?$"
)

# Statuses a retry can actually fix. Everything else — 404 above all — is the
# server's answer, and asking again four times only adds load to the host that
# just rate-limited us.
#
# 403 is here on evidence, not on principle. It is normally "forbidden, don't
# ask again", but arXiv returns it as a *throttle* response: a live run against
# `arxiv.org/html/` hit a 403 mid-batch that was a rate limit rather than a
# real refusal, which is why deepmind_harvest grew a retry in the first place
# (its module docstring records the observation). Dropping 403 from this set
# re-breaks that harvester, and its test says so.
RETRYABLE_STATUS = frozenset({403, 408, 425, 429, 500, 502, 503, 504})

_LOCK = threading.Lock()
_LAST_REQUEST: dict[str, float] = {}
_DB_LOCK = threading.Lock()
_CONFIG_CACHE: dict | None = None
_ENGINE = None
_DB_CHECKED = False


def settings() -> dict:
    """Read the `fetch:` block from config/pipeline.yaml, with fallbacks.

    Returns:
        The merged settings mapping. Cached for the process's lifetime;
        `config/validate.py::check_pipeline` is what stops a wrong value
        reaching here in the first place.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        loaded = {}
        try:
            loaded = (yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}).get("fetch") or {}
        except (OSError, yaml.YAMLError):
            pass
        _CONFIG_CACHE = {**_DEFAULTS, **loaded}
    return _CONFIG_CACHE


def cache_key(url: str, suffix: str) -> str:
    """The on-disk filename for a URL.

    Reproduces byte-for-byte what the six harvesters each computed inline, so
    an existing `research/docs/*_cache/` directory is still read as a hit
    rather than silently re-downloaded.

    Args:
        url: Absolute URL being cached.
        suffix: File extension the harvester's own cache used (`.xml`, `.txt`,
            `.html`) -- part of the key, so it has to match.

    Returns:
        Filename, punctuation collapsed to underscores and truncated to 150
        characters. Lossy and one-way: a URL cannot be recovered from it.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_")[:150] + suffix


def _family(url: str) -> str:
    """The throttle bucket a URL belongs to.

    `export.arxiv.org` and `arxiv.org` are one operator enforcing one limit, so
    they share a bucket. Treating them separately is how the old code managed
    to look polite per-module and be impolite in aggregate.

    Args:
        url: Absolute URL.

    Returns:
        The configured domain this URL's host falls under, or the bare host
        when none matches. Matching is exact-or-subdomain, so a config key
        without a dot can never match -- which `check_pipeline` rejects.
    """
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for domain in settings()["min_interval_seconds"]:
        if host == domain or host.endswith("." + domain):
            return domain
    return host


def _interval(family: str) -> float:
    """Minimum seconds between two live requests to one bucket.

    Args:
        family: A bucket name from :func:`_family`.

    Returns:
        The configured interval for that bucket, else the default.
    """
    cfg = settings()
    return float(cfg["min_interval_seconds"].get(family, cfg["default_min_interval_seconds"]))


def _wait_turn(family: str, interval: float) -> None:
    """Block until this bucket is allowed another live request.

    Before the request rather than after it. The old code slept *after* a
    successful fetch, which means the first request of a process fires with no
    spacing at all — exactly the request that fails when the previous firing
    left the address rate-limited.

    Args:
        family: Bucket to wait on.
        interval: Minimum seconds between two requests to that bucket.
    """
    with _LOCK:
        now = time.monotonic()
        last = _LAST_REQUEST.get(family)
        # Reserve this family's next slot before releasing, so a concurrent
        # caller queues behind it rather than racing for the same instant.
        start = now if last is None else max(now, last + interval)
        _LAST_REQUEST[family] = start
    # Sleep OUTSIDE the lock. Holding it across the wait would serialise every
    # host behind arXiv's 3s spacing, so a mistral.ai fetch would pay arXiv's
    # rate limit. Nothing calls this concurrently today (the orchestrator runs
    # sources serially), which is exactly why it would be missed later.
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    """Seconds to wait per the server's own `Retry-After`, if it sent one.

    Accepts both forms the header allows: a count of seconds, or an HTTP date.
    Nothing in the previous code read this at all, so a server that said
    exactly how long to wait was ignored and guessed at instead.

    Args:
        exc: The HTTP error to read the header from.

    Returns:
        Seconds to wait, or None when the header is absent or unparseable.
        Never raises: the caller has a backoff of its own to fall back on.
    """
    raw = None
    try:
        raw = exc.headers.get("Retry-After")
    except AttributeError:
        return None
    if not raw:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return float(raw)
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        # `parsedate_to_datetime` RAISES on unparseable input on 3.10+; it does
        # not return None, so a `parsed is None` guard here was dead code. A
        # `Retry-After: soon` from arXiv or a CDN in front of it would have
        # escaped fetch() as a ValueError, and the harvesters catch only
        # RuntimeError — killing the whole harvest rather than one paper, which
        # is the exact blast radius this module was written to remove.
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


def _jitter(url: str) -> float:
    """A stable fraction in [0, 1) for one URL, used to stagger its expiry.

    **Why this exists.** Every discovery URL is first fetched in the same
    firing, so a single flat TTL means every one of them lapses in the same
    firing too — and that firing replays the whole discovery set at arXiv in one
    burst and gets rate-limited. D53 assumed these would "re-run on staggered
    expiry"; nothing staggered them. Measured on 2026-09-06: 35 cached rows,
    every one expiring on the same date.

    `hashlib`, not the built-in `hash()`, and that is the whole point: `hash()`
    is salted per process, so the same URL would land on a different expiry
    every run and a body could be re-stamped further into the future each time
    it was touched. This has to be a property of the URL, not of the process
    that happened to ask.

    Args:
        url: Absolute URL.

    Returns:
        A deterministic fraction in [0, 1).
    """
    digest = hashlib.blake2b(url.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def _ttl(url: str, ttl_hours) -> timedelta | None:
    """How long this URL's body stays trustworthy. None means forever.

    Forever is only correct when the URL names an immutable document.
    Everything else — API queries, listings, sitemaps — gets a TTL, because
    those are the URLs whose whole purpose is to return something different
    once a new paper exists.

    Three cases, in the order they are decided:

    * an explicit `ttl_hours` (including `None` for permanent) is obeyed;
    * :data:`LISTING` takes the short window, undithered — these are the URLs
      that decide how fast a *new* paper is found, there are only a handful of
      them, and asking daily is what makes the answer worth trusting;
    * everything else takes the discovery window, spread per URL by
      :func:`_jitter` so the set stops coming due together.

    Separate from :func:`_expiry` because the disk-cache path needs the
    *duration* to measure a file's age against, not an absolute instant
    computed from now: a stale file measured from now is never stale.
    """
    cfg = settings()
    if ttl_hours is LISTING:
        return timedelta(hours=float(cfg["listing_ttl_hours"]))
    if ttl_hours is not AUTO:
        return None if ttl_hours is None else timedelta(hours=ttl_hours)
    if _IMMUTABLE.match(url):
        return None
    # Only ever *extends*, never shortens: the configured value stays the floor
    # it reads as, and the spread is the tail above it. Dithering downwards
    # would quietly make every URL fresher than the number in the config file
    # says, which is the wrong direction to be surprising in.
    hours = float(cfg["discovery_ttl_hours"])
    spread = float(cfg["discovery_ttl_jitter"])
    return timedelta(hours=hours * (1.0 + spread * _jitter(url)))


def _expiry(url: str, ttl_hours, *, since: datetime | None = None) -> datetime | None:
    """When this URL's cached body stops being trustworthy, or None for never.

    Args:
        url: The URL being cached.
        ttl_hours: AUTO to decide from the URL, LISTING for the short
            discovery window, None for permanent, or an explicit hour count.
        since: When the body was actually fetched. Defaults to now. The disk
            path passes the file's mtime so a promoted file keeps its real age
            instead of being laundered into a fresh one.
    """
    ttl = _ttl(url, ttl_hours)
    if ttl is None:
        return None
    return (since or datetime.now(timezone.utc)) + ttl


# --------------------------------------------------------------------------
# Postgres backing store
# --------------------------------------------------------------------------

def _engine():
    """The shared engine, or None when no database is reachable.

    Resolved once. A cache is an optimisation, so an unreachable database
    degrades to disk-and-live rather than failing the harvest — but it says so
    on stderr, because a cache that silently stopped working looks exactly like
    one that is working and would put us back at seventy cold requests a night.

    The table is **probed, never created**. `app/db.py::ensure_schema` owns the
    schema, and creating a table out of band here is the precise footgun that
    function documents: `op.create_table` has no `checkfirst`, so pre-creating
    a table a pending migration is about to add kills that migration on "table
    already exists" — every firing, until a human intervenes. A missing table
    means this deployment has not migrated yet, which is a loud no-cache run,
    not something to paper over.
    """
    global _ENGINE, _DB_CHECKED
    with _DB_LOCK:
        if _DB_CHECKED:
            return _ENGINE
        return _resolve_engine()


def _resolve_engine():
    """Build the engine once and record the outcome. Callers hold `_DB_LOCK`.

    Split out so `_DB_CHECKED` is set *after* the engine resolves rather than
    before: setting it first meant a second caller arriving mid-initialisation
    read `_ENGINE is None`, concluded there was no cache, and fetched live
    without a word.
    """
    global _ENGINE, _DB_CHECKED
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from sqlalchemy import inspect

        from app import models as m
        from app.db import get_engine, load_env

        load_env()
        engine = get_engine()
        if not inspect(engine).has_table(m.FetchCache.__tablename__):
            raise RuntimeError(
                f"no {m.FetchCache.__tablename__} table; run `alembic upgrade head`"
            )
        _ENGINE = engine
    except Exception as exc:  # noqa: BLE001 - any failure means "no cache", not "no harvest"
        print(f"  ! fetch cache unavailable, every fetch will be live: {exc}", file=sys.stderr)
        _ENGINE = None
    _DB_CHECKED = True
    return _ENGINE


def db_get(url: str) -> str | None:
    """The cached body for `url`, or None if absent or expired.

    Args:
        url: Absolute URL to look up.

    Returns:
        The stored body, or None when there is no row, the row has expired, or
        the database is unreachable. A database failure is reported on stderr
        and treated as a miss -- a cache is an optimisation, and losing it must
        not fail the harvest.
    """
    engine = _engine()
    if engine is None:
        return None
    from sqlalchemy import select

    from app import models as m
    from app.db import get_session

    session = get_session(engine)
    try:
        row = session.scalars(select(m.FetchCache).where(m.FetchCache.url == url)).first()
        if row is None:
            return None
        if row.expires_at is not None:
            expires = row.expires_at
            if expires.tzinfo is None:  # sqlite hands these back naive
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= datetime.now(timezone.utc):
                return None
        return row.body
    except Exception as exc:  # noqa: BLE001
        print(f"  ! fetch cache read failed for {url}: {exc}", file=sys.stderr)
        return None
    finally:
        session.close()


def db_put(url: str, body: str, expires_at: datetime | None) -> bool:
    """Store a body against `url`, replacing any existing row.

    Args:
        url: Absolute URL the body came from.
        body: Decoded response body.
        expires_at: When to stop trusting it, or None for never. None is only
            correct for an immutable document (see :func:`_ttl`).

    Returns:
        Whether the row was written. False means the cache is unavailable, not
        that the fetch failed.
    """
    engine = _engine()
    if engine is None:
        return False
    from sqlalchemy import select

    from app import models as m
    from app.db import get_session

    session = get_session(engine)
    try:
        row = session.scalars(select(m.FetchCache).where(m.FetchCache.url == url)).first()
        if row is None:
            session.add(
                m.FetchCache(
                    url=url,
                    body=body,
                    fetched_at=datetime.now(timezone.utc),
                    expires_at=expires_at,
                )
            )
        else:
            row.body = body
            row.fetched_at = datetime.now(timezone.utc)
            row.expires_at = expires_at
        session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        print(f"  ! fetch cache write failed for {url}: {exc}", file=sys.stderr)
        return False
    finally:
        session.close()


# --------------------------------------------------------------------------
# The fetch
# --------------------------------------------------------------------------

def fetch(
    url: str,
    *,
    cache_dir: Path | None = None,
    suffix: str = ".txt",
    user_agent: str | None = UA,
    retries: int | None = None,
    ttl_hours=AUTO,
) -> str:
    """Fetch a URL through the cache, the throttle and the retry policy.

    Read order is database, then disk, then the network. Disk is read but never
    required: it is how a populated `research/docs/*_cache/` on a laptop counts
    as a hit and gets promoted into Postgres instead of being downloaded again.

    Args:
        url: Absolute URL.
        cache_dir: Legacy on-disk cache to read, and to write alongside the
            database. None uses the database only, which is the deployment.
        suffix: File extension the harvester's own cache used, so its existing
            files are found.
        user_agent: UA to send. None sends no override — `ai.meta.com` rejects
            any browser-style UA with a 400 and accepts urllib's own default.
        retries: Attempts before giving up. None takes the configured value.
        ttl_hours: AUTO decides from the URL (see :func:`_expiry`); LISTING
            marks a URL that is how we learn a paper exists and takes the short
            window; None stores permanently; a number sets an explicit TTL.

    Returns:
        Decoded response body.

    Raises:
        RuntimeError: If every attempt fails. One dead source does not abort a
            run (docs/decisions.md D27); the caller decides.
    """
    cached = db_get(url)
    if cached is not None:
        return cached

    path = cache_dir / cache_key(url, suffix) if cache_dir is not None else None
    if path is not None and path.exists():
        # A disk file is subject to the same TTL as a database row, measured
        # from when it was written. Skipping this check served a year-old
        # `au:"DeepSeek-AI"` answer as a hit and then stamped it `now + 336h`,
        # laundering a stale body into a fresh one — the exact freezing of
        # discovery this module's docstring says must not happen.
        ttl = _ttl(url, ttl_hours)
        written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if ttl is None or datetime.now(timezone.utc) - written <= ttl:
            body = path.read_text(encoding="utf-8")
            # Promote to the database so the next run — and the deployment,
            # once seeded — does not need this directory to exist. The expiry
            # carries the file's real age, not a fresh one.
            db_put(url, body, _expiry(url, ttl_hours, since=written))
            return body

    cfg = settings()
    attempts = cfg["retries"] if retries is None else retries
    family = _family(url)
    interval = _interval(family)
    headers = {"User-Agent": user_agent} if user_agent else {}
    last_error: Exception | None = None

    for attempt in range(attempts):
        _wait_turn(family, interval)
        try:
            req = urllib.request.Request(url, headers=headers)
            body = urllib.request.urlopen(req, timeout=90).read().decode("utf-8", "replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            # A 404 is an answer, not a failure to get one. Retrying it spent
            # four arXiv requests and ~30s per missing paper per firing, which
            # raised our volume on the one path that always fails — and papers
            # with no arXiv HTML rendering are ordinary and handled
            # (`arxiv_html_unavailable` in meta/mistral/deepmind). Retry only
            # what a retry can fix.
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in RETRYABLE_STATUS:
                break
            if attempt == attempts - 1:
                break
            # Never back off by less than the polite interval: a 429 means the
            # server is already unhappy, and retrying sooner than the rate we
            # had committed to is how the old code turned one 429 into three.
            wait = max(interval, min(interval * (2**attempt), float(cfg["max_backoff_seconds"])))
            if isinstance(exc, urllib.error.HTTPError):
                served = _retry_after(exc)
                if served is not None:
                    wait = max(wait, min(served, float(cfg["max_backoff_seconds"])))
            time.sleep(wait)
            continue

        expires = _expiry(url, ttl_hours)
        db_put(url, body, expires)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return body

    raise RuntimeError(f"fetch failed: {url}: {last_error}")
