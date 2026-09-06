"""Build the watched-repository population and label it, so the relevance
filter has something to be measured against.

The releases leg watches repositories chosen by star rank, and a large share of
them are not LLM work: `mujoco` and its five variants, `alphafold3`, `torax`
(fusion), `weathernext` (climate), `detectron2`, `habitat`. They are correctly
scored — `alphafold3` averages 35.6 on the AI axis, third of all 87 — which is
exactly why no score threshold removes them. Relevance is a different axis, and
this file builds the set the filter is graded on.

Two cohorts, kept apart deliberately:

* **watched** — the repositories that have actually produced releases. This is
  the population the filter changes today.
* **frontier** — repositories inside the top 30 of the ranking that have not
  produced a release. The gate sits *inside* the star ranking, so dropping
  `mujoco` promotes whatever is next. Those promotions are the filter's new work
  and none of them appear in the watched cohort, so measuring only the watched
  set would report accuracy on a population the filter no longer sees. Reported
  as its own line, never folded into the headline.

Fields come from the **live org listing**, not from bronze, because that is what
`adapters._listing_fields` overlays at rank time and therefore what the filter
sees in production. Bronze is missing a description for 13 of the 87 — including
`weathernext` and `rlax` — so building from bronze would grade the model on less
information than it actually gets.

Usage::

    python research/github/label_repos.py build    # write the population
    python research/github/label_repos.py label    # Fable 5 labels it, blind
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"
POPULATION = DOCS / "repo_population.json"
LABELS = DOCS / "repo_relevance_labels.json"
COST = DOCS / "announcement_cost.json"
PROMPT = ROOT / "prompts" / "repo_relevance" / "label_v1.md"
GITHUB_SOURCES = ROOT / "config" / "github_sources.yaml"

for _leg in ("announcements", "github"):
    _path = str(ROOT / "research" / _leg)
    if _path not in sys.path:
        sys.path.insert(0, _path)

# How far below the watched cut to sample. `releases_watch` is 10, so ranks
# 11-30 are the repositories a freed slot actually reaches.
FRONTIER_TO_RANK = 30

LABELLER = "claude-fable-5"

# The labeller is not a bake-off candidate, for the reason config/dedupe.yaml
# records about its adjudicator: an eval whose labels come from one of the
# models being graded is marking its own homework.
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label": {"type": "string", "enum": ["relevant", "off_topic"]},
        "category": {
            "type": "string",
            "enum": [
                "model", "training_or_serving", "agent_or_tooling", "eval_or_safety",
                "vendor_sdk", "ml_infrastructure",
                "other_ai_research", "non_ai",
            ],
        },
        "reason": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["label", "category", "reason", "confidence"],
}


def _record_cost(cost: dict, workflow: str) -> None:
    """Append one cost record to the shared ledger.

    Mirrors `dedupe._record_cost`. Cost is recorded at the call site because it
    cannot be reconstructed afterwards.

    Args:
        cost: Cost record from `providers._cost`.
        workflow: Which workflow to attribute the spend to.
    """
    log = json.loads(COST.read_text(encoding="utf-8")) if COST.exists() else []
    log.append({**cost, "workflow": workflow})
    COST.write_text(json.dumps(log, indent=2), encoding="utf-8")


def population_rows() -> list[dict]:
    """Build both cohorts from the live listings and the database.

    Returns:
        Records carrying the fields the filter sees in production, plus
        `cohort`, `rank` and `releases`.

    Raises:
        RuntimeError: If an org's listing comes back empty while bronze holds
            repositories for it — the same distinction `fetch_releases` draws
            between a failed API call and an org that ships nothing.
    """
    import harvest_github
    import providers
    import rank_repos
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app import models as m
    from app.db import get_engine
    from app.pipeline.adapters import _listing_fields

    # DATABASE_URL lives in .env; without this `get_engine` silently falls back
    # to a local sqlite file that has none of these tables.
    providers.load_env()
    cfg = rank_repos.load_config()
    orgs = yaml.safe_load(GITHUB_SOURCES.read_text(encoding="utf-8"))["orgs"]
    token = harvest_github.load_token()

    with Session(get_engine()) as session:
        bronze = session.scalars(select(m.RawGithubRepo)).all()
        releases = session.scalars(
            select(m.RawArticle.payload).where(
                m.RawArticle.source_file == "github_releases")
        ).all()

    watched: dict[tuple[str, str], int] = {}
    for payload in releases:
        key = (payload.get("org"), payload.get("repo"))
        watched[key] = watched.get(key, 0) + 1

    rows: list[dict] = []
    for org, entry in orgs.items():
        org_bronze = [r for r in bronze if r.org == org]
        since = datetime.now(timezone.utc) - timedelta(days=entry.get("months", 12) * 30)
        listing = {r["name"]: r for r in harvest_github.repos(org, token, since)}
        if not listing and org_bronze:
            raise RuntimeError(
                f"{org}: the listing came back empty while bronze holds "
                f"{len(org_bronze)} repositories — a failed call, not a quiet org"
            )
        ranked = rank_repos.rank(
            [(r.org, r.repo, _listing_fields(r.payload, listing.get(r.repo, {})))
             for r in org_bronze if r.repo in listing],
            cfg,
        )
        for index, ranked_row in enumerate(ranked, start=1):
            key = (org, ranked_row["repo"])
            is_watched = key in watched
            if not is_watched and index > FRONTIER_TO_RANK:
                continue
            entry_listing = listing[ranked_row["repo"]]
            rows.append({
                "org": org,
                "repo": ranked_row["repo"],
                "stars": ranked_row["stars"],
                "description": ranked_row["description"],
                "language": entry_listing.get("language"),
                "topics": entry_listing.get("topics") or [],
                "rank": index,
                "releases": watched.get(key, 0),
                "cohort": "watched" if is_watched else "frontier",
            })
    return rows


def build() -> None:
    """Write the population file."""
    rows = population_rows()
    POPULATION.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    watched = [r for r in rows if r["cohort"] == "watched"]
    frontier = [r for r in rows if r["cohort"] == "frontier"]
    missing = [r for r in rows if not (r["description"] or "").strip()]
    print(f"wrote {POPULATION.relative_to(ROOT)} — {len(rows)} repositories")
    print(f"  watched  {len(watched):>3}  ({sum(r['releases'] for r in watched)} releases)")
    print(f"  frontier {len(frontier):>3}  (in the ranking, no release yet)")
    print(f"  without a description: {len(missing)}")


def build_user(row: dict) -> str:
    """Render one repository as the user message.

    Delegates to `repo_relevance.describe`, the renderer production uses, rather
    than keeping a second copy. `bakeoff_repos.py` already does this and states
    why: a separate copy drifts, and the eval then quietly stops describing the
    system. That argument applies with more force here, because this is what
    produces the *ground truth* every bake-off figure is scored against — a
    field added to `describe` and not to a hand copy would mean the labels were
    made from a different question than the one being graded, with no test
    failing and both files still running.

    Args:
        row: A population record.

    Returns:
        The user message.
    """
    from app.pipeline import repo_relevance

    return repo_relevance.describe(row)


def label(limit: int | None = None) -> None:
    """Label every repository in the population with Fable 5, blind.

    Resumable: already-labelled repositories are carried through untouched, so
    an interrupted run costs nothing to finish and `--limit` can be used to
    prove the path on a handful before paying for all of them.

    Args:
        limit: Stop after labelling this many new repositories.
    """
    import providers
    from score_announcements import strip_comments

    providers.load_env()
    rows = json.loads(POPULATION.read_text(encoding="utf-8"))
    done = {}
    if LABELS.exists():
        # Keyed on the record's own `repo` field, which is already "org/name".
        done = {r["repo"]: r for r in json.loads(LABELS.read_text(encoding="utf-8"))}

    system = strip_comments(PROMPT.read_text(encoding="utf-8"))
    out, spend, fresh = [], 0.0, 0
    for row in rows:
        key = f"{row['org']}/{row['repo']}"
        if key in done:
            out.append(done[key])
            continue
        if limit is not None and fresh >= limit:
            continue
        fresh += 1
        started = time.time()
        result, cost = providers.classify(
            "anthropic", LABELLER, system, build_user(row), SCHEMA, f"repo:{key}")
        _record_cost(cost, "repo_relevance_labels")
        spend += cost["usd"]
        out.append({
            "repo": key,
            "cohort": row["cohort"],
            "label": result["label"],
            "category": result["category"],
            "reason": result["reason"],
            "confidence": result["confidence"],
            "labeller": LABELLER,
            "prompt_version": PROMPT.stem,
        })
        print(f"  {result['label']:10s} {result['category']:20s} {key}"
              f"  ({time.time() - started:.1f}s)")
        LABELS.write_text(json.dumps(out, indent=2), encoding="utf-8")

    LABELS.write_text(json.dumps(out, indent=2), encoding="utf-8")
    relevant = [r for r in out if r["label"] == "relevant"]
    print(f"\nwrote {LABELS.relative_to(ROOT)} — {len(out)} labelled, ${spend:.4f} this run")
    print(f"  relevant   {len(relevant)}")
    print(f"  off_topic  {len(out) - len(relevant)}")
    for cohort in ("watched", "frontier"):
        part = [r for r in out if r["cohort"] == cohort]
        keep = [r for r in part if r["label"] == "relevant"]
        print(f"  {cohort:9s} {len(keep)}/{len(part)} relevant")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["build", "label"])
    parser.add_argument("--limit", type=int, default=None,
                        help="label at most this many new repositories")
    args = parser.parse_args()
    build() if args.mode == "build" else label(args.limit)


if __name__ == "__main__":
    main()
