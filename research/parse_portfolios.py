"""Parse the full portfolio statement (Vermögensaufstellung) out of every BIT
fund report in research/docs/funds, and keep the freshest one per fund.

Each fund has its own financial year-end, so the freshest complete portfolio is
sometimes the annual report and sometimes the semi-annual one. Both share a
layout, and text runs carry no usable coordinates, so records are matched on
their fixed field order relative to the unit column:

    pct, value, price, currency, sells, buys, holding, UNIT, name…, ISIN

UNIT is "STK" for equities and a currency code for bonds, which is why the
matcher tests the shape of the whole record rather than looking for "STK".
"""

import json
import re
import unicodedata
from pathlib import Path

import pypdf

DOCS = Path(__file__).parent / "docs"
FUNDS = DOCS / "funds"
OUT = DOCS / "portfolios.json"

# fund name -> candidate report files, freshest wins
REPORTS = {
    "BIT Global Technology Leaders": ["hjb_GTL.pdf", "jb_GTL.pdf"],
    "BIT Global Leaders": ["hjb_GlobalLeaders.pdf", "jb_GlobalLeaders.pdf"],
    "BIT Global Crypto Leaders": ["hjb_CryptoLeaders.pdf", "jb_CryptoLeaders.pdf"],
    "BIT Aggressive Growth": ["hjb_AggressiveGrowth.pdf", "jb_AggressiveGrowth.pdf"],
    "BIT Defensive Growth": ["hjb_DefensiveGrowth.pdf", "jb_DefensiveGrowth.pdf"],
    "BIT Global Multi Asset": ["hjb_MultiAsset.pdf", "jb_MultiAsset.pdf"],
}

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
NUM_RE = re.compile(r"^-?[\d.]+,\d+$|^-?[\d.]+$")
# Equities quote a currency in this column; bonds quote price as a percentage
# of nominal and put a bare "%" there instead.
CCY_RE = re.compile(r"^([A-Z]{3}|%)$")
UNIT_RE = re.compile(r"^(STK|[A-Z]{3})$")


def num(s: str) -> float | None:
    """Convert a German-formatted number ('1.234,56') to a float."""
    return float(s.replace(".", "").replace(",", ".")) if NUM_RE.match(s) else None


def lines_of(path: Path) -> tuple[list[str], str | None]:
    """Return the portfolio-statement lines and the statement date."""
    reader = pypdf.PdfReader(str(path))
    out: list[str] = []
    as_of = None
    started = False
    for page in reader.pages:
        text = unicodedata.normalize("NFKC", page.extract_text() or "")
        if "VERMÖGENSAUFSTELLUNG" in text:
            started = True
            if as_of is None:
                m = re.search(r"VERMÖGENSAUFSTELLUNG ZUM ([\d.]+)", text)
                as_of = m.group(1) if m else None
        if not started:
            continue
        if re.search(r"ANGABEN FÜR INSTITUTIONELLE|WÄHREND DES BERICHTSZEITRAUMS", text):
            break
        out += [ln.strip() for ln in text.split("\n") if ln.strip()]
    return out, as_of


def record_at(lines: list[str], isin_idx: int) -> dict | None:
    """Match the fixed field order backwards from an ISIN line."""
    for j in range(isin_idx - 1, max(isin_idx - 14, 6), -1):
        if not UNIT_RE.match(lines[j]):
            continue
        tail = [num(lines[j - k]) for k in range(1, 4)]        # holding, buys, sells
        head = [num(lines[j - k]) for k in range(5, 8)]        # price, value, pct
        if None in tail or None in head or not CCY_RE.match(lines[j - 4]):
            continue
        holding, buys, sells = tail
        price, value, pct = head
        return {
            "name": " ".join(lines[j + 1 : isin_idx]).strip(),
            "isin": lines[isin_idx],
            "unit": lines[j],
            "currency": lines[j - 4],
            "shares": holding,
            "bought_in_period": buys,
            "sold_in_period": sells,
            "price": price,
            "value_eur": value,
            "pct_of_fund": pct,
        }
    return None


# Cash management, not a portfolio position: the Multi Asset fund parks cash in a
# money-market fund, which carries an ISIN and so parses like any other holding,
# but sits outside "Summe Wertpapiervermögen". Excluding it is what makes that
# fund reconcile. Attempting to classify holdings by their asset-class heading
# was tried and abandoned — the headings close the block above them, and the
# reversed reading order means records near a page break get attached to the
# wrong heading, silently mislabelling real equities as cash.
EXCLUDE_ISINS = {"IE00B3KF1681"}


def parse(path: Path) -> dict:
    lines, as_of = lines_of(path)
    holdings, seen = [], set()
    for i, line in enumerate(lines):
        if not ISIN_RE.match(line) or line in EXCLUDE_ISINS:
            continue
        rec = record_at(lines, i)
        # An ISIN can legitimately repeat (same bond, different tranche); key on
        # the ISIN plus its value so genuine duplicates are not collapsed.
        key = (line, rec["value_eur"]) if rec else None
        if rec and key not in seen:
            seen.add(key)
            holdings.append(rec)
    holdings.sort(key=lambda h: -h["pct_of_fund"])
    # The report states its own securities subtotal; parsing that and comparing
    # is the check that would catch this parser silently degrading.
    stated = None
    for i, line in enumerate(lines):
        if line.replace(" ", "") == "SummeWertpapiervermögen" and i >= 3:
            stated = {"pct": num(lines[i - 3]), "value_eur": num(lines[i - 2])}

    return {
        "stated_total": stated,
        "file": path.name,
        "as_of": as_of,
        "n_holdings": len(holdings),
        "total_pct": round(sum(h["pct_of_fund"] for h in holdings), 2),
        "total_value_eur": round(sum(h["value_eur"] for h in holdings), 2),
        "holdings": holdings,
    }


def to_iso(d: str) -> str:
    day, month, year = d.split(".")
    return f"{year}-{month}-{day}"


def main() -> None:
    out = {}
    for fund, files in REPORTS.items():
        best = None
        for name in files:
            path = FUNDS / name
            if not path.exists():
                continue
            got = parse(path)
            if got["as_of"] and (best is None or to_iso(got["as_of"]) > to_iso(best["as_of"])):
                best = got
        if best:
            best["fund"] = fund
            best["as_of_iso"] = to_iso(best["as_of"])
            out[fund] = best
            st = best["stated_total"]
            delta = abs(best["total_value_eur"] - st["value_eur"]) if st else None
            best["reconciles"] = delta is not None and delta < 1.0
            flag = "OK " if best["reconciles"] else "!! "
            print(f"{flag}{fund:34s} {best['as_of']}  {best['n_holdings']:3d} holdings  "
                  f"{best['total_pct']:6.2f}%  €{best['total_value_eur']/1e6:8,.1f}m  ({best['file']})")
            if not best["reconciles"]:
                print(f"     stated {st} vs parsed {best['total_value_eur']}")

    OUT.write_text(json.dumps(out, indent=2))
    total = sum(v["n_holdings"] for v in out.values())
    bad = [f for f, v in out.items() if not v["reconciles"]]
    print(f"\n{total} positions across {len(out)} funds -> {OUT}")
    print("all funds reconcile to their stated subtotal" if not bad else f"NOT RECONCILING: {bad}")


if __name__ == "__main__":
    main()
