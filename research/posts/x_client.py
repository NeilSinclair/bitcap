"""Authenticated reads against the X API v2, with a spend ceiling that binds.

X bills per resource RETURNED, not per request: $0.005 a post, $0.010 a user
(https://docs.x.com/x-api/getting-started/pricing, fetched 2026-09-05). So the
cost of this leg is decided entirely by how many items we ask for, and the two
levers that matter are both server-side on the timeline endpoint -- `start_time`
and `exclude=[replies,retweets]` -- because what the server filters is never
billed.

THIS MODULE NEVER PAGINATES. That is the design, not an omission. `max_results`
caps a single page at 100, so refusing to follow `next_token` converts every
call into a known, bounded charge before it is made. A pagination loop over a
prolific account is precisely how a $5 balance disappears into one timeline.
:class:`Spend` enforces the same thing a second time, predictively, so a bug in
the caller's arithmetic cannot outspend the configured ceiling either.

WHY NOT `research/papers/fetch_cache.py`. That module is the shared fetcher and
this leg deliberately does not use it. Three blocking reasons, none of them
stylistic: `fetch()` takes no `headers=` argument, so an `Authorization` header
cannot be passed; it is GET-only against HTML; and its Postgres cache is keyed
on the URL alone (`app/models.py` FetchCache.url is unique), so two requests
differing only by token or by query would collide, and an authenticated response
body would be stored under a bare URL key. Extending it to carry credentials
means changing the cache key of every existing entry. The pattern followed
instead is `research/github/harvest_github.py`, which is already this repo's
accepted shape for a token-authenticated JSON API. The duplication is real and
is recorded in docs/decisions.md D63 rather than left to be discovered.

The polite interval between calls is read from the same `fetch` block of
config/pipeline.yaml that the HTML fetcher uses, so there is one place where
per-host rate limits are configured.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, parse, request

import yaml

ROOT = Path(__file__).parent.parent.parent
CONFIG = ROOT / "config"

UA = "bitcap-case-study research spike (contact: neilaf4@gmail.com)"

# 429 is the documented rate-limit response; 5xx are transient. 401/403 are
# credential problems and must fail immediately -- retrying a bad token four
# times just delays a clear error into a confusing one.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# The fields we ask for, and nothing beyond them. Every extra expansion is
# another resource X can bill for.
POST_FIELDS = "created_at,entities,referenced_tweets,public_metrics,lang"
USER_FIELDS = "id,name,username,description,verified,verified_type,public_metrics"


class SpendCeiling(RuntimeError):
    """Raised when a call would take the run past its configured ceiling.

    Attributes:
        resource: Either ``"posts"`` or ``"users"``.
        would_be: The total the refused call would have reached.
        limit: The ceiling from config/posts_sources.yaml.
    """

    def __init__(self, resource: str, would_be: int, limit: int):
        self.resource, self.would_be, self.limit = resource, would_be, limit
        super().__init__(
            f"refusing to read {resource}: would reach {would_be}, ceiling is {limit}")


class Spend:
    """Running tally of billable resources, checked before each call.

    The check is predictive on purpose. Asking forgiveness after a 100-post page
    has been billed is not a ceiling, it is a report.

    Attributes:
        posts: Posts billed so far.
        users: User records billed so far.
    """

    def __init__(self, max_posts: int, max_users: int,
                 post_usd: float = 0.005, user_usd: float = 0.010):
        self.max_posts, self.max_users = max_posts, max_users
        self.post_usd, self.user_usd = post_usd, user_usd
        self.posts = self.users = 0

    def reserve_posts(self, n: int) -> None:
        """Claim headroom for `n` posts, or refuse.

        Args:
            n: Posts the caller is about to request.

        Raises:
            SpendCeiling: If the request would breach the ceiling.
        """
        if self.posts + n > self.max_posts:
            raise SpendCeiling("posts", self.posts + n, self.max_posts)

    def reserve_users(self, n: int) -> None:
        """Claim headroom for `n` user lookups, or refuse.

        Args:
            n: User records the caller is about to request.

        Raises:
            SpendCeiling: If the request would breach the ceiling.
        """
        if self.users + n > self.max_users:
            raise SpendCeiling("users", self.users + n, self.max_users)

    def bill_posts(self, n: int) -> None:
        """Record `n` posts as billed."""
        self.posts += n

    def bill_users(self, n: int) -> None:
        """Record `n` user records as billed."""
        self.users += n

    @property
    def usd(self) -> float:
        """Money spent so far, at the configured published rates."""
        return round(self.posts * self.post_usd + self.users * self.user_usd, 4)

    def snapshot(self) -> dict:
        """A recordable summary of the run's spend."""
        return {"posts": self.posts, "users": self.users, "usd": self.usd,
                "max_posts": self.max_posts, "max_users": self.max_users}


def settings(path: Path | None = None) -> dict:
    """Read config/posts_sources.yaml.

    Args:
        path: Override for the config file, for tests.

    Returns:
        The parsed config.
    """
    return yaml.safe_load((path or CONFIG / "posts_sources.yaml").read_text())


def interval(host: str = "api.x.com") -> float:
    """Polite seconds between calls, from the shared `fetch` block.

    Read from config/pipeline.yaml rather than restated here, so per-host rate
    limits live in one file for both fetchers.

    Args:
        host: Host to look up.

    Returns:
        Minimum seconds between requests.
    """
    try:
        cfg = yaml.safe_load((CONFIG / "pipeline.yaml").read_text()).get("fetch", {})
    except (OSError, yaml.YAMLError):
        return 1.0
    return float(cfg.get("min_interval_seconds", {}).get(
        host, cfg.get("default_min_interval_seconds", 1.0)))


