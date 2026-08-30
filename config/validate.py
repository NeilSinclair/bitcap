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


def main() -> int:
    mech = yaml.safe_load((ROOT / "mechanisms.yaml").read_text())
    comp = yaml.safe_load((ROOT / "companies.yaml").read_text())
    views = json.loads(PORTFOLIOS.read_text())

    valid_ids = {m["id"] for m in mech["mechanisms"]}
    enums = mech["enums"]
    held = {p["isin"]: p for p in views["positions"]}
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

    reg_errors, reg_warnings = check_registry(ROOT, views)
    for e in reg_errors:
        print(f"ERROR   {e}")
    for w in reg_warnings:
        print(f"warn    {w}")
    errors += reg_errors

    print(f"\n{len(seen)} companies described, {len(errors)} errors, "
          f"{len(warnings) + len(reg_warnings)} warnings")
    if missing:
        print(f"{len(missing)} Technology Leaders holdings still undescribed:")
        for i, n in sorted(missing, key=lambda x: x[1])[:30]:
            print(f"   {i}  {n}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
