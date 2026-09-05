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


def fetch_paper_abstracts(session, limit: int | None = None) -> tuple[list, list]:
    """Turn the papers already in bronze into article-shaped records to score.

    Separate from `fetch_papers`, which harvests bylines for the people
    register. This reads `raw_papers` and fetches each paper's *citation page*
    for its abstract, so the scored text and the link the reader clicks are the
    same document.

    Args:
        session: Open session.
        limit: Stop after N papers (the n=1 proving path).

    Returns:
        Tuple of (article records, unresolved). Both are handed to the caller
        rather than written here, so landing stays in one place in the worker.
    """
    import yaml
    from paper_text import collect

    config = yaml.safe_load(
        (ROOT / "config" / "papers_sources.yaml").read_text(encoding="utf-8"))
    labs = {entry["lab"]: entry for entry in config["labs"]}
    return collect(session, labs, limit=limit)


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

    **Stored is not enough on its own — it has to be classified too.** That
    was originally because `classify_new` read article text from the corpus
    file, which resets to the image copy every firing on a container with no
    disk, so a stored-but-pending article could never be classified unless the
    page was fetched again. `classify_new` now reads the payload from
    `raw_articles` (D53), so the hazard is gone and the refetch is no longer
    load-bearing — it is left in place only because a stored article that is
    still pending is also the one case where the stored *text* may be a
    partial fetch worth replacing. Dropping the classified check would be safe
    for the classifier and is the obvious next simplification.

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

    # Recovery, in the same call as discovery. A lab whose site blocks us
    # (`backfill: wayback` in sources.yaml) yields a title and a one-sentence
    # summary from its feed; the archive has the page. This used to be a
    # separate script run by hand against announcements.json, which meant the
    # deployed pipeline never ran it at all and every OpenAI article reached
    # the classifier as a ~200-character blurb.
    #
    # Failures here are recorded, never raised: articles found is the source's
    # job and it has already done it. An article the archive has not crawled
    # yet is the ordinary case for anything published in the last few days.
    if lab.get("backfill") == "wayback" and items:
        try:
            unresolved.extend(fa.enrich_wayback(lab, items, since))
        except Exception as exc:  # noqa: BLE001
            unresolved.append({
                "kind": "backfill",
                "name": f"{lab['id']}:wayback",
                "reason": f"backfill failed, articles kept on summaries: {exc}",
            })

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


def _listing_fields(payload: dict, repo: dict) -> dict:
    """Overlay a live REST listing entry onto a repository's stored history.

    The releases leg ranks on stars, and bronze's copy is only as fresh as the
    last time that repository's history was re-walked — which happens when its
    `pushed_at` moves, and the github leg runs at cadence 3. A repository that
    stops being committed to would rank for ever on a frozen star count, and
    `deepseek-harness` gained 200,000 stars in the weeks this was built.

    So the releases leg lists each org itself: one cheap REST call chain, free
    on an authenticated token, and it leaves the github leg's incremental
    contract completely alone.

    `created_at` comes from the same place, and cannot be inferred. The obvious
    proxy — the earliest commit in the harvest window — reports `openai/whisper`
    (2022) as created in 2026, because it was dormant and got touched once. The
    error lands on exactly the famous quiet repositories a star ranking floats
    to the top (docs/decisions.md).

    Args:
        payload: The stored history from `raw_github_repos`.
        repo: The REST listing entry for the same repository.

    Returns:
        The payload with the listing fields overlaid.
    """
    return {
        **payload,
        "stars": repo.get("stargazers_count", 0),
        "description": repo.get("description"),
        "created_at": repo.get("created_at"),
        "language": repo.get("language"),
        "topics": repo.get("topics") or [],
        "archived": bool(repo.get("archived")),
    }


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




