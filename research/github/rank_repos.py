"""Rank the labs' public repositories, so the releases leg knows what to watch.

THE RANKING IS STARS. There is no second sort key and no blended score.

Commit volume was the first instinct and the data rejected it. xAI's three most
newsworthy repositories are its three *least* committed, because they are code
dumps -- published artifacts pushed by CI rather than developed in the open.
`x-algorithm` (the For You feed algorithm, 32,610 stars) carries 19 commits, all
from a CI account; `grok-prompts` (Grok's system prompts) carries 2. A commit
ranking puts `xai-sdk-python` (565 stars, routine SDK maintenance) above all
three. Commits survive here only as displayed evidence -- is this repository
alive or is it an archive -- and must never re-enter the sort;
`tests/test_rank_repos.py::TestStarsAreTheOnlyRanking` pins that.

The known weakness of a raw star ranking is that it is a hall of fame rather
than a signal: 40% of the top 100 have no commit in 90 days, and the list would
be identical next month. `new_within_days` addresses it without reintroducing
commits, splitting new from established on the repository's real `created_at`.
That date has to be *fetched*, never inferred: "no commit before X" means only
"dormant until X", so first-commit-in-window reports `openai/whisper` (2022) and
`openai/CLIP` (2020) as created in 2026. The error is not noise, it lands on
precisely the famous quiet repositories stars float to the top.

Input is `raw_github_repos.payload` -- the bronze layer the github leg already
maintains, which carries the star count, the description, `created_at` and the
commits, all refreshed from the org listing on every run. Nothing here reads a
file or the network, so the ranking is a pure function of what is in the
database (D31: the deployed container has no disk).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from aggregate_github import MIRROR_MARKER, is_bot

ROOT = Path(__file__).parent.parent.parent
CONFIG = ROOT / "config" / "repo_signals.yaml"


def load_config(path: Path = CONFIG) -> dict:
    """Read config/repo_signals.yaml."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def parse(stamp: str) -> datetime:
    """Parse a GitHub ISO-8601 timestamp into an aware datetime."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def is_mirror(description: str | None) -> bool:
    """Report whether a description declares the repository a mirror.

    Uses `aggregate_github.MIRROR_MARKER` rather than a second copy of the
    pattern, so the people register and this ranking exclude the same
    repositories. GitHub's own `fork` flag does not catch these -- a declared
    read-only mirror is not a fork.

    Args:
        description: The repository description, possibly None.

    Returns:
        True if the description declares a mirror.
    """
    return bool(description and MIRROR_MARKER.search(description))


def activity(commits: list[dict], since: datetime) -> dict:
    """Summarise commit activity in the window, as displayed evidence only.

    A null login is counted as *unattributed*, not human. `is_bot` keys on the
    GitHub login, and xAI's CI commits carry `login: null` with
    `email: support@x.ai` -- every one of `x-algorithm`'s 19 commits has that
    shape. Counting them as human reports an automated code dump as a team at
    work.

    Args:
        commits: Commit records {date, login, name, email}.
        since: Start of the window.

    Returns:
        Counts plus the most recent commit date in the window.
    """
    recent = [c for c in commits if (c.get("date") or "") and parse(c["date"]) >= since]
    human, bot, unattributed = [], [], []
    for c in recent:
        login = c.get("login")
        if not login:
            unattributed.append(c)
        elif is_bot(login):
            bot.append(c)
        else:
            human.append(c)
    return {
        "commits": len(recent),
        "human": len(human),
        "bot": len(bot),
        "unattributed": len(unattributed),
        "authors": len({c["login"] for c in human}),
        "last_commit": max((c["date"] for c in recent), default=None),
    }


def row(org: str, name: str, payload: dict, cfg: dict, now: datetime) -> dict:
    """Build one ranked row from a repository's bronze payload.

    Args:
        org: GitHub organisation login.
        name: Repository name.
        payload: `raw_github_repos.payload` -- history plus the listing fields.
        cfg: Parsed config/repo_signals.yaml.
        now: The instant the ranking is computed against.

    Returns:
        A row carrying `stars` (the sort key), the new/established `age` cut,
        and the activity evidence.
    """
    act = activity(payload.get("commits", []),
                   now - timedelta(days=cfg["window_days"]))

    created = payload.get("created_at")
    if not created:
        # A repository last walked before the listing fields were captured has
        # no `created_at` until its next push. "unknown" is the honest answer;
        # guessing from the first commit would mark every dormant famous
        # repository as new, which is the failure this cut exists to avoid.
        age = "unknown"
    elif parse(created) >= now - timedelta(days=cfg["new_within_days"]):
        age = "new"
    else:
        age = "established"

    return {
        "org": org,
        "repo": name,
        "url": f"https://github.com/{org}/{name}",
        "stars": payload.get("stars", 0),
        "description": payload.get("description"),
        "age": age,
        "created_at": created,
        "language": payload.get("language"),
        "topics": payload.get("topics") or [],
        "archived": bool(payload.get("archived")),
        "commits_window": act["commits"],
        "human_window": act["human"],
        "bot_window": act["bot"],
        "unattributed_window": act["unattributed"],
        "authors_window": act["authors"],
        "last_commit": act["last_commit"],
        "commits_year": payload.get("total", len(payload.get("commits", []))),
    }


def rank(repos: list[tuple[str, str, dict]], cfg: dict,
         now: datetime | None = None) -> list[dict]:
    """Rank repositories by stars.

    Stars are the only sort key. Ties break on repository name so the order is
    stable across runs; nothing about commit volume enters the comparison.

    Args:
        repos: `(org, repo, payload)` triples, typically from
            `raw_github_repos`.
        cfg: Parsed config/repo_signals.yaml.
        now: The instant to rank against; defaults to now.

    Returns:
        Rows sorted by stars descending, mirrors excluded.
    """
    now = now or datetime.now(timezone.utc)
    rows = []
    for org, name, payload in repos:
        if is_mirror(payload.get("description")):
            continue
        r = row(org, name, payload, cfg, now)
        if r["stars"] >= cfg.get("min_stars", 0):
            rows.append(r)
    rows.sort(key=lambda r: (-r["stars"], r["repo"]))
    return rows
