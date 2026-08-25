"""Derive cross-fund views from the parsed full portfolios.

Each fund reports on its own financial calendar, so the six statements carry six
different dates. Aggregating across them is still the best available answer to
"what does BIT own", but the mixed dates are a real caveat and are carried
through into the output rather than hidden.
"""

import json
import re
from pathlib import Path

DOCS = Path(__file__).parent / "docs"
OUT = DOCS / "portfolio_views.json"

SHORT = {
    "BIT Global Technology Leaders": "Technology Leaders",
    "BIT Global Leaders": "Global Leaders",
    "BIT Global Crypto Leaders": "Crypto Leaders",
    "BIT Aggressive Growth": "Aggressive Growth",
    "BIT Defensive Growth": "Defensive Growth",
    "BIT Global Multi Asset": "Multi Asset",
}


def tidy(name: str) -> str:
    """Restore word breaks lost by PDF extraction ('Amazon.comInc.' -> 'Amazon.com Inc.').

    The extractor drops spaces inside some issuer names, so breaks are put back
    at the case and digit transitions where they were removed.
    """
    for pattern in (r"(?<=[a-z.,])(?=[A-Z])", r"(?<=[A-Z])(?=[A-Z][a-z])", r"(?<=[0-9])(?=[A-Za-z])"):
        name = re.sub(pattern, " ", name)
    return re.sub(r"\s+", " ", name).strip()


def main() -> None:
    ports = json.loads((DOCS / "portfolios.json").read_text())
    funds = {f["name"]: f for f in json.loads((DOCS / "bit_capital_funds.json").read_text())}

    summary, positions = [], []
    for name, p in ports.items():
        summary.append(
            {
                "fund": SHORT[name],
                "fund_full": name,
                "as_of": p["as_of"],
                "as_of_iso": p["as_of_iso"],
                "n_holdings": p["n_holdings"],
                "coverage_pct": p["total_pct"],
                "value_eur": p["total_value_eur"],
                "reconciles": p["reconciles"],
                "source": p["file"],
                "factsheet_positions": funds[name]["n_positions"] if name in funds else None,
            }
        )
        for h in p["holdings"]:
            positions.append({**h, "name": tidy(h["name"]), "fund": SHORT[name],
                              "fund_full": name, "as_of": p["as_of"]})

    # Same security held by more than one fund, summed in EUR.
    by_isin: dict[str, dict] = {}
    for h in positions:
        a = by_isin.setdefault(
            h["isin"], {"isin": h["isin"], "name": h["name"], "value_eur": 0, "funds": []}
        )
        a["value_eur"] += h["value_eur"]
        a["funds"].append({"fund": h["fund"], "pct": h["pct_of_fund"]})
        if len(h["name"]) < len(a["name"]):
            a["name"] = h["name"]

    aggregate = sorted(by_isin.values(), key=lambda a: -a["value_eur"])
    for a in aggregate:
        a["n_funds"] = len(a["funds"])
        a["value_eur"] = round(a["value_eur"], 2)

    total = sum(a["value_eur"] for a in aggregate)
    for a in aggregate:
        a["pct_of_total"] = round(100 * a["value_eur"] / total, 3)

    data = {
        "summary": sorted(summary, key=lambda s: -s["value_eur"]),
        "positions": sorted(positions, key=lambda h: -h["value_eur"]),
        "aggregate": aggregate,
        "total_positions": len(positions),
        "total_unique": len(aggregate),
        "total_value_eur": round(total, 2),
        "date_range": [min(s["as_of_iso"] for s in summary), max(s["as_of_iso"] for s in summary)],
    }
    OUT.write_text(json.dumps(data, indent=2))

    print(f"{len(positions)} positions, {len(aggregate)} unique securities, "
          f"€{total/1e6:,.0f}m across {len(summary)} funds")
    print(f"statement dates span {data['date_range'][0]} to {data['date_range'][1]}\n")
    print("Largest exposures across all funds:")
    for a in aggregate[:15]:
        held = ", ".join(f["fund"] for f in a["funds"])
        print(f"  €{a['value_eur']/1e6:7,.1f}m  {a['pct_of_total']:5.2f}%  {a['name'][:34]:34s} [{held}]")
    multi = [a for a in aggregate if a["n_funds"] > 1]
    print(f"\n{len(multi)} securities are held by more than one fund")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
