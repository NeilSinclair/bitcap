"""Validate the config files against each other and against the portfolio.

Run before anything consumes them. Catches the failure modes that would otherwise
degrade silently: a mechanism or category id that does not exist, an ISIN that the
fund does not hold, a holding with no entry, a category with no members, and a claim
with neither a source nor an explicit `unverified` note.

Scope is BIT Global Technology Leaders alone (docs/decisions.md D8).
"""

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
PORTFOLIOS = ROOT.parent / "research" / "docs" / "portfolio_views.json"

# Mirrors research/announcements/fetch_announcements.py's METHODS dict: each
# discovery function indexes these keys directly (lab["page_param"], etc.)
# rather than reading them defensively, so a missing one does not fail at
# config-load time -- it fails deep into a live run, after every earlier lab
# in the loop has already done its work, with no output written for any of
# them (bitcap-reviewer finding #7, confirmed by tracing collect()'s loop).
SOURCE_METHOD_REQUIRED_KEYS = {
    "sitemap": {"index_url", "url_contains", "text_source", "date_from"},
    "rss": {"index_url"},
    "listing_pagination": {"index_url", "page_param", "url_contains", "text_source"},
    "wayback_cdx": {"index_url", "url_contains"},
    "model_index": {"index_url"},
    "discourse": {"index_url"},
}


def check_registry(root: Path, views: dict) -> tuple[list[str], list[str]]:
    """Validate holdings.yaml against categories.yaml and the fund's real positions.

    Args:
        root: Directory holding the config files.
        views: Parsed portfolio_views.json.

    Returns:
        A (errors, warnings) pair of human-readable message lists.
    """
    cats = yaml.safe_load((root / "categories.yaml").read_text())
    hold = yaml.safe_load((root / "holdings.yaml").read_text())
    errors, warnings = [], []

    valid = {c["id"] for c in cats["categories"]}
    for c in cats["categories"]:
        # D8: a category figure is meaningless without the boundary that produced it.
        if not c.get("boundary"):
            errors.append(f"category {c['id']}: no boundary definition")
        if "lab_signal_routable" not in c:
            errors.append(f"category {c['id']}: lab_signal_routable not declared")

    scope = hold["scope"]
    actual = {
        p["isin"]: p for p in views["positions"]
        if p["fund_full"] == "BIT Global Technology Leaders"
        and p["as_of"] == "30.06.2026"
    }

    seen, used = set(), set()
    for h in hold["holdings"]:
        isin, name = h.get("isin"), h.get("name", "?")
        if isin in seen:
            errors.append(f"{isin}: duplicate holding")
        seen.add(isin)
        if isin not in actual:
            errors.append(f"{isin} ({name}): not a Technology Leaders position at 30.06.2026")
        if not h.get("categories"):
            errors.append(f"{name}: no category — every holding must be reachable")
        for cid in h.get("categories", []):
            if cid not in valid:
                errors.append(f"{name}: unknown category '{cid}'")
            used.add(cid)
        if h.get("ticker") and not h.get("ticker_verified", False):
            warnings.append(f"{name}: ticker {h['ticker']} unverified")

    for isin, p in actual.items():
        if isin not in seen:
            errors.append(f"{isin} ({p['name']}): held but absent from holdings.yaml")

    for cid in sorted(valid - used):
        warnings.append(f"category {cid}: no holdings — delete it or add members")

    if len(seen) != scope["positions"]:
        errors.append(
            f"scope says {scope['positions']} positions, file has {len(seen)}"
        )

    return errors, warnings


def check_lab_exposure(company: dict, enums: dict, tracked: set[str]) -> tuple[list[str], list[str]]:
    """Validate one company's direct-to-lab exposure links.

    `lab` is resolved against sources.yaml rather than a list held here, so a lab
    added to the register activates its edges without touching this file. An id
    the register does not carry is a dormant edge, not an error: the exposure is
    real and worth recording before the lab is ingestible. Dormant edges are
    reported by name, which is also how a typo'd id surfaces.

    Args:
        company: One entry from companies.yaml.
        enums: The shared sign/magnitude/confidence vocabularies.
        tracked: Lab ids currently in sources.yaml.

    Returns:
        A (errors, warnings) pair of human-readable message lists.
    """
    kinds = {"equity", "revenue_contract", "cloud_partnership", "supply", "credit_support"}
    name = company.get("name", "?")
    errors, warnings = [], []
    seen = set()

    for x in company.get("lab_exposure", []):
        lab, kind = x.get("lab"), x.get("kind")
        tag = f"{name}/lab_exposure/{lab or '?'}"
        if not lab:
            errors.append(f"{name}/lab_exposure: entry has no 'lab'")
            continue
        if kind not in kinds:
            errors.append(f"{tag}: kind='{kind}' not in {sorted(kinds)}")
        # One edge per (lab, kind): an equity stake and a supply contract with the
        # same lab are different exposures, two equity entries are a duplicate.
        if (lab, kind) in seen:
            errors.append(f"{tag}: duplicate ({lab}, {kind}) exposure")
        seen.add((lab, kind))

        for field in ("sign", "magnitude", "confidence"):
            if x.get(field) not in enums[field]:
                errors.append(f"{tag}: {field}='{x.get(field)}' not in vocabulary")
        if not x.get("why"):
            errors.append(f"{tag}: no 'why' — every link must explain itself")
        if not x.get("source") and not x.get("unverified"):
            errors.append(f"{tag}: neither source nor 'unverified' note")

        if lab not in tracked:
            warnings.append(
                f"{tag}: dormant — '{lab}' is not in sources.yaml, so this edge "
                "routes nothing until the lab is added to the register"
            )

    return errors, warnings


