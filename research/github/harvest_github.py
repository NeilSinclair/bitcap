"""Harvest 12 months of commit authorship from a GitHub organisation.

Uses the REST API to enumerate repositories and GraphQL to walk each
repository's default-branch history. GraphQL is used for the history because
one query returns the commit date and the resolved GitHub user in a single
round trip; the REST commits endpoint would need a second call per author.

Each repository's result is cached on disk so re-runs are idempotent and cost
no rate limit.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).parent.parent.parent
CACHE = ROOT / "research" / "docs" / "github_cache"
API = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"

HISTORY_QUERY = """
query($owner:String!, $name:String!, $since:GitTimestamp!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(since:$since, first:100, after:$cursor) {
            totalCount
            pageInfo { hasNextPage endCursor }
            nodes {
              committedDate
              author { name email user { login } }
            }
          }
        }
      }
    }
  }
}
"""


def load_token() -> str:
    """Read GITHUB_TOKEN from the environment or the repo-root .env.

    Returns:
        The token string.

    Raises:
        SystemExit: If no token is available.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("GITHUB_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip("'\"")
    if not token:
        sys.exit("GITHUB_TOKEN not set (environment or .env)")
    return token


def _call(url: str, token: str, data: dict | None = None, retries: int = 4):
    """Issue an authenticated API call with backoff on transient failure.

    Args:
        url: Absolute endpoint URL.
        token: GitHub personal access token.
        data: JSON body; when present the call is a POST.
        retries: Attempts before giving up.

    Returns:
        Tuple of (parsed JSON body, response headers).

    Raises:
        RuntimeError: If every attempt fails.
    """
    body = json.dumps(data).encode() if data is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "bitcap-research",
    }
    if body:
        headers["Content-Type"] = "application/json"

    for attempt in range(retries):
        req = request.Request(url, data=body, headers=headers)
        try:
            with request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read()), dict(resp.headers)
        except error.HTTPError as exc:
            # 403/429 here are rate limiting, not authorisation failures.
            if exc.code in (403, 429) and attempt < retries - 1:
                reset = exc.headers.get("x-ratelimit-reset")
                wait = 60.0
                if reset:
                    wait = max(5.0, float(reset) - time.time() + 5)
                print(f"    rate limited, sleeping {wait:.0f}s", flush=True)
                time.sleep(min(wait, 900))
                continue
            if exc.code >= 500 and attempt < retries - 1:
                time.sleep(2**attempt)
                continue
            raise
        except (error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)
    raise RuntimeError(f"failed after {retries} attempts: {url}")


def repos(org: str, token: str, since: datetime) -> list[dict]:
    """List an organisation's own repositories pushed to since a cutoff.

    Forks and archived repositories are excluded: neither reflects work done
    by the organisation's staff during the window.

    Args:
        org: GitHub organisation login.
        token: GitHub personal access token.
        since: Only repositories pushed at or after this instant are returned.

    Returns:
        Repository dicts as returned by the REST API, newest push first.
    """
    out, page = [], 1
    while True:
        url = f"{API}/orgs/{org}/repos?per_page=100&sort=pushed&page={page}"
        batch, _ = _call(url, token)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    cutoff = since.isoformat()
    return [
        r
        for r in out
        if not r["fork"] and not r["archived"] and r["pushed_at"] >= cutoff
    ]


def history(owner: str, name: str, token: str, since: datetime) -> dict:
    """Walk one repository's default-branch commit history since a cutoff.

    Args:
        owner: Organisation login.
        name: Repository name.
        token: GitHub personal access token.
        since: Earliest commit date to include.

    Returns:
        Dict with 'total' (commits in window) and 'commits' (list of
        {date, login, name, email}). An empty repository yields zero commits.
    """
    commits, cursor, total = [], None, 0
    while True:
        payload = {
            "query": HISTORY_QUERY,
            "variables": {
                "owner": owner,
                "name": name,
                "since": since.isoformat().replace("+00:00", "Z"),
                "cursor": cursor,
            },
        }
        body, _ = _call(GRAPHQL, token, payload)
        if "errors" in body:
            raise RuntimeError(f"{owner}/{name}: {body['errors']}")

        ref = body["data"]["repository"]["defaultBranchRef"]
        if not ref:  # empty repo, no default branch
            return {"total": 0, "commits": []}

        hist = ref["target"]["history"]
        total = hist["totalCount"]
        for node in hist["nodes"]:
            author = node["author"] or {}
            user = author.get("user") or {}
            commits.append(
                {
                    "date": node["committedDate"],
                    "login": user.get("login"),
                    "name": author.get("name"),
                    "email": author.get("email"),
                }
            )
        if not hist["pageInfo"]["hasNextPage"]:
            break
        cursor = hist["pageInfo"]["endCursor"]

    return {"total": total, "commits": commits}


def collect(org: str, months: int = 12) -> dict:
    """Harvest every in-window repository for an organisation.

    Results are cached per repository under research/docs/github_cache, so a
    re-run only fetches repositories not seen before.

    Args:
        org: GitHub organisation login.
        months: Size of the lookback window.

    Returns:
        Dict keyed by repository name, each holding its history result plus
        the repository's star count and description.
    """
    token = load_token()
    since = datetime.now(timezone.utc) - timedelta(days=months * 30)
    CACHE.mkdir(parents=True, exist_ok=True)

    found = repos(org, token, since)
    print(f"{org}: {len(found)} repos pushed in the last {months} months")

    out = {}
    for i, repo in enumerate(found, 1):
        name = repo["name"]
        cached = CACHE / f"{org}__{name}.json"
        if cached.exists():
            out[name] = json.loads(cached.read_text())
            print(f"  [{i}/{len(found)}] {name}: cached")
            continue

        result = history(org, name, token, since)
        result["stars"] = repo["stargazers_count"]
        result["description"] = repo["description"]
        cached.write_text(json.dumps(result))
        out[name] = result
        print(f"  [{i}/{len(found)}] {name}: {result['total']} commits", flush=True)

    return out


if __name__ == "__main__":
    org = sys.argv[1] if len(sys.argv) > 1 else "anthropics"
    data = collect(org)
    dest = ROOT / "research" / "docs" / f"github_commits_{org}.json"
    dest.write_text(json.dumps(data, indent=2))
    total = sum(r["total"] for r in data.values())
    print(f"\n{total} commits across {len(data)} repos -> {dest.name}")