def load_token(env_name: str = "X_BEARER_TOKEN") -> str:
    """Read the bearer token from the environment or the repo-root .env.

    Args:
        env_name: Environment variable to read.

    Returns:
        The token.

    Raises:
        SystemExit: If no token is available. Exiting rather than returning None
            keeps a missing credential from being reported as an empty corpus.
    """
    token = os.environ.get(env_name)
    if not token:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith(f"{env_name}="):
                    token = line.split("=", 1)[1].strip().strip("'\"")
    if not token:
        sys.exit(f"{env_name} not set (environment or .env)")
    return token


def _call(url: str, token: str, retries: int = 4, pause: float | None = None):
    """Issue one authenticated GET, with backoff on rate limits and 5xx.

    Args:
        url: Absolute endpoint URL including query string.
        token: Bearer token.
        retries: Attempts before giving up.
        pause: Seconds to wait before the request; defaults to the configured
            per-host interval.

    Returns:
        The parsed JSON body.

    Raises:
        RuntimeError: If every attempt fails.
        urllib.error.HTTPError: On a non-retryable status, unchanged, so a 401
            reads as a credential problem rather than a timeout.
    """
    headers = {"Authorization": f"Bearer {token}", "User-Agent": UA}
    wait_before = interval() if pause is None else pause

    for attempt in range(retries):
        if wait_before:
            time.sleep(wait_before)
        try:
            with request.urlopen(
                    request.Request(url, headers=headers), timeout=60) as resp:
                return json.loads(resp.read())
        except error.HTTPError as exc:
            if exc.code not in RETRYABLE_STATUS or attempt == retries - 1:
                raise
            # X signals the reset as a Unix timestamp, not a duration.
            reset = exc.headers.get("x-rate-limit-reset")
            wait = 2.0 ** attempt
            if exc.code == 429 and reset:
                wait = max(5.0, float(reset) - time.time() + 5)
            print(f"    {exc.code}, sleeping {wait:.0f}s", flush=True)
            time.sleep(min(wait, 900))
        except (error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(2.0 ** attempt)
    raise RuntimeError(f"failed after {retries} attempts: {url}")


def users_by(handles: list[str], token: str, spend: Spend,
             base_url: str = "https://api.x.com/2") -> tuple[dict, list[str]]:
    """Resolve handles to user records in as few requests as possible.

    Does three jobs in one call, which is why it runs first. It yields the
    numeric ids the timeline endpoint needs; it returns each account's real name
    and bio, which is the first direct fetch-verification of handles that
    config/people.yaml has only ever had indirect evidence for; and it surfaces
    dead or renamed handles before their timelines are paid for.

    Args:
        handles: Bare handles, no leading "@".
        token: Bearer token.
        spend: Ceiling enforcer, checked before each request.
        base_url: API root.

    Returns:
        Tuple of (handle -> user record, handles that did not resolve). The
        lookup key is lowercased, because X is case-insensitive on usernames and
        config/people.yaml is not consistently cased.

    Raises:
        SpendCeiling: If the lookup would breach the ceiling.
    """
    found: dict[str, dict] = {}
    missing: list[str] = []

    # 100 per request is the documented maximum.
    for start in range(0, len(handles), 100):
        chunk = handles[start:start + 100]
        spend.reserve_users(len(chunk))
        query = parse.urlencode({"usernames": ",".join(chunk),
                                 "user.fields": USER_FIELDS})
        body = _call(f"{base_url}/users/by?{query}", token)
        for user in body.get("data", []):
            found[user["username"].lower()] = user
        spend.bill_users(len(body.get("data", [])))
        # X reports unresolvable usernames in `errors`, not by omission alone.
        for problem in body.get("errors", []):
            name = problem.get("value")
            if name:
                missing.append(name)

    seen = {h.lower() for h in found}
    for handle in handles:
        if handle.lower() not in seen and handle not in missing:
            missing.append(handle)
    return found, missing


def user_posts(user_id: str, token: str, spend: Spend, *, max_results: int,
               start_time: datetime, exclude: list[str],
               base_url: str = "https://api.x.com/2") -> list[dict]:
    """Read one page of a user's posts. One page, never more.

    Args:
        user_id: Numeric id from :func:`users_by`.
        token: Bearer token.
        spend: Ceiling enforcer; `max_results` is reserved before the call
            because that is the most X can bill for it.
        max_results: 5..100. Both bounds are the API's, and the lower one is why
            the rate probe costs five posts a handle rather than one.
        start_time: Only posts at or after this instant. Applied server-side,
            so anything older is never billed.
        exclude: Server-side exclusions, e.g. ``["replies", "retweets"]``.
        base_url: API root.

    Returns:
        The posts returned, newest first. May be shorter than `max_results`,
        which for a quiet account is the finding rather than a failure.

    Raises:
        ValueError: If `max_results` is outside the API's 5..100 range.
        SpendCeiling: If the page would breach the ceiling.
    """
    if not 5 <= max_results <= 100:
        raise ValueError(f"max_results must be 5..100, got {max_results}")

    spend.reserve_posts(max_results)
    params = {
        "max_results": max_results,
        "start_time": start_time.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "tweet.fields": POST_FIELDS,
    }
    if exclude:
        params["exclude"] = ",".join(exclude)

    body = _call(f"{base_url}/users/{user_id}/tweets?{parse.urlencode(params)}",
                 token)
    posts = body.get("data", [])
    # Bill what came back, not what was reserved: a quiet account is cheap.
    spend.bill_posts(len(posts))
    return posts


def window_start(days: int, now: datetime | None = None) -> datetime:
    """The `start_time` for a rolling window.

    Args:
        days: Window length.
        now: Override for tests.

    Returns:
        A timezone-aware UTC datetime `days` before now.
    """
    return (now or datetime.now(timezone.utc)) - timedelta(days=days)