def check_practices(root: Path, mechanism_ids: set[str]) -> tuple[list[str], list[str]]:
    """Validate practices.yaml, the AI-team axis.

    Mechanisms and practices are separate vocabularies consumed by separate
    renderers, so an id present in both is ambiguous at the join: the renderer
    cannot tell which axis produced a tag. That collision is an error, not a
    warning.

    Args:
        root: Directory holding the config files.
        mechanism_ids: Currently valid mechanism ids, for the collision check.

    Returns:
        A (errors, warnings) pair of human-readable message lists.
    """
    path = root / "practices.yaml"
    errors, warnings = [], []
    if not path.exists():
        return [f"{path.name}: missing"], []

    doc = yaml.safe_load(path.read_text())
    for key in ("version", "practices", "enums", "required_per_tag"):
        if key not in doc:
            errors.append(f"practices.yaml: no '{key}' block")
    if errors:
        return errors, warnings

    seen = set()
    for pr in doc["practices"]:
        pid = pr.get("id", "?")
        if pid in seen:
            errors.append(f"practice {pid}: duplicate id")
        seen.add(pid)
        if pid in mechanism_ids:
            errors.append(
                f"practice {pid}: id also defined in mechanisms.yaml - "
                "a tag with this id is ambiguous at the join"
            )
        for field in ("label", "description", "adopt_note"):
            if not pr.get(field):
                errors.append(f"practice {pid}: no '{field}'")

        # An undifferentiated "it got better" is the thing this tag replaced.
        if pid == "model_capability":
            dims = pr.get("dimensions")
            if not dims:
                errors.append("practice model_capability: no 'dimensions' vocabulary")
            else:
                for name, desc in dims.items():
                    if not desc:
                        errors.append(f"model_capability/{name}: dimension has no definition")

    for enum in ("action", "impact", "confidence"):
        if not doc["enums"].get(enum):
            errors.append(f"practices.yaml: enums.{enum} missing or empty")

    # The citation guarantee has to hold on both axes or the AI-team digest can
    # surface a recommendation with nothing behind it.
    if "quote" not in doc["required_per_tag"]:
        errors.append("practices.yaml: 'quote' not required per tag - no citation guarantee")

    if "model_capability" in seen and "dimensions" not in doc.get(
        "required_per_tag_model_capability", []
    ):
        errors.append(
            "practices.yaml: model_capability does not require 'dimensions'"
        )

    return errors, warnings


# Every key a lab entry may carry. An allowlist rather than a spell-check:
# `backfil: wayback` is not a near miss of anything by string distance, it is
# simply a key no code reads, and the lab silently keeps its feed summaries.
# Add a key here when you add one to sources.yaml -- that is the point.
SOURCE_LAB_KEYS = {
    "id", "label", "method", "index_url", "text_source", "date_from",
    "url_contains", "notes", "also", "enabled", "backfill", "window_months",
    "baseline", "date_basis", "category", "page_param", "user_agent",
}

# Text-recovery strategies a lab may declare with `backfill:`. Read by
# `fetch_announcements.collect` and `adapters.fetch_announcements`; listed here
# so an unknown value is an error rather than a silent no-op.
BACKFILL_METHODS = {"wayback"}


def check_sources(root: Path) -> list[str]:
    """Validate sources.yaml: every lab carries the keys its method needs.

    Args:
        root: Directory holding the config files.

    Returns:
        Error message list.
    """
    errors = []
    srcs = yaml.safe_load((root / "sources.yaml").read_text())
    for lab in srcs["labs"]:
        lab_id = lab.get("id", "?")
        # A lab's secondary channels are validated exactly like its primary
        # one. They are the redundancy that catches an incomplete feed, so a
        # typo silently disabling one puts the lab back on a single channel
        # while the run still reports success.
        # Disabled channels are validated too. A channel turned off while it
        # misbehaves is meant to be turned back on, and a config error that
        # only surfaces on re-enabling is one found at the worst moment.
        for channel in [lab] + list(lab.get("also", [])):
            method = channel.get("method")
            if method not in SOURCE_METHOD_REQUIRED_KEYS:
                errors.append(f"sources.yaml/{lab_id}: unknown method '{method}'")
                continue
            # `channels()` reads `enabled` by truthiness, so `enabled: "false"`
            # (quoted) or `enabled: flase` parses as a truthy string and the
            # channel keeps running while the file says it is off. Same shape
            # as the `domain_shared is not a bool` check in
            # check_github_sources.
            if not isinstance(channel.get("enabled", True), bool):
                errors.append(
                    f"sources.yaml/{lab_id}: method '{method}' has "
                    f"enabled: {channel['enabled']!r}, which is not a bool"
                )
            for key in sorted(SOURCE_METHOD_REQUIRED_KEYS[method]):
                # Checked on the channel's own keys, not the merged view. A
                # secondary channel inherits `id` and `label` deliberately,
                # but inheriting `index_url` would point it at the primary
                # feed and it would appear to work.
                if key not in channel:
                    errors.append(
                        f"sources.yaml/{lab_id}: method '{method}' requires "
                        f"'{key}', missing"
                    )

        # `backfill` selects text recovery for a lab whose site blocks us, and
        # is matched by equality in two places, so a wrong value does not raise
        # -- it silently leaves the lab on feed summaries, which is how OpenAI
        # came to be scored on 200-character blurbs while every check stayed
        # green.
        backfill = lab.get("backfill")
        if backfill is not None and backfill not in BACKFILL_METHODS:
            errors.append(
                f"sources.yaml/{lab_id}: unknown backfill '{backfill}' "
                f"(known: {', '.join(sorted(BACKFILL_METHODS))})"
            )

        # A misspelt *key* is the same failure and nothing else can see it: no
        # code reads `backfil`, so the lab quietly keeps its summaries. This
        # replaces a near-miss test that did not catch `backfil` -- the very
        # example its own comment cited -- because collapsing case and
        # underscores cannot recover a dropped letter. An allowlist has no such
        # gap: anything not named here is either a typo or a key someone added
        # without telling this file.
        for key in lab:
            if key not in SOURCE_LAB_KEYS:
                errors.append(
                    f"sources.yaml/{lab_id}: unknown key '{key}' -- nothing "
                    f"reads it (known: {', '.join(sorted(SOURCE_LAB_KEYS))})"
                )
    return errors


