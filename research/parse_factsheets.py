"""Parse BIT Capital monthly factsheets into structured JSON.

Text extraction alone scrambles the multi-column tables, so this uses pypdf's
visitor API to keep x/y coordinates and reads columns off positions.
"""

import json
import re
from pathlib import Path

import pypdf

DOCS = Path(__file__).parent / "docs" / "funds"
OUT = Path(__file__).parent / "docs" / "bit_capital_funds.json"

FILES = {
    "BIT Global Technology Leaders": "Global_Technology_Leaders_R-I.pdf",
    "BIT Global Leaders": "Global_Leaders_R-I.pdf",
    "BIT Global Crypto Leaders": "Global_Crypto_Leaders_R-I.pdf",
    "BIT Aggressive Growth": "Aggressive_Growth_R-I.pdf",
    "BIT Crypto Opportunities": "Crypto_Opportunities_I-I.pdf",
    "BIT Defensive Growth": "Defensive_Growth_I-I.pdf",
    "BIT Global Multi Asset": "BIT_Global_Multi_Asset_R-II_EN_ultimo.pdf",
}

PERIODS = ["mtd", "ytd", "1y", "3y", "5y", "since_inception"]


def cells(path: Path, page: int) -> list[tuple[float, float, str]]:
    """Return (y, x, text) for every text run on a page."""
    items: list[tuple[float, float, str]] = []

    def visit(text, cm, tm, font, size):
        t = " ".join(text.split())
        if t:
            items.append((round(tm[5], 1), round(tm[4], 1), t))

    pypdf.PdfReader(str(path)).pages[page - 1].extract_text(visitor_text=visit)
    return items


def lines(items) -> list[list[tuple[float, str]]]:
    """Group text runs into visual lines, sorted top-to-bottom, left-to-right."""
    rows: dict[int, list[tuple[float, str]]] = {}
    for y, x, t in items:
        rows.setdefault(round(y / 3), []).append((x, t))
    return [sorted(rows[k]) for k in sorted(rows, reverse=True)]


def perf_table(rows) -> dict:
    """Read the performance & risk table, keyed by period."""
    out: dict[str, dict] = {}
    labels = {"Volatility p.a.": "volatility", "Sharpe ratio": "sharpe"}
    for i, row in enumerate(rows):
        text = " ".join(t for _, t in row)
        if text.startswith("MTD") and "Since inception" in text:
            # The performance row is the next line with >=5 numeric cells.
            for nxt in rows[i + 1 : i + 4]:
                vals = [t for _, t in nxt if re.fullmatch(r"-?[\d.,]+%|-", t)]
                if len(vals) >= 5:
                    out["cumulative"] = dict(zip(PERIODS, vals))
                    break
        for label, key in labels.items():
            if text.startswith(label):
                vals = [t for _, t in row if re.fullmatch(r"-?[\d.,]+%?|-|neg\.", t)]
                if len(vals) >= 5:
                    out[key] = dict(zip(PERIODS, vals))
    return out


def label_value(rows, wanted: str) -> str | None:
    """Find a right-hand value for a left-hand label on the same visual line."""
    for row in rows:
        text = " ".join(t for _, t in row)
        if wanted.lower() in text.lower():
            parts = [t for _, t in row]
            for j, p in enumerate(parts):
                if wanted.lower() in p.lower() and j + 1 < len(parts):
                    return parts[j + 1]
    return None


def band(items, xmin: float, xmax: float, data_xmin: float = 0) -> list[list[str]]:
    """Group runs inside an x-band into visual lines, top to bottom.

    Page 2 is laid out in three columns — portfolio tables on the left, the
    manager commentary in the middle, allocations on the right — so blocks are
    only unambiguous once restricted to their band.
    """
    rows: dict[int, list[tuple[float, str]]] = {}
    for y, x, t in items:
        if xmin <= x < xmax:
            rows.setdefault(round(y / 3), []).append((x, t))
    out = []
    for k in sorted(rows, reverse=True):
        cs = sorted(rows[k])
        # Section headers sit left of their data (the right-hand allocation
        # blocks are titled from the commentary column), so keep any run that
        # is either a header candidate or genuine data in the band.
        keep = [t for x, t in cs if x >= data_xmin or not re.search(r"\d", t)]
        if keep:
            out.append(keep)
    return out


