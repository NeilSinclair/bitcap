"""Recompute scores from already-cached tags, without calling the model.

Scores are a pure function of the tags, so a change to the scoring *rule* costs
nothing to test: the v1 classifications are on disk and can be rescored under
the v2 rule for free. Only a change to the *vocabulary* needs new model calls.

This exists because that distinction was missed once already — a full 375-item
re-run was launched to test a formula change that required no calls at all.

It also reports which items genuinely do need re-classification, so the paid
run can be restricted to them.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent))
DOCS = ROOT / "research" / "docs"
V1_CACHE = DOCS / "announcement_scores"
SCORING = ROOT / "config" / "scoring.yaml"
ARTICLES = DOCS / "announcements.json"
NEEDS = DOCS / "needs_reclassify.json"
V2_CACHE = DOCS / "announcement_scores" / "v2"
OUT = DOCS / "scored_announcements_v2.json"

# v1 event ids that still mean the same thing under v2. Anything absent from
# this map cannot be rescored honestly and must be re-classified.
RENAMED = {
    "capability_result": "capability_result",
    "pricing_change": "pricing_change",
    "compute_commitment": "compute_commitment",
    "open_weights": "open_weights",
    "research_result": "research_result",
    "product_launch": "product_launch",
    "developer_tooling": "developer_tooling",
    "enterprise_partnership": "enterprise_partnership",
    "personnel": "personnel",
    "corporate_other": "other",  # renamed only; still weight 0
}

# Mechanisms renamed in config, mapped forward from cached tags. `invert` is set
# when the rename reversed the polarity of the id, so the cached sign no longer
# means what it did. Migrating here rather than editing the cache keeps the cache
# an honest record of what the model actually returned.
MECHANISM_RENAMED = {
    "ai_capex_cycle_turns": {"id": "ai_capex_investments", "invert": True},
}

FLIP = {"positive": "negative", "negative": "positive", "mixed": "mixed"}


def migrate_renamed(record: dict) -> list[str]:
    """Rewrite cached mechanism tags whose id changed in config.

    Args:
        record: A classification, modified in place.

    Returns:
        Human-readable descriptions of the migrations applied.
    """
    applied = []
    for tag in record.get("mechanisms", []):
        rule = MECHANISM_RENAMED.get(tag["id"])
        if not rule:
            continue
        was = f"{tag['id']}/{tag['sign']}"
        tag["id"] = rule["id"]
        if rule["invert"]:
            tag["sign"] = FLIP[tag["sign"]]
        applied.append(f"{was} -> {tag['id']}/{tag['sign']}")
    return applied


# v1 types with no honest v2 equivalent, and why.
AMBIGUOUS = {
    "model_release": "split into frontier_model_release / incremental_model_release",
    "safety_policy": "may be regulatory_action (action taken ON the lab) instead",
}

# Words suggesting an item could newly fire one of the two added mechanisms.
NEW_MECHANISM_HINTS = re.compile(
    r"\b(chip|silicon|asic|accelerator|tpu|inference chip|custom|broadcom|cerebras"
    r"|s-1|ipo|funding|raise[sd]?|series [a-z]|valuation|invest(ment|s)?"
    r"|acquisition|acquires?|acquire)\b",
    re.I,
)


def load_v1() -> dict:
    """Load the cached v1 classifications with their v1 scores, keyed by URL.

    The cache holds tags only; the score was computed at run time and lives in
    the run output, so both are needed to show a before and after.

    Returns:
        Mapping of url -> v1 result dict including its v1 score.
    """
    articles = json.loads(ARTICLES.read_text())
    prior = {
        a["url"]: a
        for a in json.loads((DOCS / "scored_announcements.json").read_text())["scored"]
    }
    out = {}
    for article in articles:
        key = re.sub(r"[^A-Za-z0-9]+", "_", article["url"])[:140] + ".json"
        path = V1_CACHE / key
        if not path.exists():
            continue
        record = {**article, **json.loads(path.read_text())}
        record["score"] = prior.get(article["url"], {}).get("score", 0.0)
        out[article["url"]] = record
    return out


def needs_reclassify(record: dict) -> str | None:
    """Say whether an item must be re-classified, and why.

    Args:
        record: A v1 result merged with its article.

    Returns:
        Reason string, or None if the cached tags can be rescored as they are.
    """
    event = record["event_type"]
    if event in AMBIGUOUS:
        return AMBIGUOUS[event]
    if event not in RENAMED:
        return f"unknown v1 event type {event!r}"
    if NEW_MECHANISM_HINTS.search(f"{record['title']} {record.get('summary', '')}"):
        return "may fire custom_silicon_substitution or lab_capital_access"
    return None


def strip_retired(record: dict, mech_ids: set[str], cat_ids: set[str]) -> list[str]:
    """Drop tags naming ids the config no longer defines.

    Cached classifications outlive the vocabulary that produced them. When a
    mechanism is retired, every cached result still carries it, and rebuilding
    the register from cache would silently reintroduce a transmission path that
    no longer exists.

    Args:
        record: A classification, modified in place.
        mech_ids: Currently valid mechanism ids.
        cat_ids: Currently valid category ids.

    Returns:
        The retired ids that were removed.
    """
    removed = []
    for key, valid in (("mechanisms", mech_ids), ("categories", cat_ids)):
        kept = []
        for tag in record.get(key, []):
            if tag["id"] in valid:
                kept.append(tag)
            else:
                removed.append(f"{key}:{tag['id']}")
        record[key] = kept
    return removed


def main() -> None:
    """Build the v2 register from whatever is available, and list what is not.

    A freshly re-classified v2 result always wins. Otherwise the v1 tags are
    rescored under the v2 rule, which is exact for event types that only
    changed name. Items that are neither are reported as still needing calls.
    Running this again after a targeted re-classification simply upgrades those
    items in place, so it is safe to run at any point.
    """
    rules = yaml.safe_load(SCORING.read_text())
    from score_announcements import score_of, vocabularies

    _, _, mech_ids, cat_ids = vocabularies()
    retired: list[str] = []
    migrated: list[str] = []

    v1 = load_v1()
    print(f"{len(v1)} cached v1 classifications")

    fresh = {}
    if V2_CACHE.exists():
        for article in json.loads(ARTICLES.read_text()):
            key = re.sub(r"[^A-Za-z0-9]+", "_", article["url"])[:140] + ".json"
            path = V2_CACHE / key
            if path.exists():
                fresh[article["url"]] = {**article, **json.loads(path.read_text())}
    print(f"{len(fresh)} re-classified under v2\n")

    rescored, blocked, register = [], [], []
    for url, record in v1.items():
        if url in fresh:
            result = fresh[url]
            migrated += migrate_renamed(result)
            retired += strip_retired(result, mech_ids, cat_ids)
            result["score"], result["band"] = score_of(result, rules)
            result["provenance"] = "reclassified_v2"
            register.append(result)
            continue

        reason = needs_reclassify(record)
        if reason:
            blocked.append((record, reason))
            continue

        mapped = {**record, "event_type": RENAMED[record["event_type"]]}
        migrated += migrate_renamed(mapped)
        retired += strip_retired(mapped, mech_ids, cat_ids)
        score, band = score_of(mapped, rules)
        mapped["score"], mapped["band"] = score, band
        mapped["provenance"] = "rescored_v1_tags"
        register.append(mapped)
        rescored.append((record, score, band))

    print(f"rescorable for free : {len(rescored)}")
    print(f"needs model calls   : {len(blocked)}\n")

    moved = [(r, s, b) for r, s, b in rescored if abs(s - r["score"]) >= 0.05]
    print(f"changed score: {len(moved)} of {len(rescored)}")
    print("\n=== BIGGEST RISES (free rescore only) ===")
    for r, s, b in sorted(moved, key=lambda m: -(m[1] - m[0]["score"]))[:10]:
        print(f"  {r['score']:>5} -> {s:<6} [{r['lab'][:4]}] {r['title'][:52]}")
        print(f"         {r['event_type']}")
    print("\n=== BIGGEST FALLS ===")
    for r, s, b in sorted(moved, key=lambda m: m[1] - m[0]["score"])[:6]:
        print(f"  {r['score']:>5} -> {s:<6} [{r['lab'][:4]}] {r['title'][:52]}")
        print(f"         {r['event_type']}")

    print("\n=== WHY THE REST NEED CALLS ===")
    counts = {}
    for _, reason in blocked:
        counts[reason] = counts.get(reason, 0) + 1
    for reason, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>4}  {reason}")

    NEEDS.write_text(json.dumps([r["url"] for r, _ in blocked], indent=2))
    print(f"\n{len(blocked)} urls -> {NEEDS.name}")

    if migrated:
        from collections import Counter

        print("\n=== TAGS MIGRATED: id renamed in config ===")
        for tag, n in Counter(migrated).most_common():
            print(f"  {n:>4}  {tag}")

    if retired:
        from collections import Counter

        print("\n=== TAGS DROPPED: id no longer in config ===")
        for tag, n in Counter(retired).most_common():
            print(f"  {n:>4}  {tag}")

    register.sort(key=lambda a: -a["score"])
    OUT.write_text(json.dumps({"scored": register, "failures": []}, indent=2))
    print(f"{len(register)} items in the v2 register -> {OUT.name}")
    if blocked:
        print(f"  INCOMPLETE: {len(blocked)} items still carry v1 classifications")


if __name__ == "__main__":
    main()
