"""Build a per-position quarterly history from the 13F filings, plus a price series.

Two series per position, and they come from different places on purpose:

  holding  — shares, value and weight per quarter, straight from the filings.
             Certain, because it is what BIT reported.
  price    — a vendor daily close where the ticker reconciles, otherwise the
             price implied by the filing itself (value_usd / shares). Daily is
             deliberate: quarter-end comparisons must land on the quarter-end.

The implied price is the honest fallback and also the verifier: a hand-mapped
ticker is only used once the vendor's close on each quarter-end agrees with what
BIT's own numbers imply. A mismatch drops the ticker rather than drawing a
plausible-looking wrong line.
"""

import json
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median

import yaml

from analyse import MATERIAL_PCT, load, split_and_merge

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
CACHE = DOCS / "prices"
OUT = DOCS / "position_history.json"
TICKERS = ROOT.parent / "config" / "tickers.yaml"

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{t}?period1={a}&period2={b}&interval=1d"
UA = "BIT Capital case study (research; neilaf4@gmail.com)"

# A ticker is accepted only if the vendor's quarter-end close sits within this
# fraction of the price the filing implies, across most checked quarters. Closes
# are fetched daily so the comparison lands on the quarter-end itself; 3% then
# absorbs stale-bar and rounding noise while still failing a different company.
# An earlier weekly fetch rejected six correctly-mapped names purely because a
# weekly bar closes several days after quarter-end — the tolerance was hiding a
# sampling bug, so it was fixed rather than widened. With daily closes a correct
# mapping lands on 0.0%, so this is tight on purpose.
TOLERANCE = 0.02

# Vendor closes are adjusted for splits retroactively; the filings are not. A
# 20:1 in Alphabet leaves every pre-split quarter 95% "wrong" on a mapping that
# is perfectly correct, so only quarters since the most recent split can be
# checked. Four is enough to make a coincidental match implausible.
VERIFY_QUARTERS = 4
# A forward split can never push the relative error to 1.0 — a 10:1 caps it at
# 0.9 and a 2:1 at 0.5 — so this sits below that ceiling and far above TOLERANCE.
# Dropping pre-split quarters is safe even for a genuinely wrong ticker, because
# the surviving recent quarters must still agree to within 2%.
SPLIT_RATIO = 0.4


CUSIP_RE = re.compile(r"^[A-Z0-9]{8}\d$")


def load_tickers() -> dict[str, str]:
    """Load the CUSIP->ticker map, refusing anything that is not a CUSIP.

    YAML will happily turn an unquoted CUSIP into an integer, and one with a
    leading zero into octal — 023135106 becomes 5028422. That corrupts a
    security identifier with no error and no visible symptom beyond a missing
    price line, so it is checked rather than trusted.
    """
    raw = yaml.safe_load(TICKERS.read_text())["tickers"]
    bad = [k for k in raw if not (isinstance(k, str) and CUSIP_RE.match(k))]
    if bad:
        raise ValueError(
            f"tickers.yaml: {len(bad)} keys are not CUSIPs — quote them. Got: {bad[:5]}")
    return raw