def fetch_releases(source, state=None, session=None) -> FetchResult:
    """Fetch this org's new release notes from its top-starred repositories.

    **The star ranking is the gate.** Watching all 770 repositories would be
    absurd; watching the top few dozen is one cheap call each. The ranking is
    computed here from `raw_github_repos` -- the bronze the github leg already
    maintains -- so this leg reads no file and needs no separate metadata
    fetch. On a container with no disk that is the only place it could come
    from anyway (D31).

    **The cursor is the watermark.** `source_state.watermark["cursors"]` holds
    one `published_at` per repository, and the orchestrator persists whatever
    this returns. An earlier version kept it in a JSON file beside a lock file;
    on the deployed shape that file resets to the image copy every firing, so
    the cursor would never advance past the first backfill and the leg would
    re-fetch the same few releases forever while looking healthy.

    Args:
        source: A releases source; its config is the github_sources.yaml entry
            plus `org`.
        state: The source's persistent state; `watermark["cursors"]` bounds
            each repository's fetch.
        session: Open session, for reading `raw_github_repos`. Without one
            there is no ranking and the source fails loudly rather than
            reporting no releases.

    Returns:
        Release notes in the announcements item shape, with the advanced
        cursors as the watermark. The documents are also written into
        `session` here rather than by the worker's landing phase, so that the
        cursor and the rows it describes become durable together.

    Raises:
        RuntimeError: When bronze holds no repositories for this org ("the
            github leg has not run", not "this org ships nothing"), when the
            live listing comes back empty against a non-empty bronze, or when
            every watched repository failed.
    """
    import harvest_github
    import rank_repos
    import yaml

    import fetch_releases as fr

    entry = source.config
    org = entry["org"]
    lab = entry["lab"]
    cfg = yaml.safe_load(
        (ROOT / "config" / "repo_signals.yaml").read_text(encoding="utf-8"))
    token = harvest_github.load_token()

    rows = session.scalars(
        select(m.RawGithubRepo).where(m.RawGithubRepo.org == org)
    ).all() if session is not None else []
    if not rows:
        raise RuntimeError(
            f"{org}: no repositories in raw_github_repos -- the github leg has "
            "not run, and reporting no releases here would be indistinguishable "
            "from an org that ships none"
        )

    # Stars and `created_at` come from a live listing, not from bronze. Bronze
    # is only as fresh as the last history walk, which happens when a
    # repository's `pushed_at` moves and on a leg running at cadence 3 -- so a
    # repository that stops being committed to would rank for ever on a frozen
    # star count. One listing chain per org, free on an authenticated token.
    since = datetime.now(timezone.utc) - timedelta(days=entry.get("months", 12) * 30)
    listing = {r["name"]: r for r in harvest_github.repos(org, token, since)}
    if not listing:
        # Bronze holds repositories for this org, so an empty listing is the
        # API failing to answer, not the org going quiet. Watching nothing and
        # reporting no releases would be indistinguishable from a week in which
        # nobody shipped.
        raise RuntimeError(
            f"{org}: the repository listing came back empty while bronze holds "
            f"{len(rows)} repositories -- treating that as a failed source "
            "rather than as an org with nothing to watch"
        )

    ranked = rank_repos.rank(
        [(r.org, r.repo, _listing_fields(r.payload, listing.get(r.repo, {})))
         for r in rows if r.repo in listing],
        cfg,
    )[: cfg["releases_watch"]]

    cursors = dict((getattr(state, "watermark", None) or {}).get("cursors") or {})

    items: list[dict] = []
    truncated = 0
    reached_all = True
    failures: list[str] = []

    for row in ranked:
        repo = row["repo"]
        try:
            found, stats = fr.new_releases(
                org, repo, token, lab, cursors.get(repo),
                cfg["releases_backfill"], cfg["releases_per_run"],
                cfg["releases_max_pages"])
        except Exception as exc:
            # One repository must not take out the org. A repository renamed or
            # made private since bronze last saw it raises a 404 that `_call`
            # does not retry, and letting it propagate would discard every
            # release already fetched from the repositories before it, waste
            # their rate limit, advance no cursor, and repeat every firing.
            failures.append(f"{repo}: {exc}")
            continue
        truncated += stats["truncated"]
        reached_all = reached_all and stats["reached_cursor"]
        if found:
            items += found
            cursors[repo] = fr.next_cursor(found)

    # Land the documents here, not in the worker's landing phase, because the
    # cursor gates every future fetch. `run_source` commits the advanced
    # watermark as soon as this returns; the landing phase commits its rows a
    # phase later. Anything failing in between -- the register load, a
    # redeploy, an OOM kill during a 9-30 minute firing -- rolls the rows back
    # while the cursor stays advanced, and `new_releases` then filters those
    # releases out for ever as already-seen. They would not appear in
    # `truncated`, no alert would name them, and a missing release is
    # indistinguishable from a quiet week.
    #
    # Writing them into the same session makes both durable at the same commit.
    # The failure direction is safe too: if this raises after landing,
    # `record_failure` leaves the watermark alone, so the next firing refetches
    # and the url-keyed upsert absorbs the duplicate.
    #
    # This is the first leg whose watermark gates future fetches. The github
    # leg is immune because it re-derives from `pushed_at` against bronze.
    if session is not None and items:
        from app.load_raw import load_article_records
        from app.pipeline.registry import CORPUS_LABELS, RELEASES

        load_article_records(session, items, CORPUS_LABELS[RELEASES])

    newest = max((i["published_at"] for i in items), default=None)
    watermark = {
        "cursors": cursors,
        "repos_watched": len(ranked),
        # The new/established split of what is being watched. Surfaced because
        # a star ranking is a hall of fame -- if this reads "established: 10"
        # every night, the leg is watching archives and the cut needs to become
        # a filter rather than a label.
        "watching": {age: sum(1 for r in ranked if r["age"] == age)
                     for age in ("new", "established", "unknown")},
        "truncated": truncated,
        # Kept because a walk that never reached the cursor left releases above
        # it unfetched and uncounted -- `truncated` only counts what this call
        # saw and dropped. Discarding it made that case indistinguishable from
        # a clean run.
        "reached_cursor": reached_all,
    }
    if newest:
        watermark["max_published"] = newest
    if failures:
        watermark["repo_failures"] = failures

    # Every watched repository failing is a broken source, not a quiet week --
    # a token rotated to one without the right scope 404s on all of them. Left
    # as a success it would reset `consecutive_failures` to zero every firing,
    # so `source_down` could never fire and the leg would stay dead behind
    # eight green rows.
    if ranked and len(failures) == len(ranked):
        raise RuntimeError(
            f"{org}: all {len(ranked)} watched repositories failed -- "
            f"first was {failures[0]}"
        )
    return FetchResult(items=items, watermark=watermark)


ADAPTERS = {
    "announcements": fetch_announcements,
    "papers": fetch_papers,
    "github": fetch_github,
    "releases": fetch_releases,
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
