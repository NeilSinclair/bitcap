"""One calling convention over three legs that do not share one.

The harvesters under `research/` were each written against the lab in front of
them, and it shows: announcements dispatches on a method name, the six papers
harvesters take three different argument lists and return two different shapes,
and the GitHub pair has no `main()` at all and reads its org from `sys.argv[1]`.
None of that is wrong — it is what those scripts needed — but an orchestrator
cannot loop over it.

This module is the only place that knows about those differences. Every adapter
takes a :class:`~app.pipeline.registry.Source` and returns a
:class:`FetchResult`, and nothing under `research/` is modified to make that
true. Adding a lab remains a config change; adding a genuinely new *kind* of
source is a new adapter here.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app import models as m
from app.cli import PROMPT_VERSION

ROOT = Path(__file__).parent.parent.parent
RESEARCH = ROOT / "research"
DOCS = RESEARCH / "docs"

# The harvesters import each other by bare module name (`from llm_byline import
# ...`), which is how they are run — as scripts, from their own directory. The
# tests already do this; see any tests/test_*_harvest.py.
for _leg in ("announcements", "papers", "github"):
    _path = str(RESEARCH / _leg)
    if _path not in sys.path:
        sys.path.insert(0, _path)


@dataclass
class FetchResult:
    """What one source produced on one attempt.

    Attributes:
        items: Normalised records, ready to persist. Shape is per-leg but always
            carries a resolvable `url` — no citation, no item.
        watermark: Leg-specific high-water mark to carry into the next run.
        unresolved: Things this source found but could not resolve, each with a
            `reason`. Never dropped: an unresolvable paper is a recorded gap,
            not an absence (docs/handover.md §5).
        cost_usd: LLM spend attributable to this source, if any.
        raw_repos: GitHub only — repository histories fetched on this run, for
            `raw_github_repos`. `items` for that leg is the *aggregate* (one
            record per person), which cannot be re-derived from itself; this
            carries the commits it was computed from so the next run does not
            have to fetch them again (D32).
    """

    items: list[dict] = field(default_factory=list)
    watermark: dict = field(default_factory=dict)
    unresolved: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    raw_repos: list[dict] = field(default_factory=list)


def _cutoff(window_months: float, now: datetime | None = None) -> datetime:
    """The same window arithmetic fetch_announcements.collect() does."""
    return (now or datetime.now(timezone.utc)) - timedelta(days=window_months * 30.5)


# How far back an incremental fetch always looks, however recently the last one
# succeeded. A source that ran an hour ago is still re-checked over two days:
# labs backdate posts and correct dates after publishing, and a window pulled
# tight to the last run would miss anything that appeared out of order.
INCREMENTAL_FLOOR_HOURS = 48

# The papers leg's full window. Was inline in `fetch_papers`; named here because
# it is now the ceiling on an incremental window rather than the only value.
PAPERS_WINDOW_MONTHS = 3


def _window_start(state, window_months: float, now: datetime | None = None) -> datetime:
    """How far back this source fetches on this run.

    A source that has never succeeded fetches its whole configured window. That
    is the first firing against an empty database, and it is also a newly-added
    lab backfilling itself without dragging the six already loaded through a
    re-harvest they do not need.

    Every run after that fetches *everything since the last success, and never
    less than* :data:`INCREMENTAL_FLOOR_HOURS`. Anchoring to the last success
    rather than to a fixed 48 hours is what makes a missed night self-healing:
    three firings lost to an outage means the next one reaches back three days,
    instead of quietly skipping whatever was published in between. It never
    reaches back further than the configured window, which is the age past which
    the corpus does not want the article anyway.

    `last_success_at` stands in for "is there anything in bronze for this
    source". They are the same fact in every path that exists today — a run that
    persisted articles is a run that succeeded — and this is the one the
    orchestrator already hands to the adapter, so it needs no session here.

    Args:
        state: The source's persistent state, or None when it has none.
        window_months: The source's full configured window.
        now: Injectable clock for the tests.

    Returns:
        Earliest publication date this run should fetch.
    """
    now = now or datetime.now(timezone.utc)
    full = _cutoff(window_months, now)

    last_success = getattr(state, "last_success_at", None)
    if last_success is None:
        return full

    # Stored naive under sqlite and aware under Postgres; compare in UTC either way.
    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=timezone.utc)

    return max(full, min(last_success, now - timedelta(hours=INCREMENTAL_FLOOR_HOURS)))


def _window_months(state, now: datetime | None = None) -> int:
    """:func:`_window_start` expressed in whole months, for the papers leg.

    Whole months, and never fewer than one, because the harvesters do not all
    take a fractional window. Three of them compute `timedelta(days=months *
    30.5)` and would take any float, but `harvest_contributors` does calendar
    arithmetic — `today.month - months`, formatted with `{y:04d}` — which raises
    on a float and cannot express a sub-month window at all.

    So the papers leg gains a third of its fetches back rather than all of them.
    That is the right trade at cadence 3: a one-month floor cannot miss a paper,
    and buying the rest would mean rewriting six harvesters' notion of a window.

    Args:
        state: The source's persistent state, or None.
        now: Injectable clock for the tests.

    Returns:
        Window length in whole months, between 1 and :data:`PAPERS_WINDOW_MONTHS`.
    """
    import math

    now = now or datetime.now(timezone.utc)
    days = (now - _window_start(state, PAPERS_WINDOW_MONTHS, now)).total_seconds() / 86400
    return max(1, min(PAPERS_WINDOW_MONTHS, math.ceil(days / 30.5)))


def _settled_urls(session, prompt_version: str = PROMPT_VERSION) -> set[str]:
    """Article URLs there is no longer any reason to download.

    Cheaper than any cache, because the request is never made: a page already
    stored in `raw_articles` yields nothing new by being fetched a second time,
    and its text is already in the database for everything downstream.

    **Stored is not enough on its own — it has to be classified too.** An
    article that is stored but still pending (a run that hit its budget ceiling,
    say) has to be refetched, because `classify_new` reads article *text* from
    the corpus file rather than from the database, and on a container with no
    disk that file resets to the image copy every firing. Skipping it here would
    leave it pending for ever with nothing able to classify it, which is
    planning.md §13.3 turned from an edge case into the normal path.

    Args:
        session: Open session, or None when there is no database to ask.
        prompt_version: Classifier version that counts as classified.

    Returns:
        URLs to leave alone. Empty without a session, which refetches
        everything — correct for a manual sweep, and what the tests assume.
    """
    if session is None:
        return set()
    classified = select(m.RawLlmResponse.url).where(
        m.RawLlmResponse.prompt_version == prompt_version
    )
    return set(session.scalars(
        select(m.RawArticle.url).where(m.RawArticle.url.in_(classified))
    ))


def _cost_log_total(lab: str) -> float:
    """Running total of a papers harvester's own per-call cost log.

    Each papers harvester records cost at the call site into its own
    `research/docs/<lab>_llm_cost.json`, but those records carry **no
    timestamp**, so they cannot key into `raw_costs` (unique on url+at) without
    inventing one. Reading the total before and after a harvest gives this run's
    spend exactly, with nothing fabricated — which is what puts the papers leg
    under the budget ceiling and into `run_sources.cost_usd` instead of being
    invisible to both.
    """
    import json

    total = 0.0
    for name in (f"{lab}_llm_cost.json", "byline_llm_cost.json", "fallback_cost.json"):
        path = DOCS / name
        if not path.exists():
            continue
        try:
            total += sum(float(r.get("usd", 0.0)) for r in json.loads(path.read_text()))
        except (ValueError, TypeError):  # a truncated log must not fail the leg
            continue
    return total


def fetch_announcements(source, state=None, session=None) -> FetchResult:
    """Fetch one lab's in-window announcements.

    Calls the lab's discovery methods directly rather than `collect()`, which
    loops over every lab with no error handling between them — one
    Cloudflare-blocked lab would abort the other six. A lab may declare more
    than one channel (`also` in sources.yaml); all of them run here.

    Args:
        source: An announcements source; its config is the lab's sources.yaml
            entry plus `window_months`.
        session: Open session, used to skip articles already stored and
            classified (see :func:`_settled_urls`). Without one every in-window
            article is downloaded.
        state: The source's persistent state; its `last_success_at` bounds the
            window (see :func:`_window_start`). This used to be unused, on the
            reasoning that a full re-derive was nearly free because every page
            was cached on disk. The deployed shape has no disk — Render cron
            jobs cannot mount one — so a full re-derive is 500-odd live fetches
            every night to discover the two articles that are actually new.

    Returns:
        Articles, with the lab's newest publication date as the watermark.

    Raises:
        ValueError: On a method name the fetcher does not implement. The
            fetcher's own `collect()` calls `sys.exit` here, which would kill
            the whole run rather than one source.
    """
    import fetch_announcements as fa

    lab = source.config
    since = _window_start(state, lab["window_months"])
    settled = _settled_urls(session)

    # Each channel is isolated. Channels exist to make a lab *more* available,
    # and an unguarded loop did the opposite: a second channel raising — which
    # `from_model_index` and `from_discourse` both do by design on a page-shape
    # change — discarded the primary channel's already-fetched articles and
    # marked the whole lab FAILED. One dead channel must cost only that
    # channel's items.
    #
    # A failure is recorded as an unresolved item rather than swallowed: a gap
    # this pipeline knows about is never dropped, and a channel that is quietly
    # dead is the failure mode this whole change exists to close. If every
    # channel fails the source really is down, so the last error propagates.
    items, unresolved, errors = [], [], []
    channels = fa.channels(lab)
    for channel in channels:
        method = fa.METHODS.get(channel["method"])
        if method is None:
            raise ValueError(
                f"{source}: unknown discovery method {channel['method']!r}"
            )
        try:
            items.extend(method(channel, since, settled))
        except Exception as exc:
            errors.append(exc)
            unresolved.append({
                "kind": "channel",
                "name": f"{lab['id']}:{channel['method']}",
                "reason": f"discovery channel failed: {exc}",
            })
    if errors and len(errors) == len(channels):
        raise errors[-1]
    newest = max((a["date"] for a in items), default=None)
    return FetchResult(
        items=items,
        watermark={"max_published": newest} if newest else {},
        unresolved=unresolved,
    )


def fetch_papers(source, state=None, session=None) -> FetchResult:
    """Harvest one lab's recent papers and their bylines.

    Absorbs three differences between the six harvesters: which arguments the
    entry function accepts (declared as `args` in papers_sources.yaml), whether
    it returns a bare list or a `(papers, unresolved)` pair (`returns`), and
    which attribute carries the primary citation (`url_field`).

    That last one matters and is not cosmetic. Meta's citation is `meta_url` and
    Mistral's is `announcement_url` — the lab's own page, which always resolves
    — while `source_url` is the arXiv page the byline happened to be scraped
    from. Citing the latter would be citing our own plumbing.

    Args:
        source: A papers source; its config is the papers_sources.yaml entry.
        session: Unused; accepted so every adapter has one signature.
        state: The source's persistent state; bounds the window in whole
            months (see :func:`_window_months`). Only the harvesters that
            declare `months` in `args` can be narrowed; OpenAI's and DeepSeek's
            take no window argument and still harvest everything they find.

    Returns:
        Papers with a normalised `url`, plus any unresolved candidates.

    Raises:
        RuntimeError: If called for a lab marked `enabled: false`, which is a
            registry bug — the orchestrator skips those before getting here.
    """
    spec = source.config
    if not spec.get("enabled", True) or not spec.get("module"):
        raise RuntimeError(f"{source}: no papers harvester (see papers_sources.yaml)")

    module = importlib.import_module(spec["module"])
    entry = getattr(module, spec["entry"])

    accepted = set(spec.get("args") or ())
    available = {
        "months": _window_months(state),
        "model": "claude-sonnet-5",
        "limit": None,
    }
    kwargs = {k: v for k, v in available.items() if k in accepted}

    before = _cost_log_total(source.id)
    produced = entry(**kwargs)
    spent = max(0.0, _cost_log_total(source.id) - before)

    if spec.get("returns") == "papers_and_unresolved":
        papers, unresolved = produced
    else:
        papers, unresolved = produced, []

    url_field = spec["url_field"]
    items = []
    for paper in papers:
        raw = paper if isinstance(paper, dict) else vars(paper)
        items.append({
            "lab": source.id,
            "url": raw.get(url_field),
            "title": raw.get("title"),
            "date": raw.get("date"),
            "authors": raw.get("authors", []),
            # Per-lab idiosyncrasy (arXiv resolution confidence, author-order
            # semantics, departure asterisks) stays in one blob rather than
            # growing a column per lab — the raw-JSONB pattern the research
            # scripts already use (docs/handover.md §5).
            "raw": raw,
        })

    newest = max((i["date"] for i in items if i.get("date")), default=None)
    return FetchResult(
        items=items,
        watermark={"max_published": newest} if newest else {},
        unresolved=[{**u, "lab": source.id} for u in unresolved],
        cost_usd=round(spent, 6),
    )


def _stored_repos(session, org: str) -> dict:
    """This org's repository histories already in bronze, keyed by repo name."""
    if session is None:
        return {}
    rows = session.scalars(select(m.RawGithubRepo).where(m.RawGithubRepo.org == org))
    return {row.repo: row for row in rows}