def blocks(band_rows, headers: tuple[str, ...]) -> dict[str, list[dict]]:
    """Split a band into named sections and pull 'label / percentage' pairs."""
    out: dict[str, list[dict]] = {h: [] for h in headers}
    current = None
    for parts in band_rows:
        text = " ".join(parts).strip()
        hit = next((h for h in headers if text.startswith(h) or text == f"2 {h}"), None)
        if hit:
            current = hit
            continue
        if current is None:
            continue
        pcts = [p for p in parts if re.fullmatch(r"-?[\d.,]+%", p)]
        names = [p for p in parts if not re.fullmatch(r"-?[\d.,]+%|2", p)]
        if pcts and names:
            entry = {"name": names[0].strip(), "pct": pcts[0]}
            if len(names) > 1:
                entry["note"] = names[1].strip()
            out[current].append(entry)
    return out


def parse(name: str, filename: str) -> dict:
    path = DOCS / filename
    c1, c2 = cells(path, 1), cells(path, 2)
    p1, p2 = lines(c1), lines(c2)
    left = blocks(band(c2, 0, 200), ("Top 10", "Asset classes", "Currencies"))
    right = blocks(band(c2, 200, 600, data_xmin=340), ("Region Allocation", "Sector Allocation"))
    head = " ".join(t for row in p1[:12] for _, t in row)
    isin = re.search(r"ISIN:\s*([A-Z]{2}[A-Z0-9]{9}\d)", head)
    asof = re.search(r"as of (\d{2}\.\d{2}\.\d{4})", head)

    strategy = []
    grab = False
    for row in p1:
        text = " ".join(t for _, t in row)
        if text.startswith("Investment strategy"):
            grab = True
            continue
        if grab:
            if text.startswith("1Source") or text.startswith("Performance & Risk"):
                break
            if len(text) > 30:
                strategy.append(text)

    commentary = []
    grab = False
    for row in p2:
        text = " ".join(t for _, t in row)
        if text.startswith("Commentary"):
            grab = True
            continue
        if grab:
            if text.startswith(("Region Allocation", "Sector Allocation", "Top 10")):
                break
            if len(text) > 30:
                commentary.append(text)

    return {
        "name": name,
        "share_class_file": filename,
        "isin": isin.group(1) if isin else None,
        "as_of": asof.group(1) if asof else None,
        "fund_assets": label_value(p1, "Fund assets"),
        "inception": label_value(p1, "Inception date"),
        "fund_type": label_value(p1, "Fund type"),
        "sfdr": label_value(p1, "SFDR classification"),
        "management_fee": label_value(p1, "Management fee"),
        "ongoing_costs": label_value(p1, "Ongoing costs"),
        "n_positions": label_value(p2, "Number of positions"),
        "performance": perf_table(p1),
        "strategy": " ".join(strategy),
        "commentary": " ".join(commentary),
        "region": right["Region Allocation"],
        "sector": right["Sector Allocation"],
        "top10": left["Top 10"],
        "asset_classes": left["Asset classes"],
        "currencies": left["Currencies"],
    }


def main() -> None:
    funds = [parse(n, f) for n, f in FILES.items()]
    OUT.write_text(json.dumps(funds, indent=2))
    for f in funds:
        print(
            f"{f['name']:34s} {str(f['fund_assets']):12s} "
            f"pos={str(f['n_positions']):4s} top10={len(f['top10'])} "
            f"sect={len(f['sector'])} perf={'cumulative' in f['performance']}"
        )
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
