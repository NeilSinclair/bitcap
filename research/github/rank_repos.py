"""Rank the labs' public repositories, so the releases leg knows what to watch.

THE RANKING IS STARS. There is no second sort key and no blended score.

Commit volume was the first instinct and the data rejected it. xAI's three most
newsworthy repositories are its three *least* committed, because they are code
dumps -- published artifacts pushed by CI rather than developed in the open.
`x-algorithm` (the For You feed algorithm, 32,610 stars) carries 19 commits, all
from a CI account; `grok-prompts` (Grok's system prompts) carries 2. A commit
ranking puts `xai-sdk-python` (565 stars, routine SDK maintenance) above all
three. Nothing about commits enters the sort;
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

from aggregate_github import MIRROR_MARKER

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



def row(org: str, name: str, payload: dict, cfg: dict, now: datetime) -> dict:
    """Build one ranked row from a repository's bronze payload.

    Deliberately thin. An earlier version carried the activity evidence too --
    commits, human commits, bot commits, distinct authors, last commit -- so a
    reader could see whether a repository was alive or an archive. There is no
    reader on this branch, and computing twelve fields for one consumer is the
    kind of weight that reads as finished work while being untested and unseen.
    The evidence columns come back with the page that shows them.

    Args:
        org: GitHub organisation login.
        name: Repository name.
        payload: `raw_github_repos.payload` with the live listing overlaid.
        cfg: Parsed config/repo_signals.yaml.
        now: The instant the ranking is computed against.

    Returns:
        A row carrying `stars` (the sort key) and the new/established `age`
        cut.
    """
    created = payload.get("created_at")
    if not created:
        # A repository absent from the live listing overlay has no date. Saying
        # so is honest; guessing from the first commit in the window would mark
        # every dormant famous repository as new, which is the failure this cut
        # exists to avoid.
        age = "unknown"
    elif parse(created) >= now - timedelta(days=cfg["new_within_days"]):
        age = "new"
    else:
        age = "established"

    return {
        "org": org,
        "repo": name,
        "stars": payload.get("stars", 0),
        "description": payload.get("description"),
        "age": age,
        "created_at": created,
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


def shortlist(rows: list[dict], keep, limit: int, stop=None) -> list[dict]:
    """Walk the ranking and take the first `limit` rows `keep` accepts.

    Separate from `rank` because ranking is a sort and this is a take. Keeping
    them apart is what lets `rank` stay the pure function this module's
    docstring promises: `keep` is injected, so the relevance gate can consult a
    model and a session while nothing here knows that. It also means this walk
    is tested with a lambda, and `TestStarsAreTheOnlyRanking` never has to learn
    that a gate exists.

    Order is never changed. A rejected repository frees its slot to the next one
    down the ranking rather than shrinking the list -- that is the whole point
    of gating here rather than after the caller's slice, because dropping a
    physics simulator should promote whatever real repository sits behind it.

    Laziness is not an optimisation, it is the cost model. `keep` is called only
    as far down as needed to fill `limit`; judging a whole listing to choose ten
    of it would be 395 calls for facebookresearch alone, every firing.

    `stop` bounds the walk. Laziness bounds the *typical* case, but an org whose
    ranking is mostly off-topic has no natural stopping point, and without a
    ceiling one firing walks the entire listing. It is a callable rather than a
    count because what needs bounding is *what the walk spends*, not how far it
    looks: the caller's predicate answers most rows from a cache, and counting
    those against the ceiling stopped the walk 30 rows down for ever even when
    going deeper was free. That left `facebookresearch` watching three
    repositories rather than ten, permanently, with no way to recover.
    Stopping is a legitimate finding about an org, not a failure -- the caller
    reports it rather than raising.

    Args:
        rows: Ranked rows from `rank`, in ranking order.
        keep: Predicate called with one row; truthy keeps it.
        limit: How many rows to take.
        stop: Optional zero-argument callable checked before each row; a truthy
            answer ends the walk. Omitted means walk until `limit` is filled.

    Returns:
        At most `limit` rows, in ranking order.
    """
    picked = []
    for r in rows:
        if len(picked) >= limit or (stop is not None and stop()):
            break
        if keep(r):
            picked.append(r)
    return picked