def check_github_sources(root: Path, tracked_labs: set[str]) -> list[str]:
    """Validate github_sources.yaml, previously not read by this file at all.

    Args:
        root: Directory holding the config files.
        tracked_labs: Lab ids currently in sources.yaml, to catch a `lab:`
            typo here that would otherwise silently misfile a whole org's
            worth of GitHub-derived people under no register entry at all.

    Returns:
        Error message list.
    """
    path = root / "github_sources.yaml"
    if not path.exists():
        return [f"{path.name}: missing"]

    doc = yaml.safe_load(path.read_text())
    errors = []
    for org, entry in doc.get("orgs", {}).items():
        lab = entry.get("lab")
        if not lab:
            errors.append(f"github_sources.yaml/{org}: no 'lab'")
        elif lab not in tracked_labs:
            errors.append(f"github_sources.yaml/{org}: lab '{lab}' not in sources.yaml")

        if not isinstance(entry.get("domain_shared", False), bool):
            errors.append(f"github_sources.yaml/{org}: domain_shared is not a bool")

        suffix = entry.get("work_suffix")
        if suffix is not None:
            try:
                re.compile(suffix)
            except re.error as exc:
                errors.append(f"github_sources.yaml/{org}: work_suffix does not compile: {exc}")
    return errors




def check_entities(root: Path) -> list[str]:
    """Validate entities.yaml — the first-mention vocabulary.

    `research/corpus/first_mention.py` indexes `model_families`,
    `max_version_parts`, `new_within_days` and `unannounced_top` directly, so a
    mistyped key is a KeyError deep into a run. An empty `model_families` is
    worse than that: it raises nothing, matches nothing, and reports a quiet
    week for ever.

    Args:
        root: Directory holding the config files.

    Returns:
        Error message list.
    """
    path = root / "entities.yaml"
    if not path.exists():
        return [f"{path.name}: missing"]
    doc = yaml.safe_load(path.read_text()) or {}
    errors = []
    for key in ("new_within_days", "max_version_parts", "unannounced_top"):
        value = doc.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors.append(
                f"entities.yaml: {key} must be a positive integer, not {value!r}")
    families = doc.get("model_families")
    if not families or not all(isinstance(f, str) and f for f in families):
        errors.append(
            "entities.yaml: model_families must be a non-empty list of strings "
            "-- an empty one matches nothing and reports a quiet week for ever")
    if not isinstance(doc.get("deny") or [], list):
        errors.append("entities.yaml: deny must be a list")
    return errors


def check_repo_signals(root: Path) -> list[str]:
    """Validate repo_signals.yaml — what the releases leg reads at runtime.

    `adapters.fetch_releases` indexes `releases_watch`, `releases_backfill`,
    `releases_per_run` and `releases_max_pages` directly, so a mistyped key is
    a KeyError that fails every releases source on every firing. Worse is a
    value of the wrong type: `releases_watch: "25"` slices the ranked rows with
    a string and raises something considerably less legible than this message.

    Args:
        root: Directory holding the config files.

    Returns:
        Error message list.
    """
    path = root / "repo_signals.yaml"
    if not path.exists():
        return [f"{path.name}: missing"]
    doc = yaml.safe_load(path.read_text()) or {}
    errors = []
    for key in ("releases_watch", "releases_backfill", "releases_per_run",
                "releases_max_pages", "new_within_days"):
        value = doc.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors.append(
                f"repo_signals.yaml: {key} must be a positive integer, not {value!r}"
            )
    if not isinstance(doc.get("min_stars"), int):
        errors.append("repo_signals.yaml: min_stars must be an integer")
    errors.extend(_check_relevance(root, doc))
    return errors


def _check_relevance(root: Path, doc: dict) -> list[str]:
    """Validate the `relevance` block inside repo_signals.yaml.

    Three of these five checks exist because the failure they catch happens
    *after* money is spent or *after* the watch list has already changed:

    * An unpriced `model` works all the way through the provider call and then
      raises KeyError in `providers._cost`, so the call is billed and no verdict
      comes back. That is the whole reason this imports the price table rather
      than pattern-matching the name.
    * A missing prompt file raises on the first repository of the first org.
    * `max_judged` below `releases_watch` can never fill the watch list, so the
      leg quietly watches fewer repositories than it is configured to.

    Args:
        root: Directory holding the config files.
        doc: The parsed repo_signals.yaml.

    Returns:
        Error message list.
    """
    block = doc.get("relevance")
    if not isinstance(block, dict):
        # Absent is an error rather than a default: the releases leg would keep
        # watching what it watches today and nothing would say why.
        return ["repo_signals.yaml: relevance must be a mapping"]

    errors = []
    if not isinstance(block.get("enabled"), bool):
        errors.append(
            "repo_signals.yaml: relevance.enabled must be true or false, not "
            f"{block.get('enabled')!r} — a missing kill switch reads as on"
        )

    # Guarded, like the identical insert further down this file: unguarded,
    # repeated validation in one process prepends the path again each time and
    # permanently shadows any same-named installed module.
    shim = str(root.parent / "research" / "announcements")
    if shim not in sys.path:
        sys.path.insert(0, shim)
    try:
        import providers
    except ImportError:
        # Reported, not raised. `check_dedupe` does the same: a moved shim must
        # not take down the whole validator with a traceback when its other
        # thirty checks would still have run.
        errors.append("repo_signals.yaml: cannot import providers to check "
                      "relevance.provider and relevance.model")
        return errors

    provider = block.get("provider")
    if provider not in providers.PROVIDERS:
        errors.append(
            f"repo_signals.yaml: relevance.provider {provider!r} is not one of "
            f"{sorted(providers.PROVIDERS)}"
        )
    model = block.get("model")
    if model not in providers.PRICES:
        errors.append(
            f"repo_signals.yaml: relevance.model {model!r} has no entry in "
            "providers.PRICES — the call would be billed and then raise while "
            "building the cost record"
        )

    version = block.get("prompt_version")
    if not isinstance(version, str) or not version:
        errors.append("repo_signals.yaml: relevance.prompt_version must be a name")
    elif not (root.parent / "prompts" / "repo_relevance" / f"{version}.md").exists():
        errors.append(
            f"repo_signals.yaml: prompts/repo_relevance/{version}.md is missing"
        )

    judged = block.get("max_judged")
    watch = doc.get("releases_watch")
    if not isinstance(judged, int) or isinstance(judged, bool) or judged < 1:
        errors.append(
            f"repo_signals.yaml: relevance.max_judged must be a positive integer, "
            f"not {judged!r}"
        )
    elif isinstance(watch, int) and not isinstance(watch, bool) and judged < watch:
        errors.append(
            f"repo_signals.yaml: relevance.max_judged ({judged}) is below "
            f"releases_watch ({watch}), so the watch list can never fill"
        )
    return errors


