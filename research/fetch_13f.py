"""Download BIT Capital GmbH 13F-HR filings from SEC EDGAR into research/docs.

Writes one JSON file per filing (holdings + metadata) and a combined holdings.json.
"""

import json
import re
import time
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

CIK = "0002053305"
UA = "BITCap Case Study Research (contact: research@example.com)"
DOCS = Path(__file__).parent / "docs"
RAW = DOCS / "13f_raw"


def get(url: str, attempts: int = 5) -> bytes:
    """Fetch a URL with the User-Agent SEC requires, retrying with backoff on 503."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(attempts):
        time.sleep(0.5 * (2**attempt) if attempt else 0.4)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 503) or attempt == attempts - 1:
                raise
    raise RuntimeError("unreachable")


def filings() -> list[dict]:
    """Return all 13F-HR filings (accession, filing date, report period)."""
    data = json.loads(get(f"https://data.sec.gov/submissions/CIK{CIK}.json"))
    recent = data["filings"]["recent"]
    out = []
    for form, acc, filed, report, doc in zip(
        recent["form"],
        recent["accessionNumber"],
        recent["filingDate"],
        recent["reportDate"],
        recent["primaryDocument"],
    ):
        if form.startswith("13F-HR"):
            out.append(
                {"form": form, "accession": acc, "filed": filed, "period": report, "doc": doc}
            )
    return out


def info_table_xml(accession: str) -> bytes | None:
    """Return the information-table XML for a filing.

    Most filings expose it as a standalone .xml in the filing directory. Older
    backfilled ones only have it embedded in the combined submission .txt, so
    fall back to slicing it out of there.
    """
    acc_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(CIK)}/{acc_nodash}"
    index = json.loads(get(f"{base}/index.json"))
    names = [i["name"] for i in index["directory"]["item"]]
    for name in names:
        if name.endswith(".xml") and "primary_doc" not in name:
            return get(f"{base}/{name}")

    text = get(f"{base}/{accession}.txt").decode("utf-8", "replace")
    match = re.search(r"<(?:\w+:)?informationTable[\s\S]*?</(?:\w+:)?informationTable>", text)
    return match.group(0).encode() if match else None


def parse_info_table(xml: bytes) -> list[dict]:
    """Parse a 13F information table into holding dicts."""
    root = ET.fromstring(xml)
    ns = {"ns": re.match(r"\{(.*)\}", root.tag).group(1)} if root.tag.startswith("{") else {}
    prefix = "ns:" if ns else ""
    rows = []
    for e in root.findall(f"{prefix}infoTable", ns):

        def txt(path: str) -> str | None:
            node = e.find(prefix + path.replace("/", f"/{prefix}"), ns)
            return node.text if node is not None else None

        rows.append(
            {
                "issuer": txt("nameOfIssuer"),
                "class": txt("titleOfClass"),
                "cusip": txt("cusip"),
                "value_usd": int(txt("value") or 0),
                "shares": int(txt("shrsOrPrnAmt/sshPrnamt") or 0),
                "share_type": txt("shrsOrPrnAmt/sshPrnamtType"),
                "put_call": txt("putCall"),
                "discretion": txt("investmentDiscretion"),
            }
        )
    return rows


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    combined = []
    for f in filings():
        xml = info_table_xml(f["accession"])
        if not xml:
            print(f"  no info table: {f['accession']}")
            continue
        (RAW / f"{f['period']}_{f['accession']}.xml").write_bytes(xml)
        holdings = parse_info_table(xml)
        total = sum(h["value_usd"] for h in holdings)
        for h in holdings:
            h["pct_of_13f"] = round(100 * h["value_usd"] / total, 3) if total else 0
        combined.append({**f, "total_value_usd": total, "n_holdings": len(holdings), "holdings": holdings})
        print(f"{f['period']}  {f['form']:10s} {len(holdings):3d} holdings  ${total/1e6:,.1f}m")

    combined.sort(key=lambda c: (c["period"], c["filed"]))
    (DOCS / "bit_capital_13f.json").write_text(json.dumps(combined, indent=2))
    print(f"\n{len(combined)} filings -> {DOCS / 'bit_capital_13f.json'}")


if __name__ == "__main__":
    main()
