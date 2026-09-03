"""Derive portfolio analytics from BIT Capital's 13F filing history.

The 13F covers US-listed long equity across all BIT vehicles combined, so it is
a partial but consistent view: good for trend, concentration and turnover,
blind to non-US listings, crypto and cash.
"""

import json
from pathlib import Path

DOCS = Path(__file__).parent / "docs"
OUT = DOCS / "analysis.json"


def load() -> list[dict]:
    """Return 13F filings oldest-first, one per period (amendments win)."""
    raw = json.loads((DOCS / "bit_capital_13f.json").read_text())
    by_period: dict[str, dict] = {}
    for f in raw:
        prev = by_period.get(f["period"])
        if prev is None or f["filed"] >= prev["filed"]:
            by_period[f["period"]] = f
    return [by_period[p] for p in sorted(by_period)]


def split_and_merge(filing: dict) -> tuple[list[dict], list[dict]]:
    """Split a filing into common-stock and option rows, merged by CUSIP.

    13F rows are per class and per derivative type, so the same issuer can
    appear several times. Options carry notional exposure rather than capital
    deployed, so they are kept apart rather than summed into position sizes.
    """
    buckets: dict[tuple, dict] = {}
    for h in filing["holdings"]:
        kind = "option" if h["put_call"] else "common"
        key = (kind, h["cusip"])
        agg = buckets.setdefault(
            key,
            {"issuer": h["issuer"], "cusip": h["cusip"], "kind": kind,
             "put_call": h["put_call"], "value_usd": 0, "shares": 0},
        )
        agg["value_usd"] += h["value_usd"]
        agg["shares"] += h["shares"]

    common = [v for k, v in buckets.items() if k[0] == "common"]
    options = [v for k, v in buckets.items() if k[0] == "option"]
    total = sum(h["value_usd"] for h in common) or 1
    for h in common:
        h["pct"] = round(100 * h["value_usd"] / total, 3)
    return (
        sorted(common, key=lambda h: -h["value_usd"]),
        sorted(options, key=lambda h: -h["value_usd"]),
    )


def concentration(holdings: list[dict], n: int) -> float:
    """Share of portfolio value held in the largest n positions."""
    top = sorted(holdings, key=lambda h: -h["value_usd"])[:n]
    total = sum(h["value_usd"] for h in holdings)
    return round(100 * sum(h["value_usd"] for h in top) / total, 1) if total else 0.0


def turnover(prev: dict, cur: dict) -> dict:
    """Compare two consecutive quarters of common-stock positions by CUSIP."""
    p = {h["cusip"]: h for h in split_and_merge(prev)[0]}
    c = {h["cusip"]: h for h in split_and_merge(cur)[0]}
    entries = sorted(set(c) - set(p), key=lambda k: -c[k]["value_usd"])
    exits = sorted(set(p) - set(c), key=lambda k: -p[k]["value_usd"])
    changed = sum(c[k]["value_usd"] for k in entries) + sum(p[k]["value_usd"] for k in exits)
    base = max(sum(h["value_usd"] for h in p.values()), 1)
    return {
        "period": cur["period"],
        "entries": [{"issuer": c[k]["issuer"], "value_usd": c[k]["value_usd"], "pct": c[k]["pct"]} for k in entries[:12]],
        "exits": [{"issuer": p[k]["issuer"], "value_usd": p[k]["value_usd"], "pct": p[k]["pct"]} for k in exits[:12]],
        "n_entries": len(entries),
        "n_exits": len(exits),
        "name_turnover_pct": round(100 * changed / base, 1),
    }


# A position can persist at a trivial size — Micron sat at 0.04% of the book
# through 2022 — so a raw presence streak reads as conviction when it is a stub.
# Streaks are therefore reported at a materiality floor as well as raw.
MATERIAL_PCT = 0.5


def survival(filings: list[dict]) -> list[dict]:
    """Consecutive quarters each current position has been held, two ways.

    `quarters_held` counts presence at any size; `quarters_material` counts only
    quarters at or above MATERIAL_PCT of 13F value. The gap between them is the
    tell: a large gap means the name was carried as a stub before conviction.
    """
    periods = [f["period"] for f in filings]
    held, weights = {}, {}
    for f in filings:
        common = split_and_merge(f)[0]
        held[f["period"]] = {h["cusip"] for h in common}
        weights[f["period"]] = {h["cusip"]: h["pct"] for h in common}

    out = []
    for h in split_and_merge(filings[-1])[0]:
        streak = material = 0
        for p in reversed(periods):
            if h["cusip"] in held[p]:
                streak += 1
            else:
                break
        for p in reversed(periods):
            if weights[p].get(h["cusip"], 0) >= MATERIAL_PCT:
                material += 1
            else:
                break
        first = next((p for p in periods if h["cusip"] in held[p]), None)
        out.append(
            {
                "issuer": h["issuer"],
                "pct": h["pct"],
                "quarters_held": streak,
                "quarters_material": material,
                "first_seen": first,
            }
        )
    return out


def main() -> None:
    filings = load()
    latest = filings[-1]

    series = []
    for f in filings:
        common, options = split_and_merge(f)
        series.append(
            {
                "period": f["period"],
                "value_usd": sum(h["value_usd"] for h in common),
                "options_usd": sum(h["value_usd"] for h in options),
                "n_holdings": len(common),
                "top10_pct": concentration(common, 10),
            }
        )

    quarters = [turnover(a, b) for a, b in zip(filings, filings[1:])]

    top, options = split_and_merge(latest)
    prev = {h["cusip"]: h for h in split_and_merge(filings[-2])[0]}
    for h in top:
        before = prev.get(h["cusip"])
        h["share_change_pct"] = (
            round(100 * (h["shares"] - before["shares"]) / before["shares"], 1)
            if before and before["shares"]
            else None
        )

    data = {
        "latest_period": latest["period"],
        "latest_filed": latest["filed"],
        "latest_total_usd": series[-1]["value_usd"],
        "latest_options_usd": series[-1]["options_usd"],
        "latest_n_holdings": series[-1]["n_holdings"],
        "series": series,
        "latest_holdings": top,
        "latest_options": options,
        "quarters": quarters,
        "survival": survival(filings),
    }
    OUT.write_text(json.dumps(data, indent=2))

    print(f"Latest 13F {latest['period']} (filed {latest['filed']}): "
          f"${data['latest_total_usd']/1e6:,.0f}m common stock across "
          f"{data['latest_n_holdings']} US-listed positions, plus "
          f"${data['latest_options_usd']/1e6:,.0f}m notional in options")
    print(f"Top 10 = {concentration(top, 10)}% of common-stock value\n")
    print("Largest positions:")
    for h in top[:15]:
        chg = f"{h['share_change_pct']:+.0f}%" if h["share_change_pct"] is not None else "NEW"
        print(f"  {h['pct']:5.2f}%  {h['issuer'][:38]:38s} ${h['value_usd']/1e6:7,.0f}m  shares {chg}")
    print("\nLast quarter's activity:")
    q = quarters[-1]
    print(f"  {q['n_entries']} new, {q['n_exits']} exited, name turnover {q['name_turnover_pct']}%")
    print("  new:   " + ", ".join(e["issuer"][:22] for e in q["entries"][:6]))
    print("  exits: " + ", ".join(e["issuer"][:22] for e in q["exits"][:6]))
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
