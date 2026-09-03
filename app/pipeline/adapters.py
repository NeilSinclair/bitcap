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
    """

    items: list[dict] = field(default_factory=list)
    watermark: dict = field(default_factory=dict)
    unresolved: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0


def _cutoff(window_months: int) -> datetime:
    """The same window arithmetic fetch_announcements.collect() does."""
    return datetime.now(timezone.utc) - timedelta(days=window_months * 30.5)


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


def fetch_announcements(source, state=None) -> FetchResult:
    """Fetch one lab's in-window announcements.

    Calls the lab's discovery method directly rather than `collect()`, which
    loops over every lab with no error handling between them — one
    Cloudflare-blocked lab would abort the other six.

    Args:
        source: An announcements source; its config is the lab's sources.yaml
            entry plus `window_months`.
        state: Unused. Announcements are re-derived from a rolling window each
            run and the per-URL disk cache already makes that nearly free, so
            there is no incremental fetch to drive from a watermark.

    Returns:
        Articles, with the lab's newest publication date as the watermark.

    Raises:
        ValueError: On a method name the fetcher does not implement. The
            fetcher's own `collect()` calls `sys.exit` here, which would kill
            the whole run rather than one source.
    """
    import fetch_announcements as fa

    lab = source.config
    method = fa.METHODS.get(lab["method"])
    if method is None:
        raise ValueError(f"{source}: unknown discovery method {lab['method']!r}")

    items = method(lab, _cutoff(lab["window_months"]))
    newest = max((a["date"] for a in items), default=None)
    return FetchResult(items=items, watermark={"max_published": newest} if newest else {})


def fetch_papers(source, state=None) -> FetchResult:
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
        state: Unused; each harvester caches its own extraction results.

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
    available = {"months": 3, "model": "claude-sonnet-5", "limit": None}
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


def fetch_github(source, state=None) -> FetchResult:
    """Harvest one org's commits and aggregate them into a people register.

    Harvest and aggregate run together as one source rather than two. They are
    strictly sequential for a given org and neither is useful alone, so
    splitting them would buy a cross-source dependency and nothing else.

    Args:
        source: A github source; its config is the github_sources.yaml entry
            plus `org`.
        state: Unused; the per-repo disk cache is what makes a re-harvest cheap.
            Note there is no max-age on that cache — a genuine refresh means
            deleting the org's cached files (docs/handover.md §4c).

    Returns:
        One item per person, with the org's totals as the watermark.
    """
    import aggregate_github
    import harvest_github

    entry = source.config
    org = entry["org"]
    harvest_github.collect(org, months=entry.get("months", 12))

    # Read the aggregation arguments through the module's own `load_labs()`
    # rather than off `entry` directly. It is not a plain passthrough: a null
    # `work_suffix` becomes a deliberately never-matching regex, so that a lab
    # with no known handle convention yields zero alias merges instead of
    # silently borrowing another lab's pattern. Rebuilding that mapping here
    # would reintroduce exactly the bug that constant exists to prevent.
    domain, work_suffix, domain_shared = aggregate_github.load_labs()[org]
    people = aggregate_github.aggregate(org, domain, work_suffix, domain_shared)

    totals = people.get("totals", {})
    return FetchResult(
        items=[{**p, "org": org, "lab": entry["lab"]} for p in people.get("people", [])],
        watermark={"totals": totals},
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
