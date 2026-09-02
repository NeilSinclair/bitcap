"""Validate the config files against each other and against the portfolio.

Run before anything consumes them. Catches the failure modes that would otherwise
degrade silently: a mechanism or category id that does not exist, an ISIN that the
fund does not hold, a holding with no entry, a category with no members, and a claim
with neither a source nor an explicit `unverified` note.

Scope is BIT Global Technology Leaders alone (docs/decisions.md D8).
"""

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
PORTFOLIOS = ROOT.parent / "research" / "docs" / "portfolio_views.json"


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

    seen = set()
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
    for e in reg_errors + prac_errors:
        print(f"ERROR   {e}")
    for w in reg_warnings + prac_warnings:
        print(f"warn    {w}")
    errors += reg_errors + prac_errors
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