def _in_window(payload: dict, since: datetime) -> dict:
    """A stored history trimmed to the current window.

    A repository is only re-walked when its `pushed_at` moves, so a quiet
    repository's stored commits were fetched against an older, wider `since` and
    still carry commits that have since aged out. Counting them would let the
    12-month register drift into an all-time one, one night at a time.

    Both sides are ISO-8601 in UTC with a `Z` suffix, which orders correctly as
    text — the same comparison `harvest_github.repos` makes against `pushed_at`.
    """
    cutoff = since.isoformat().replace("+00:00", "Z")
    commits = [c for c in payload.get("commits", []) if (c.get("date") or "") >= cutoff]
    return {**payload, "commits": commits, "total": len(commits)}


def fetch_github(source, state=None, session=None) -> FetchResult:
    """Harvest one org's commits and aggregate them into a people register.

    Harvest and aggregate run together as one source rather than two. They are
    strictly sequential for a given org and neither is useful alone, so
    splitting them would buy a cross-source dependency and nothing else.

    **Incremental on `pushed_at`.** Every repository the org has pushed to in the
    window is listed — one cheap REST call — but a repository whose `pushed_at`
    has not moved since bronze last saw it cannot have new commits, so its
    history is read from `raw_github_repos` instead of being walked again. Only
    genuinely changed repositories cost a GraphQL walk.

    This replaces the on-disk `github_cache/`, which the deployed container does
    not have, and fixes a staleness bug in it along the way: that cache had no
    max-age, so a repository cached once was never refetched however many
    commits it gained (D32).

    Args:
        source: A github source; its config is the github_sources.yaml entry
            plus `org`.
        state: Unused. Unlike the other two legs, this one's window is not a
            feed to advance but a rolling aggregate; what makes it incremental
            is `pushed_at` per repository, which is finer than one watermark per
            source and lives in bronze.
        session: Open session, for reading `raw_github_repos`. Without one every
            repository is walked — correct, just slow, which is what the CLI and
            the tests want.

    Returns:
        One item per person, the org's totals as the watermark, and the
        repository histories fetched this run in `raw_repos`.
    """
    import aggregate_github
    import harvest_github

    entry = source.config
    org = entry["org"]
    months = entry.get("months", 12)

    token = harvest_github.load_token()
    # 30 days, not 30.5: this is the arithmetic `harvest_github.collect` does,
    # and the window has to mean the same thing on both paths.
    since = datetime.now(timezone.utc) - timedelta(days=months * 30)

    stored = _stored_repos(session, org)
    harvest: dict = {}
    fetched: list[dict] = []

    for repo in harvest_github.repos(org, token, since):
        name, pushed_at = repo["name"], repo["pushed_at"]
        known = stored.get(name)
        if known is not None and known.pushed_at >= pushed_at:
            harvest[name] = _in_window(known.payload, since)
            continue

        result = harvest_github.history(org, name, token, since)
        result["stars"] = repo["stargazers_count"]
        result["description"] = repo["description"]
        harvest[name] = result
        fetched.append(
            {"org": org, "repo": name, "pushed_at": pushed_at, "payload": result}
        )

    # Read the aggregation arguments through the module's own `load_labs()`
    # rather than off `entry` directly. It is not a plain passthrough: a null
    # `work_suffix` becomes a deliberately never-matching regex, so that a lab
    # with no known handle convention yields zero alias merges instead of
    # silently borrowing another lab's pattern. Rebuilding that mapping here
    # would reintroduce exactly the bug that constant exists to prevent.
    domain, work_suffix, domain_shared = aggregate_github.load_labs()[org]
    people = aggregate_github.aggregate(
        org, domain, work_suffix, domain_shared, raw=harvest
    )

    totals = people.get("totals", {})
    return FetchResult(
        items=[{**p, "org": org, "lab": entry["lab"]} for p in people.get("people", [])],
        watermark={"totals": totals},
        raw_repos=fetched,
    )


ADAPTERS = {
    "announcements": fetch_announcements,
    "papers": fetch_papers,
    "github": fetch_github,
}


def adapter_for(source):
    """Return the adapter for a source's leg.

    Raises:
        ValueError: On a leg with no adapter, rather than skipping it quietly.
    """
    try:
        return ADAPTERS[source.leg]
    except KeyError:
        raise ValueError(f"no adapter for leg {source.leg!r}") from None