def check_pipeline(root: Path) -> list[str]:
    """Validate pipeline.yaml — the file that decides what the cron spends.

    Every value here fails *silently* when wrong, because each is read with a
    `.get(..., default)` or fed straight into a comparison:

    * `content_band: High` (capitalised) makes the band filter match nothing, so
      **zero content alerts are raised, forever**, with no error anywhere.
    * `content_max_age_days: 0` does the same thing by a different route: both
      content rules bound `published_on` by it, so nothing is ever recent enough.
    * a mistyped `cadence` key means that leg never runs, and the register
      quietly covers less than anyone thinks.
    * a `channel` with no delivery function, or a `budget` that is absent or
      non-numeric, is not discovered until a cron fires unattended.

    Args:
        root: Directory holding the config files.

    Returns:
        Error message list.
    """
    path = root / "pipeline.yaml"
    if not path.exists():
        return [f"{path.name}: missing"]
    doc = yaml.safe_load(path.read_text()) or {}
    errors = []

    budget = doc.get("budget") or {}
    for key in ("per_run_usd", "per_month_usd"):
        value = budget.get(key)
        if not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"pipeline.yaml/budget: '{key}' must be a positive number")
    if all(isinstance(budget.get(k), (int, float)) for k in ("per_run_usd", "per_month_usd")):
        if budget["per_month_usd"] < budget["per_run_usd"]:
            errors.append(
                "pipeline.yaml/budget: per_month_usd is below per_run_usd, so a single "
                "run can never complete inside the monthly ceiling"
            )

    # Imported, not restated. A hardcoded copy of this set goes stale the first
    # time a leg is added: the new leg's cadence entry is then reported as not
    # a leg, by the very check meant to catch a mistyped one.
    from app.pipeline.registry import LEGS

    legs = set(LEGS)
    for key, value in (doc.get("cadence") or {}).items():
        if key not in legs | {"drift"}:
            errors.append(
                f"pipeline.yaml/cadence: '{key}' is not a leg {sorted(legs)} or 'drift'"
            )
        if not isinstance(value, int) or value < 1:
            errors.append(f"pipeline.yaml/cadence/{key}: must be an integer >= 1")

    # The kill switch. A typo here is the worst silent failure in this file:
    # `enabled: {releaces: false}` leaves the leg running and reads, to whoever
    # typed it, as switched off. And any non-empty string is truthy.
    for key, value in (doc.get("enabled") or {}).items():
        if key not in legs:
            errors.append(
                f"pipeline.yaml/enabled: '{key}' is not a leg {sorted(legs)} -- "
                "the leg it was meant to switch off is still running"
            )
        if not isinstance(value, bool):
            errors.append(
                f"pipeline.yaml/enabled/{key}: must be true or false, not "
                f"{value!r} -- any non-empty string reads as on"
            )

    alerts = doc.get("alerts") or {}
    bands = ("none", "low", "medium", "high")
    # 0 suppresses every content alert forever, in exactly the way a
    # capitalised `content_band` does, and reads as a deliberate-looking number.
    max_age = alerts.get("content_max_age_days")
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age < 1:
        errors.append(
            f"pipeline.yaml/alerts: content_max_age_days {max_age!r} must be a "
            f"positive int -- 0 or missing silently stops every content alert")
    elif max_age < 92:
        errors.append(
            f"pipeline.yaml/alerts: content_max_age_days {max_age} is shorter "
            f"than the ~3-month announcements window, so real announcements "
            f"would stop alerting")
    if alerts.get("content_band") not in bands:
        errors.append(
            f"pipeline.yaml/alerts: content_band {alerts.get('content_band')!r} is not "
            f"one of {list(bands)} -- a mismatch silently raises no content alerts"
        )
    if alerts.get("channel") not in ("stdout", "webhook"):
        errors.append(
            f"pipeline.yaml/alerts: channel {alerts.get('channel')!r} is not stdout|webhook"
        )
    floor = alerts.get("drift_agreement_floor")
    if not isinstance(floor, (int, float)) or not 0 <= floor <= 1:
        errors.append("pipeline.yaml/alerts: drift_agreement_floor must be between 0 and 1")
    for key in ("source_down_runs", "max_deliveries_per_run"):
        if key in alerts and (not isinstance(alerts[key], int) or alerts[key] < 1):
            errors.append(f"pipeline.yaml/alerts/{key}: must be an integer >= 1")

    classification = doc.get("classification") or {}
    if not classification.get("model"):
        errors.append("pipeline.yaml/classification: no 'model'")
    if not isinstance(classification.get("workers", 1), int) or classification.get("workers", 1) < 1:
        errors.append("pipeline.yaml/classification: workers must be an integer >= 1")

    # `fetch:` fails silently in the way this whole function exists to catch.
    # `min_interval_seconds` is keyed by *registrable domain* and matched with
    # `host == key or host.endswith("." + key)`, so writing `arxiv` instead of
    # `arxiv.org` matches nothing, falls through to `default_min_interval_seconds`,
    # and hits arXiv three times faster than its published guidance -- which is
    # what caused the 2026-09-04 outage (D53). Validation would report clean.
    fetch = doc.get("fetch") or {}
    if not fetch:
        errors.append("pipeline.yaml: no 'fetch' block -- the papers harvesters "
                      "would fall back to hardcoded defaults with nothing saying so")
    intervals = fetch.get("min_interval_seconds")
    if not isinstance(intervals, dict) or not intervals:
        errors.append("pipeline.yaml/fetch: min_interval_seconds must be a non-empty "
                      "mapping of host -> seconds")
    else:
        for host, seconds in intervals.items():
            if "." not in str(host):
                errors.append(
                    f"pipeline.yaml/fetch/min_interval_seconds: {host!r} is not a domain "
                    "(host matching is exact-or-subdomain, so a bare name never matches "
                    "and the host silently drops to default_min_interval_seconds)"
                )
            if not isinstance(seconds, (int, float)) or seconds <= 0:
                errors.append(
                    f"pipeline.yaml/fetch/min_interval_seconds/{host}: must be a positive number"
                )
        if "arxiv.org" not in intervals:
            errors.append(
                "pipeline.yaml/fetch/min_interval_seconds: no 'arxiv.org' entry -- five "
                "harvesters fetch arXiv and it is the host that rate-limited us (D53)"
            )
    for key, kind in (("default_min_interval_seconds", (int, float)),
                      ("discovery_ttl_hours", (int, float)),
                      ("max_backoff_seconds", (int, float)),
                      ("retries", int)):
        value = fetch.get(key)
        if not isinstance(value, kind) or isinstance(value, bool) or value <= 0:
            errors.append(f"pipeline.yaml/fetch: '{key}' must be a positive number")
    if isinstance(fetch.get("retries"), int) and fetch["retries"] > 10:
        errors.append(
            "pipeline.yaml/fetch: retries above 10 means a rate-limited host is hammered "
            "for minutes; a lost source is a partial run, not a dead one (D27)"
        )

    # The dashboard's horizon. Silent when wrong in the way that matters most:
    # a value of 1 renders yesterday's articles and nothing else, which looks
    # exactly like a pipeline that has stopped ingesting. Absent is legal and
    # means no window; present and nonsense is not.
    display = doc.get("display")
    if display is not None:
        window = display.get("corpus_window_days")
        if window is not None and (
            not isinstance(window, int) or isinstance(window, bool) or window < 7
        ):
            errors.append(
                f"pipeline.yaml/display: corpus_window_days={window!r} must be an "
                "integer of at least 7 -- a shorter horizon empties the dashboard and "
                "reads as a stalled pipeline rather than as a setting"
            )
    return errors


