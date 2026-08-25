"""Line up the three position sources for BIT Global Technology Leaders.

The same holding is named differently in each source ("Taiwan Semiconduct.Manufact."
in the annual report, "TSMC" on the factsheet, "TAIWAN SEMICONDUCTOR MANUFAC" in
the 13F), so matching runs through an explicit alias table rather than fuzzy
string comparison. Anything unmatched is reported, never silently dropped.
"""

import json
from pathlib import Path

DOCS = Path(__file__).parent / "docs"
OUT = DOCS / "date_comparison.json"

# canonical key -> (annual-report ISIN, factsheet label, 13F issuer as filed)
ALIASES = [
    ("Amazon", "US0231351067", "Amazon", "AMAZON COM INC"),
    ("IREN", "AU0000185993", "IREN", "IREN LIMITED"),
    ("Micron", "US5951121038", "Micron", "MICRON TECHNOLOGY INC"),
    ("Robinhood", "US7707001027", "Robinhood", "ROBINHOOD MKTS INC"),
    ("Navan", None, "Navan", "NAVAN INC"),
    ("TSMC", "US8740391003", "TSMC", "TAIWAN SEMICONDUCTOR MANUFAC"),
    ("AUTO1", "DE000A2LQ884", "Auto1", None),
    ("Infineon", None, "Infineon", None),
    ("Oscar Health", "US6877931096", "Oscar Health", "OSCAR HEALTH INC"),
    ("Hinge Health", "US4333131039", "Hinge Health", "HINGE HEALTH INC"),
]


def main() -> None:
    gtl = json.loads((DOCS / "gtl_portfolio_2025-12-31.json").read_text())
    funds = json.loads((DOCS / "bit_capital_funds.json").read_text())
    analysis = json.loads((DOCS / "analysis.json").read_text())

    fund = next(f for f in funds if f["name"] == "BIT Global Technology Leaders")
    dec = {h["isin"]: h for h in gtl["holdings"]}
    jul = {t["name"]: float(t["pct"].rstrip("%")) for t in fund["top10"]}
    q2 = {h["issuer"]: h for h in analysis["latest_holdings"]}

    rows = []
    for key, isin, fs_name, issuer in ALIASES:
        d = dec.get(isin) if isin else None
        rows.append(
            {
                "name": key,
                "isin": isin,
                "dec2025_pct": d["pct_of_fund"] if d else None,
                "dec2025_held": d is not None,
                "jul2026_pct": jul.get(fs_name),
                "q2_13f_pct": q2[issuer]["pct"] if issuer and issuer in q2 else None,
                "us_listed": issuer is not None,
            }
        )

    unmatched_fs = sorted(set(jul) - {a[2] for a in ALIASES})
    if unmatched_fs:
        raise SystemExit(f"factsheet names not in alias table: {unmatched_fs}")

    # Everything held at year-end that is NOT in the July top 10. The factsheet
    # alone cannot say whether these were sold or merely shrank out of the top 10
    # — but the 13F can, for US listings: a CUSIP absent from the Q2 filing is
    # held by no BIT fund at all. A US ISIN embeds its CUSIP as characters 3-11.
    q2_cusips = {h["cusip"] for h in analysis["latest_holdings"]}
    top10_isins = {a[1] for a in ALIASES if a[1]}
    tail = []
    for h in gtl["holdings"]:
        if h["isin"] in top10_isins:
            continue
        cusip = h["isin"][2:11] if h["isin"].startswith("US") else None
        if cusip is None:
            status = "unknown"  # non-US listing: the 13F is silent on it
        elif cusip in q2_cusips:
            status = "still_held"  # somewhere in BIT, not necessarily this fund
        else:
            status = "sold"  # held by no BIT fund at 30.06.2026
        tail.append(
            {
                "name": h["name"],
                "isin": h["isin"],
                "dec2025_pct": h["pct_of_fund"],
                "status": status,
            }
        )

    data = {
        "fund": "BIT Global Technology Leaders",
        "dec2025": {
            "as_of": "2025-12-31",
            "source": "Annual report, Vermögensaufstellung (pages 8-9)",
            "coverage": "complete: 34 positions = 97.43% of fund assets",
            "fund_assets_eur": 1088484031.17,
            "n_positions": gtl["n_holdings"],
        },
        "jul2026": {
            "as_of": "2026-07-31",
            "source": "Monthly factsheet, R-I share class",
            "coverage": "top 10 only, of 23 positions",
            "fund_assets": fund["fund_assets"],
            "n_positions": fund["n_positions"],
        },
        "q2_2026": {
            "as_of": "2026-06-30",
            "source": "Form 13F-HR",
            "coverage": "US-listed common stock, ALL BIT funds combined",
        },
        "rows": rows,
        "tail": sorted(tail, key=lambda t: -t["dec2025_pct"]),
    }
    OUT.write_text(json.dumps(data, indent=2))

    print(f"{'name':14s} {'Dec-25':>8s} {'Jul-26':>8s} {'13F Q2':>8s}")
    for r in rows:
        f = lambda v: "—" if v is None else f"{v:.2f}%"
        note = "" if r["us_listed"] else "  (not in 13F: non-US listing)"
        print(f"{r['name']:14s} {f(r['dec2025_pct']):>8s} {f(r['jul2026_pct']):>8s} {f(r['q2_13f_pct']):>8s}{note}")
    print(f"\n{len(tail)} positions held at year-end are outside the July top 10:")
    for s, label in (("sold", "confirmed sold (no BIT fund holds it)"),
                     ("still_held", "still held somewhere in BIT"),
                     ("unknown", "unknown (non-US listing, 13F silent)")):
        items = [t for t in tail if t["status"] == s]
        weight = sum(t["dec2025_pct"] for t in items)
        print(f"  {len(items):2d} positions, {weight:5.2f}% of year-end fund — {label}")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