def fetch_prices(ticker: str, start: date, end: date) -> list[dict] | None:
    """Return [{date, close}] daily for a ticker, cached on disk.

    The cache is keyed on the requested range as well as the ticker. Keying on
    ticker alone silently returns a short cached series when a caller later asks
    for a longer window, which reads as "no price before 2023" rather than as a
    cache miss.

    Returns None if the symbol does not resolve or the fetch fails after retries.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{ticker}_{start:%Y%m%d}_{end:%Y%m%d}.json"
    if cached.exists():
        return json.loads(cached.read_text())

    url = CHART.format(
        t=ticker,
        a=int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp()),
        # Yahoo treats period2 as exclusive, so ask past the final quarter-end
        # or the most important close in the series is the one that is missing.
        b=int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()) + 7 * 86400,
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(6):
        # The vendor throttles aggressively and answers a burst with empty
        # payloads rather than errors, which silently drops correct tickers to
        # the implied-price fallback. Pace the first call too, not just retries.
        time.sleep(2.0 * (2**attempt) if attempt else 1.5)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code not in (429, 502, 503) or attempt == 5:
                return None
        except Exception:
            if attempt == 5:
                return None

    result = (payload.get("chart") or {}).get("result")
    if not result:
        return None
    stamps = result[0].get("timestamp") or []
    closes = (result[0].get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    series = [
        {"date": datetime.fromtimestamp(t, timezone.utc).date().isoformat(), "close": round(c, 4)}
        for t, c in zip(stamps, closes)
        if c is not None
    ]
    if not series:
        return None
    cached.write_text(json.dumps(series))
    return series


def downsample(series: list[dict], every: int = 5) -> list[dict]:
    """Thin a daily series to roughly weekly, always keeping the last point.

    Daily closes are what the verification needs; the chart does not, and the
    whole report is a single self-contained file.
    """
    kept = series[::every]
    if kept[-1] != series[-1]:
        kept.append(series[-1])
    return kept


def close_near(series: list[dict], when: str) -> float | None:
    """Last close on or before `when`, or None if the series starts after it."""
    prior = [p["close"] for p in series if p["date"] <= when]
    return prior[-1] if prior else None


def verify(series: list[dict], quarters: list[dict]) -> dict:
    """Check a vendor series against the prices the filings imply.

    Only recent quarters are checked: vendor closes are split-adjusted and the
    13F is not, so an old split would fail a name that is correctly mapped.
    """
    errors = []
    for q in [x for x in quarters if x["implied_price"]]:
        vendor = close_near(series, q["period"])
        if vendor:
            errors.append((q["period"], abs(vendor - q["implied_price"]) / q["implied_price"]))
    # Drop everything up to and including the most recent corporate action:
    # before it, vendor and filing are quoting different share bases. Only if
    # quarters survive it, though — a ticker that disagrees in *every* quarter
    # is a mismatch, not a split, and must be reported as one.
    split = [p for p, e in errors if e >= SPLIT_RATIO]
    after = [e for e in errors if split and e[0] > split[-1]]
    if after:
        errors = after
    else:
        split = []
    recent = errors[-VERIFY_QUARTERS:]
    if not recent:
        return {"ok": False, "reason": "no overlapping quarters to check"}

    worst_period, worst = max(recent, key=lambda e: e[1])
    ok = worst <= TOLERANCE
    return {
        "ok": ok,
        "worst_error_pct": round(100 * worst, 2),
        "quarters_checked": len(recent),
        # Surfaced rather than smoothed away: it tells the reader the price line
        # is split-adjusted while the share counts beside it are not.
        "split_before": split[-1] if split else None,
        "reason": None if ok else
                  f"vendor close differs from filing-implied price by {100*worst:.1f}% at {worst_period}",
    }


def histories(filings: list[dict]) -> dict[str, dict]:
    """Quarterly shares/value/weight/implied price for every CUSIP ever held."""
    periods = [f["period"] for f in filings]
    by_period = {f["period"]: {h["cusip"]: h for h in split_and_merge(f)[0]} for f in filings}

    out: dict[str, dict] = {}
    for cusip in {c for p in by_period.values() for c in p}:
        quarters = []
        for p in periods:
            h = by_period[p].get(cusip)
            quarters.append({
                "period": p,
                "shares": h["shares"] if h else 0,
                "value_usd": h["value_usd"] if h else 0,
                "pct": h["pct"] if h else 0.0,
                # Only meaningful while the position exists.
                "implied_price": round(h["value_usd"] / h["shares"], 4)
                                 if h and h["shares"] else None,
            })
        # A 13F row can carry a value with no share count. None exist today, but
        # falling back to any period that reports the CUSIP beats StopIteration.
        held = [q for q in quarters if q["shares"]] or [q for q in quarters
                                                        if q["period"] in by_period
                                                        and cusip in by_period[q["period"]]]
        name = next(by_period[q["period"]][cusip]["issuer"] for q in reversed(held))
        out[cusip] = {"cusip": cusip, "issuer": name, "quarters": quarters}
    return out


def main() -> None:
    filings = load()
    hist = histories(filings)
    tickers = load_tickers()
    current = {h["cusip"] for h in split_and_merge(filings[-1])[0]}

    first = date.fromisoformat(filings[0]["period"])
    last = date.fromisoformat(filings[-1]["period"])
    start = date(first.year - 1, first.month, first.day)

    accepted = rejected = fallback = 0
    for cusip, rec in hist.items():
        rec["price_source"] = "implied"
        rec["price"] = [
            {"date": q["period"], "close": q["implied_price"]}
            for q in rec["quarters"] if q["implied_price"]
        ]
        ticker = tickers.get(cusip)
        if not ticker or cusip not in current:
            if cusip in current:
                fallback += 1
            continue

        series = fetch_prices(ticker, start, last)
        if not series:
            rec["price_note"] = f"{ticker}: no vendor data returned"
            fallback += 1
            continue
        check = verify(series, rec["quarters"])
        if check["ok"]:
            # The chart line is downsampled, but any quarter-end comparison must
            # use the exact daily close: a thinned series can be a week stale,
            # which is several percent on these names and silently corrupts
            # share-change and counterfactual arithmetic.
            rec.update(
                ticker=ticker, price_source="vendor",
                price=downsample(series), price_check=check,
                # Kept for every quarter, not just held ones: how far a name ran
                # *before* they bought it is the point of the entry analysis.
                quarter_close={q["period"]: close_near(series, q["period"])
                               for q in rec["quarters"]
                               if close_near(series, q["period"]) is not None},
            )
            accepted += 1
        else:
            rec["price_note"] = f"{ticker} rejected — {check['reason']}"
            rejected += 1
            print(f"  REJECT {ticker:6s} {rec['issuer'][:34]:34s} {check['reason']}")

    # The report is one self-contained file, so ship only positions a reader can
    # reach: held today, or once large enough to matter. The rest stay in the
    # filings and can be re-derived by re-running this script.
    keep = {
        c for c, r in hist.items()
        if c in current or max(q["pct"] for q in r["quarters"]) >= MATERIAL_PCT
    }
    payload = {
        "periods": [f["period"] for f in filings],
        "price_tolerance_pct": TOLERANCE * 100,
        "positions": {c: hist[c] for c in keep},
    }
    OUT.write_text(json.dumps(payload))
    print(f"\n{len(keep)} positions shipped of {len(hist)} ever held, "
          f"across {len(filings)} quarters")
    print(f"price series: {accepted} vendor-verified, {rejected} rejected, "
          f"{fallback} current positions on implied price only")
    print(f"-> {OUT} ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