def check_papers_sources(root: Path, tracked_labs: set[str]) -> list[str]:
    """Validate papers_sources.yaml — the papers register.

    The silent failure this catches: a mistyped `url_field`. Every paper then
    resolves to `url: None`, the register drops the lot as `skipped_no_url`, and
    the source is still recorded SUCCEEDED with a healthy `items_seen`. A whole
    lab's papers and people vanish and the run looks fine.

    Args:
        root: Directory holding the config files.
        tracked_labs: Lab ids in sources.yaml.

    Returns:
        Error message list.
    """
    path = root / "papers_sources.yaml"
    if not path.exists():
        return [f"{path.name}: missing"]
    doc = yaml.safe_load(path.read_text()) or {}
    errors = []
    url_fields = {"url", "meta_url", "announcement_url"}
    returns = {"papers", "papers_and_unresolved"}
    strategies = {"blockquote_abstract", "heading_section", "lead_section"}

    for entry in doc.get("labs", []):
        lab = entry.get("lab", "?")
        if lab not in tracked_labs:
            errors.append(f"papers_sources.yaml/{lab}: lab not in sources.yaml")
        # A mistyped strategy is the same class of silent failure as a mistyped
        # `url_field`: extraction falls back through the weaker strategies, so
        # the papers still arrive -- as lead sections full of page furniture,
        # scoring zero, with nothing recorded as unresolved.
        abstract = entry.get("abstract") or {}
        if not isinstance(abstract, dict) or "strategy" not in abstract:
            errors.append(
                f"papers_sources.yaml/{lab}: no `abstract` block -- the scoring "
                f"leg has no way to read this lab's papers")
        else:
            if abstract.get("strategy") not in strategies:
                errors.append(
                    f"papers_sources.yaml/{lab}: abstract.strategy "
                    f"{abstract.get('strategy')!r} not in {sorted(strategies)} -- "
                    f"extraction would fall back and never say so")
            if not isinstance(abstract.get("max_chars"), int) or abstract["max_chars"] < 200:
                errors.append(
                    f"papers_sources.yaml/{lab}: abstract.max_chars "
                    f"{abstract.get('max_chars')!r} must be an int >= 200 -- the "
                    f"extractor rejects anything shorter as furniture")
        if entry.get("url_field") not in url_fields:
            errors.append(
                f"papers_sources.yaml/{lab}: url_field {entry.get('url_field')!r} not in "
                f"{sorted(url_fields)} -- every paper would resolve to a null citation "
                "and be dropped while the source still reports success"
            )
        if entry.get("returns") not in returns:
            errors.append(f"papers_sources.yaml/{lab}: returns must be one of {sorted(returns)}")
        if entry.get("enabled", True):
            if not entry.get("module") or not entry.get("entry"):
                errors.append(f"papers_sources.yaml/{lab}: enabled but names no module/entry")
        elif not (entry.get("notes") or "").strip():
            # A lab that is off must say why, or a deliberate exclusion is
            # indistinguishable from an oversight.
            errors.append(f"papers_sources.yaml/{lab}: disabled with no note explaining why")
    return errors



