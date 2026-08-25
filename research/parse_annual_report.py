"""Parse the full portfolio statement (Vermögensaufstellung) from the
BIT Global Technology Leaders annual report into structured JSON.

This is the disclosure that lists every position with its weight, rather than
just a top 10. Text runs carry no usable coordinates in this PDF, so the parser
works off the fixed field order of each record instead:

    pct, market value, price, currency, sells, buys, holding, "STK", name…, ISIN
"""

import json
import re
import unicodedata
from pathlib import Path

import pypdf

PDF = Path(__file__).parent / "docs" / "funds" / "GTL_annual_report.pdf"
OUT = Path(__file__).parent / "docs" / "gtl_portfolio_2025-12-31.json"

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
NUM_RE = re.compile(r"^-?[\d.]+,\d+$|^-?[\d.]+$")


def num(s: str) -> float | None:
    """Convert a German-formatted number ('1.234,56') to a float."""
    if not NUM_RE.match(s):
        return None
    return float(s.replace(".", "").replace(",", "."))


def report_lines() -> list[str]:
    """Return the text lines of the portfolio-statement pages."""
    reader = pypdf.PdfReader(str(PDF))
    out: list[str] = []
    started = False
    for page in reader.pages:
        text = unicodedata.normalize("NFKC", page.extract_text() or "")
        if "VERMÖGENSAUFSTELLUNG" in text:
            started = True
        if not started:
            continue
        if "ANGABEN FÜR INSTITUTIONELLE" in text:
            break
        out += [ln.strip() for ln in text.split("\n") if ln.strip()]
    return out


def parse() -> dict:
    lines = report_lines()
    holdings = []
    for i, line in enumerate(lines):
        if not ISIN_RE.match(line):
            continue
        # Walk back to the "STK" marker that opens this record.
        stk = next((j for j in range(i - 1, max(i - 12, -1), -1) if lines[j] == "STK"), None)
        if stk is None or stk < 7:
            continue
        fields = [num(lines[stk - k]) for k in range(1, 8)]
        holding, buys, sells, _, price, value, pct = fields
        currency = lines[stk - 4]
        if pct is None or value is None or not re.fullmatch(r"[A-Z]{3}", currency):
            continue
        holdings.append(
            {
                "name": " ".join(lines[stk + 1 : i]).strip(),
                "isin": line,
                "currency": currency,
                "shares": holding,
                "bought_in_period": buys,
                "sold_in_period": sells,
                "price": price,
                "value_eur": value,
                "pct_of_fund": pct,
            }
        )

    total_pct = round(sum(h["pct_of_fund"] for h in holdings), 2)
    total_eur = round(sum(h["value_eur"] for h in holdings), 2)
    holdings.sort(key=lambda h: -h["pct_of_fund"])
    return {
        "fund": "BIT Global Technology Leaders (formerly BIT Global Internet Leaders 30)",
        "as_of": "2025-12-31",
        "source": "Jahresbericht zum 31.12.2025, Vermögensaufstellung",
        "n_holdings": len(holdings),
        "total_pct_of_fund": total_pct,
        "total_value_eur": total_eur,
        "holdings": holdings,
    }


def main() -> None:
    data = parse()
    OUT.write_text(json.dumps(data, indent=2))
    print(f"{data['n_holdings']} holdings, {data['total_pct_of_fund']}% of fund assets")
    for h in data["holdings"]:
        print(f"  {h['pct_of_fund']:6.2f}%  {h['name'][:46]:46s} {h['isin']}  {h['currency']}")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