def check_people(root: Path, tracked_labs: set[str]) -> list[str]:
    """Check people.yaml joins the register and cites everything it claims.

    The people leg's failure mode is not a crash but a stale attribution: a
    researcher who has left still rendered as a voice of the lab. So the join
    to sources.yaml is enforced, and so is the rule that a claim without a
    citation does not belong in the file.

    Args:
        root: Directory holding the config files.
        tracked_labs: Lab ids in sources.yaml.

    Returns:
        Error message list.
    """
    path = root / "people.yaml"
    if not path.exists():
        return [f"{path.name}: missing"]
    doc = yaml.safe_load(path.read_text()) or {}
    errors = []
    tiers = {"own_site", "self_post", "lab_post", "search_index"}

    for lab, entry in (doc.get("labs") or {}).items():
        if lab not in tracked_labs:
            errors.append(f"people.yaml/{lab}: lab not in sources.yaml")
        current = {p.get("name") for p in entry.get("people", [])}
        for person in entry.get("people", []):
            who = f"people.yaml/{lab}/{person.get('name', '?')}"
            if not person.get("role"):
                errors.append(f"{who}: no role")
            if not person.get("sources"):
                errors.append(f"{who}: no citation -- every claim here needs one")
            if person.get("x_handle") and person.get("x_evidence") not in tiers:
                errors.append(
                    f"{who}: x_handle with x_evidence {person.get('x_evidence')!r} not in "
                    f"{sorted(tiers)} -- an unattributed handle is a guess"
                )
        for gone in entry.get("departed") or []:
            if gone.get("name") in current:
                errors.append(
                    f"people.yaml/{lab}/{gone['name']}: listed as both current and departed"
                )
            if not str(gone.get("source", "")).startswith("https://"):
                errors.append(f"people.yaml/{lab}/{gone.get('name')}: departure with no source")
    for lab in tracked_labs - set(doc.get("labs") or {}):
        errors.append(f"people.yaml/{lab}: registered lab with no people entry")
    return errors



def check_dedupe(path: Path | None = None) -> list[str]:
    """Check dedupe.yaml.

    The band is what this guards. `cosine_high` below `cosine_low` inverts the
    logic silently — every pair falls through to "auto-merge" and the collapse
    starts deleting claims with no error anywhere. A null threshold does the
    same by crashing the phase mid-run instead of at startup.

    Args:
        path: dedupe.yaml to check. Defaults to the shipped one; tests override.

    Returns:
        Error message list.
    """
    path = path or ROOT / "dedupe.yaml"
    if not path.exists():
        return [f"{path.name}: missing"]
    doc = yaml.safe_load(path.read_text()) or {}
    errors = []

    thresholds = doc.get("thresholds") or {}
    high, low = thresholds.get("cosine_high"), thresholds.get("cosine_low")
    for name, value in (("cosine_high", high), ("cosine_low", low)):
        if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
            errors.append(
                f"dedupe.yaml: {name}={value!r} must be a number in [0, 1] — "
                "run research/dedupe/calibrate.py to derive it from the labels"
            )
    if isinstance(high, (int, float)) and isinstance(low, (int, float)) and high < low:
        errors.append(
            f"dedupe.yaml: cosine_high={high} is below cosine_low={low}, which "
            "inverts the band — every pair would auto-merge"
        )

    window = doc.get("window_days")
    if not isinstance(window, int) or window < 1:
        errors.append(f"dedupe.yaml: window_days={window!r} must be a positive integer")

    releases = (doc.get("releases") or {}).get("window_hours")
    if not isinstance(releases, int) or releases < 24:
        errors.append(
            f"dedupe.yaml: releases.window_hours={releases!r} must be an integer "
            ">= 24 — published_on is a date, so a shorter window collapses to zero"
        )

    adjudication = doc.get("adjudication") or {}
    version = adjudication.get("prompt_version")
    prompt = ROOT.parent / "prompts" / "duplicate_adjudication" / f"{version}.md"
    if not version or not prompt.exists():
        errors.append(
            f"dedupe.yaml: adjudication.prompt_version={version!r} has no file at "
            f"prompts/duplicate_adjudication/{version}.md"
        )

    embedding = (doc.get("embedding") or {}).get("batch_size")
    if not isinstance(embedding, int) or embedding < 1:
        errors.append(f"dedupe.yaml: embedding.batch_size={embedding!r} must be positive")

    span = (doc.get("releases") or {}).get("max_span_days")
    if not isinstance(span, int) or span < 1:
        errors.append(
            f"dedupe.yaml: releases.max_span_days={span!r} must be a positive integer — "
            "without a ceiling a daily-release repo chains into one permanent group"
        )

    pairing = doc.get("pairing") or {}
    if not isinstance(pairing.get("enabled"), bool):
        errors.append(
            f"dedupe.yaml: pairing.enabled={pairing.get('enabled')!r} must be true or "
            "false — a missing kill switch reads as on, which is the wrong default "
            "for a switch"
        )
    pair_window = pairing.get("window_days")
    if not isinstance(pair_window, int) or pair_window < 1:
        errors.append(
            f"dedupe.yaml: pairing.window_days={pair_window!r} must be a positive integer"
        )
    cap = pairing.get("max_identifiers")
    if not isinstance(cap, int) or cap < 1:
        errors.append(
            f"dedupe.yaml: pairing.max_identifiers={cap!r} must be a positive integer — "
            "0 would silently disable pairing while `enabled` still claimed it was on"
        )

    # The gate that does the actual separating. Empty or misspelled, pairing
    # matches nothing and reports zero links, which is indistinguishable from
    # "no release was related to anything" -- and the event types are free text
    # in this file but a closed vocabulary in config/scoring.yaml.
    events = pairing.get("announcement_events")
    if not isinstance(events, list) or not events:
        errors.append(
            f"dedupe.yaml: pairing.announcement_events={events!r} must be a non-empty "
            "list -- with nothing in it every release pairs with nothing and the run "
            "still reports success"
        )
    else:
        weights = yaml.safe_load(
            (ROOT / "scoring.yaml").read_text(encoding="utf-8"))["event_weight"]
        unknown = sorted(set(events) - set(weights))
        if unknown:
            errors.append(
                f"dedupe.yaml: pairing.announcement_events names {unknown}, which are "
                "not event types in scoring.yaml -- a typo here removes a gate rather "
                "than raising"
            )

    # Both model names are checked against the price table, because `_cost`
    # indexes `PRICES[model]` *after* the provider has billed the call. An
    # unpriced model therefore spends real money and then raises a KeyError,
    # which the dedupe phase swallows: money gone, no cost record, no groups.
    # Failing here is the difference between a typo caught in CI and a typo
    # found in the ledger.
    import sys as _sys
    # Guarded: unguarded, repeated validation in one process prepends this path
    # again each time and permanently shadows any same-named installed module.
    _shim = str(ROOT.parent / "research" / "announcements")
    if _shim not in _sys.path:
        _sys.path.insert(0, _shim)
    try:
        from providers import PRICES, PROVIDERS
    except ImportError:  # pragma: no cover - only if the shim moves
        return errors + ["dedupe.yaml: cannot import providers to check model names"]

    for key, model in (("embedding.model", (doc.get("embedding") or {}).get("model")),
                       ("adjudication.model", adjudication.get("model"))):
        if model not in PRICES:
            errors.append(
                f"dedupe.yaml: {key}={model!r} is not in providers.PRICES — the call "
                "would be billed and then fail when its cost is computed"
            )
    provider = adjudication.get("provider")
    if provider not in PROVIDERS:
        errors.append(
            f"dedupe.yaml: adjudication.provider={provider!r} not in {sorted(PROVIDERS)}"
        )

    return errors


def check_digest(path: Path | None = None) -> list[str]:
    """Validate digest.yaml — every value here fails by emptying the digest.

    Nothing downstream raises on a bad value, and that is by design: an
    unrecognised band ranks below everything so a vocabulary change quietens the
    report rather than taking down the firing that publishes it. The cost of
    that choice is that a typo is indistinguishable from a quiet fortnight, and
    the page will state — truthfully and misleadingly — that the filter is
    working. Four edits that each empty a digest permanently and raise nothing:

    * ``ai.actions: []`` — no action can ever match.
    * ``ai.min_band: med`` — unknown floor ranks 99, so every item is rejected.
    * ``investment.always_band: higgh`` — same, killing the early-signal leg.
    * ``window_hours: 6`` — the window is compared at date resolution, so
      anything under 24 puts start and end on the same day and nothing is ever
      in window.

    Args:
        path: digest.yaml to check. Defaults to the shipped one; tests override.

    Returns:
        Error message list.
    """
    path = path or ROOT / "digest.yaml"
    if not path.exists():
        return [f"{path.name}: missing"]
    doc = yaml.safe_load(path.read_text()) or {}
    errors = []

    # Bands come from scoring.yaml so the two files cannot drift apart: the
    # digest filters on the bands the scorer actually assigns.
    scoring = yaml.safe_load((ROOT / "scoring.yaml").read_text()) or {}
    bands = {b["label"] for b in scoring.get("bands", [])}
    if not bands:
        errors.append("scoring.yaml: no bands — digest thresholds cannot be checked")

    window = doc.get("window_hours")
    if not isinstance(window, int) or window < 24:
        errors.append(
            f"digest.yaml: window_hours={window!r} must be an integer >= 24 — "
            "the window is compared at date resolution, so anything smaller "
            "puts start and end on the same day and every digest is empty"
        )
    elif window % 24:
        errors.append(
            f"digest.yaml: window_hours={window} must be a multiple of 24, or "
            "the period grid and the date-resolution comparison disagree"
        )

    if not isinstance(doc.get("max_holdings_shown"), int) or doc["max_holdings_shown"] < 1:
        errors.append("digest.yaml: max_holdings_shown must be a positive integer")

    for kind in ("investment", "ai"):
        rules = doc.get(kind)
        if not isinstance(rules, dict):
            errors.append(f"digest.yaml: missing '{kind}' block")
            continue
        cap = rules.get("max_items")
        if not isinstance(cap, int) or cap < 1:
            errors.append(f"digest.yaml/{kind}: max_items must be a positive integer")

    inv = doc.get("investment") or {}
    strength = inv.get("min_strength")
    if not isinstance(strength, (int, float)) or not 0 < strength <= 1:
        errors.append(
            f"digest.yaml/investment: min_strength={strength!r} must be in (0, 1]"
        )
    if bands and inv.get("always_band") not in bands:
        errors.append(
            f"digest.yaml/investment: always_band='{inv.get('always_band')}' not a "
            f"band in scoring.yaml ({', '.join(sorted(bands))}) — an unknown band "
            "rejects every item silently"
        )

    ai = doc.get("ai") or {}
    if bands and ai.get("min_band") not in bands:
        errors.append(
            f"digest.yaml/ai: min_band='{ai.get('min_band')}' not a band in "
            f"scoring.yaml ({', '.join(sorted(bands))}) — an unknown band "
            "rejects every item silently"
        )
    actions = ai.get("actions")
    # The vocabulary the classifier actually emits, from practices.yaml.
    practices = yaml.safe_load((ROOT / "practices.yaml").read_text()) or {}
    known = set(practices.get("enums", {}).get("action", [])) or {"adopt", "investigate", "watch"}
    if not isinstance(actions, list) or not actions:
        errors.append(
            f"digest.yaml/ai: actions={actions!r} must be a non-empty list — "
            "an empty list matches nothing and empties the AI digest forever"
        )
    else:
        for action in actions:
            if action not in known:
                errors.append(
                    f"digest.yaml/ai: action '{action}' not in the practice "
                    f"vocabulary ({', '.join(sorted(known))})"
                )

    return errors


def main() -> int:
    mech = yaml.safe_load((ROOT / "mechanisms.yaml").read_text())
    comp = yaml.safe_load((ROOT / "companies.yaml").read_text())
    srcs = yaml.safe_load((ROOT / "sources.yaml").read_text())
    views = json.loads(PORTFOLIOS.read_text())

    valid_ids = {m["id"] for m in mech["mechanisms"]}
    tracked_labs = {lab["id"] for lab in srcs["labs"]}
    enums = mech["enums"]
    held = {p["isin"]: p for p in views["positions"]}
    # Kept out of `warnings`: the ticker warnings are truncated at five, and a
    # dormant lab edge is the one thing this file is asked to make visible.
    lab_warnings: list[str] = []
    # A security can sit in several funds, so keep the Technology Leaders set
    # separately rather than relying on whichever row landed in `held` last.
    tech_leaders = {
        p["isin"]: p["name"] for p in views["positions"]
        if p["fund_full"] == "BIT Global Technology Leaders"
    }
    errors, warnings = [], []

    seen: set = set()
    alias_owner: dict[str, str] = {}  # an alias must mean exactly one company
    for c in comp["companies"]:
        isin, name = c.get("isin"), c.get("name", "?")
        if not isin:
            errors.append(f"{name}: missing isin")
            continue
        if isin in seen:
            errors.append(f"{isin}: duplicate entry")
        seen.add(isin)
        if isin not in held:
            errors.append(f"{isin} ({name}): not held by any fund in portfolio_views.json")
        if c.get("ai_role") not in enums["ai_role"]:
            errors.append(f"{name}: ai_role '{c.get('ai_role')}' not in vocabulary")

        # Aliases feed the named-mention join directly. A two-character alias
        # ("ON", "SE") would fire on ordinary prose and attribute an article to
        # a company nobody wrote about, so the floor is enforced here.
        for alias in c.get("aliases", []):
            if not isinstance(alias, str) or len(alias.strip()) < 3:
                errors.append(f"{name}: alias {alias!r} shorter than 3 characters")
            elif alias in alias_owner and alias_owner[alias] != isin:
                errors.append(f"{name}: alias '{alias}' also claimed by {alias_owner[alias]}")
            else:
                alias_owner[alias] = isin

        for m in c.get("mechanisms", []):
            if m["id"] not in valid_ids:
                errors.append(f"{name}: unknown mechanism '{m['id']}'")
            for field in ("sign", "magnitude", "confidence"):
                if m.get(field) not in enums[field]:
                    errors.append(f"{name}/{m['id']}: {field}='{m.get(field)}' not in vocabulary")
            if not m.get("why"):
                errors.append(f"{name}/{m['id']}: no 'why' — every link must explain itself")

        le_errors, le_warnings = check_lab_exposure(c, enums, tracked_labs)
        errors += le_errors
        lab_warnings += le_warnings

        for bucket in ("tailwinds", "headwinds"):
            for item in c.get(bucket, []):
                if not item.get("source") and not item.get("unverified"):
                    errors.append(f"{name}/{bucket}: claim has neither source nor 'unverified' note")

        if c.get("ticker") and not c.get("ticker_verified", False):
            warnings.append(f"{name}: ticker {c['ticker']} unverified")

    missing = [(i, n) for i, n in tech_leaders.items() if i not in seen]

    for e in errors:
        print(f"ERROR   {e}")
    for w in warnings[:5]:
        print(f"warn    {w}")
    if len(warnings) > 5:
        print(f"warn    ... and {len(warnings) - 5} more unverified tickers")

    if lab_warnings:
        print(f"\n{len(lab_warnings)} dormant lab edge(s) — "
              f"register carries {len(tracked_labs)}: {', '.join(sorted(tracked_labs))}")
        for w in lab_warnings:
            print(f"dormant {w}")
        print()

    reg_errors, reg_warnings = check_registry(ROOT, views)
    prac_errors, prac_warnings = check_practices(ROOT, valid_ids)
    src_errors = check_sources(ROOT)
    gh_errors = check_github_sources(ROOT, tracked_labs)
    pipe_errors = check_pipeline(ROOT)
    papers_errors = check_papers_sources(ROOT, tracked_labs)
    people_errors = check_people(ROOT, tracked_labs)
    digest_errors = check_digest()
    dedupe_errors = check_dedupe()
    signal_errors = check_repo_signals(ROOT)
    entity_errors = check_entities(ROOT)
    new_errors = (reg_errors + prac_errors + src_errors + gh_errors
                  + pipe_errors + papers_errors + people_errors
                  + digest_errors + signal_errors + dedupe_errors
                  + entity_errors)
    for e in new_errors:
        print(f"ERROR   {e}")
    for w in reg_warnings + prac_warnings:
        print(f"warn    {w}")
    errors += new_errors
    warnings += prac_warnings

    print(f"\n{len(seen)} companies described, {len(errors)} errors, "
          f"{len(warnings) + len(reg_warnings)} warnings, "
          f"{len(lab_warnings)} dormant lab edges")
    if missing:
        print(f"{len(missing)} Technology Leaders holdings still undescribed:")
        for i, n in sorted(missing, key=lambda x: x[1])[:30]:
            print(f"   {i}  {n}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
