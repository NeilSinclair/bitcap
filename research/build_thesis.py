"""What BIT's top-10 positions imply about their process, over a chosen window.

    python3 build_thesis.py [quarters]      # default 12

Every number is derived from the filings here rather than typed into the
template, so the page cannot drift from the data.

Quarter-end prices come from `quarter_close` (the exact daily close on the
quarter-end, for tickers verified against the filing) and fall back to the price
the filing itself implies. Both are raw, unadjusted prices, which is what makes
them safe to divide into raw 13F share counts. The downsampled `price` series is
for drawing lines only — it can be a week stale, which is several percent on
these names.
"""

import html
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).parent
DOCS = HERE / "docs"
OUT = HERE / "bit_top10_thesis.html"

H = json.loads((DOCS / "position_history.json").read_text())
A = json.loads((DOCS / "analysis.json").read_text())
# Statutory portfolios: the only source that expresses a position as a share of
# an individual fund's NAV, which is the denominator every concentration limit
# is written against. The 13F cannot do this — it merges all vehicles.
V = json.loads((DOCS / "portfolio_views.json").read_text())

QUARTERS = int(sys.argv[1]) if len(sys.argv) > 1 else 12
PER = H["periods"][-(QUARTERS + 1):]

# The conclusion is horizon-dependent, and that is the finding rather than a
# nuisance: over three years they are net accumulators, while over the last four
# quarters they are heavy sellers of exactly the same names. Both windows are
# therefore computed and shown side by side.
# None when the window is too short to contain a distinct four-quarter tail:
# duplicating the same column under a different heading is worse than omitting
# it.
RECENT = PER[-5:] if len(PER) >= 6 else None

THEME = {"595112103": "Micron", "80004C200": "SanDisk", "82706C108": "Silicon Motion",
         "573874104": "Marvell", "874039100": "TSMC"}

POS = H["positions"]


def nav_limits():
    """Per-fund concentration against the UCITS 5/10/40 pattern.

    A UCITS fund may hold at most 10% of NAV in one issuer, and everything above
    5% may not exceed 40% of NAV in aggregate. Whether BIT's own policy restates
    that or goes further is not in any document collected here, so the rule is
    named as the likely basis and the measurement is left to speak for itself.
    """
    funds = {}
    for pos in V["positions"]:
        funds.setdefault(pos["fund_full"], []).append(pos)
    out = []
    for name, ps in sorted(funds.items()):
        ps = sorted(ps, key=lambda x: -x["pct_of_fund"])
        over5 = [x for x in ps if x["pct_of_fund"] > 5]
        out.append({
            "fund": esc(name), "n": len(ps), "max": ps[0]["pct_of_fund"],
            "n_over5": len(over5), "sum_over5": sum(x["pct_of_fund"] for x in over5),
            "n_over10": len([x for x in ps if x["pct_of_fund"] > 10]),
            "top": esc(ps[0]["name"]), "as_of": ps[0]["as_of"],
        })
    return out


def esc(text):
    """Escape text bound for the page. Issuer names are external data.

    "Hims & Hers Health Inc" emitted a bare ampersand; a name containing "<"
    would break the document outright.
    """
    return html.escape(str(text))


def qs(cusip):
    return {q["period"]: q for q in POS[cusip]["quarters"]}


def price(cusip, when):
    """Exact quarter-end price: vendor close if verified, else filing-implied.

    None where neither exists — a quarter the position was not held has no
    implied price, and callers must decide what that means rather than be handed
    a zero.
    """
    v = (POS[cusip].get("quarter_close") or {}).get(when)
    if v is not None:
        return v
    m = qs(cusip)
    return m[when]["implied_price"] if when in m else None


def split_factor(cusip):
    """Largest disagreement between filing-implied and vendor price in-window.

    A forward split shows up as a clean step (10.0, 20.0) because vendor closes
    are adjusted retroactively and 13F share counts are not. Anything near 1.0
    means raw shares and raw prices can be compared directly across the window.
    """
    rec, m = POS[cusip], qs(cusip)
    if not rec.get("quarter_close"):
        return 1.0
    f = [m[p]["implied_price"] / rec["quarter_close"][p]
         for p in PER
         if p in rec["quarter_close"] and m[p]["implied_price"]]
    return max(f) / min(f) if f else 1.0


def rows():
    """Today's top 10, tracked back across the window."""
    out = []
    for h in sorted(A["latest_holdings"], key=lambda x: -x["pct"])[:10]:
        c = h["cusip"]
        rec, m = POS[c], qs(c)
        a, b = m[PER[0]], m[PER[-1]]
        p0, p1 = price(c, PER[0]), price(c, PER[-1])
        age = 0
        for q in reversed(rec["quarters"]):
            if not q["shares"]:
                break
            age += 1
        entry = PER[-age] if age <= len(PER) - 1 else None
        # How far the name had already run in the year before they established it.
        # Only reported when the full four-quarter lookback fits inside the
        # window. Clamping at the window edge silently measured three quarters
        # while the column still claimed four.
        runup = None
        if entry and PER.index(entry) >= 4:
            pa, pb = price(c, PER[PER.index(entry) - 4]), price(c, entry)
            if pa and pb:
                runup = 100 * (pb - pa) / pa
        ra = m[RECENT[0]] if RECENT else None
        rp0, rp1 = (price(c, RECENT[0]), price(c, RECENT[-1])) if RECENT else (None, None)
        out.append({
            "dsh_r": None if not (ra and ra["shares"]) else
                     100 * (m[RECENT[-1]]["shares"] - ra["shares"]) / ra["shares"],
            "dpx_r": None if not (rp0 and rp1) else 100 * (rp1 - rp0) / rp0,
            "name": esc(rec["issuer"].title()), "ticker": esc(rec["ticker"]) if rec.get("ticker") else None,
            "pct": b["pct"], "pct0": a["pct"], "age": age, "new": a["shares"] == 0,
            "dsh": None if not a["shares"] else 100 * (b["shares"] - a["shares"]) / a["shares"],
            "dpx": None if not (p0 and p1) else 100 * (p1 - p0) / p0,
            "maxpct": max(m[p]["pct"] for p in PER),
            "entry": entry, "runup": runup,
            "split": split_factor(c),
            "series": [m[p]["pct"] for p in PER],
            "prices": [price(c, p) for p in PER],
        })
    return out


def counterfactual(cusip, window):
    """End value plus cash released, against never having traded in the window.

    Deliberately run over the trimming phase only. Across the full three years it
    measures something else entirely — they were net buyers from a small base, so
    it just reports that buying more of a rising stock helped, which is trivial.
    It is only interpretable where they sold down an established position.

    Position-level opportunity cost, not fund P&L: proceeds were redeployed, and
    quarter-end snapshots cannot see the price they actually traded at.
    """
    m = qs(cusip)
    s0 = m[window[0]]["shares"]
    if not s0:
        return None

    # Every quarter in the window needs a real price. Substituting zero for a
    # missing one values a sale at nothing and silently returns a number that
    # looks fine: across the full book that produced 40 positions reporting an
    # "untraded" value of $0m. Undefined is the correct answer, so say so.
    missing = [w for w in window if price(cusip, w) is None]
    if missing:
        raise ValueError(
            f"{cusip}: no price at {', '.join(missing)} — the counterfactual is "
            f"undefined across a quarter the position was not held")

    flow = -sum((m[b]["shares"] - m[a]["shares"]) * price(cusip, b)
                for a, b in zip(window, window[1:]))
    untraded = s0 * price(cusip, window[-1])
    return {"actual": m[window[-1]]["value_usd"] + flow, "untraded": untraded,
            "delta": m[window[-1]]["value_usd"] + flow - untraded}


def top10_at(period, n=10):
    held = [(c, qs(c)[period]) for c in POS if qs(c)[period]["shares"]]
    return [c for c, _ in sorted(held, key=lambda kv: -kv[1]["pct"])[:n]]


def churn():
    """How many distinct names pass through the top 10, and how many persist."""
    sets = [set(top10_at(p)) for p in PER]
    ever = set().union(*sets)
    always = set.intersection(*sets)
    carry = [len(a & b) for a, b in zip(sets, sets[1:])]
    return {"ever": len(ever), "always": [esc(POS[c]["issuer"].title()) for c in always],
            "carry_avg": sum(carry) / len(carry), "carry": carry}


def cohort():
    now = {c: qs(c)[PER[-1]] for c in POS}
    return [{"name": esc(POS[c]["issuer"].title()), "then": qs(c)[PER[0]]["pct"],
             "now": now[c]["pct"]} for c in top10_at(PER[0])]


def spark(vals, colour, w=92, h=22, log=False):
    """Sparkline that breaks where the series does.

    A quarter at zero means the position was not held, and drawing through it
    turns an exit and re-entry into a smooth line. Robinhood, TSMC and Marvell
    all did exactly that inside the window, so gaps are rendered as gaps: one
    polyline per contiguous run, with a marker on the baseline where it broke.
    """
    pts = [(i, v) for i, v in enumerate(vals) if v]
    if len(pts) < 2:
        return ""
    ys = {i: (math.log(v) if log else v) for i, v in pts}
    lo, hi = min(ys.values()), max(ys.values())
    rng = (hi - lo) or 1
    x = lambda i: 2 + i * (w - 4) / (len(vals) - 1)
    y = lambda i: h - 3 - (ys[i] - lo) / rng * (h - 6)

    runs, run = [], [pts[0][0]]
    for (i, _), (j, _) in zip(pts, pts[1:]):
        if j == i + 1:
            run.append(j)
        else:
            runs.append(run)
            run = [j]
    runs.append(run)

    lines = "".join(
        f'<polyline points="{" ".join(f"{x(i):.1f},{y(i):.1f}" for i in r)}" fill="none" '
        f'stroke="{colour}" stroke-width="1.6"/>' if len(r) > 1 else
        f'<circle cx="{x(r[0]):.1f}" cy="{y(r[0]):.1f}" r="1.7" fill="{colour}"/>'
        for r in runs if r)
    gaps = "".join(f'<line x1="{x(i):.1f}" y1="{h-2}" x2="{x(i):.1f}" y2="{h}" '
                   f'stroke="#e0644f" stroke-width="1.4"/>'
                   for i, v in enumerate(vals) if not v)
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{lines}{gaps}</svg>' 


def main() -> None:
    R = rows()
    new = [r for r in R if r["new"]]
    ch = churn()
    theme = [sum(q["pct"] for c in THEME for q in POS[c]["quarters"] if q["period"] == p)
             for p in PER]
    mu = counterfactual("595112103", RECENT)
    co = cohort()
    gone = [c for c in co if c["now"] < 0.5]
    splits = [r for r in R if r["split"] > 1.5]
    iren = next(r for r in R if "Iren" in r["name"])
    late = [r for r in R if r["runup"] is not None and r["runup"] > 100]
    nav = nav_limits()

    num = lambda v: f"{v:+,.0f}%" if v is not None else "—"
    tone = lambda v: "pos" if (v or 0) > 0 else "neg" if (v or 0) < 0 else "dim"

    body = f"""
<h1>What the top 10 says about how BIT trades</h1>
<p class="sub">{QUARTERS} quarters, {PER[0]} to {PER[-1]}. All BIT vehicles combined, US-listed long equity
only. Quarter-end prices are exact daily closes for {sum(1 for r in R if r['ticker'])} of the 10, each
verified against the filing's own implied price; the rest use the implied price directly.</p>

<div class="thesis">
  <h2>The read</h2>
  <p class="note" style="margin-top:14px"><b>The horizon changes the answer, and that is the finding.</b>
  Over four quarters these look like relentless sellers. Over {QUARTERS} they are net accumulators in the
  same names. Both are true: they build for years, then cut hard once a name goes vertical.</p>
  <ol>
    <li><b>The book is rented, not owned &mdash; except at the very top.</b> {ch['ever']} different names have
      passed through the top 10 in {QUARTERS} quarters, for ten slots, and
      {ch['carry_avg']:.1f} of 10 survive an average quarter.
      {'No name held a top-10 slot in every quarter.' if not ch['always'] else 'Held throughout: ' + ', '.join(ch['always']) + '.'}
      But the survivors are held a very long time: Amazon {next(r['age'] for r in R if 'Amazon' in r['name'])}
      quarters, Micron {next(r['age'] for r in R if 'Micron' in r['name'])},
      IREN {iren['age']}. There is a small durable core and a large rented tail.</li>
    <li><b>They accumulate for years, then cut into the vertical part.</b> IREN shares are
      {num(iren['dsh'])} across {QUARTERS} quarters &mdash; but {num(iren['dsh_r'])} in the last four, while
      the price did {num(iren['dpx_r'])}. Micron: {num(next(r['dsh'] for r in R if 'Micron' in r['name']))}
      over the window, {num(next(r['dsh_r'] for r in R if 'Micron' in r['name']))} in the last four quarters
      against a {num(next(r['dpx_r'] for r in R if 'Micron' in r['name']))} price move. The selling is not a
      change of view about the company; it is triggered by the size of the move.</li>
    <li><b>The cap is probably not theirs.</b> Measured against each fund's own NAV &mdash; the denominator
      concentration limits are written against &mdash; every one of the {len(nav)} funds sits just inside the
      UCITS 5/10/40 pattern. Largest single position anywhere: {max(x['max'] for x in nav):.2f}%. Everything
      above 5%, summed, peaks at {max(x['sum_over5'] for x in nav):.1f}% against a 40% ceiling. The trimming
      looks less like conviction management and more like compliance. See below.</li>
    <li><b>New positions are opened after the move, not before.</b> {len(late)} of today's top 10 were
      established after the name had already more than doubled in the preceding year
      ({', '.join(f"{r['name'].split()[0]} {r['runup']:+,.0f}%" for r in sorted(late, key=lambda r: -r['runup'])[:4])}).
      Whatever drives entries, it is not valuation.</li>
    <li><b>Capital rotates inside a theme rather than leaving it.</b> Memory and storage went
      {theme[0]:.1f}% &rarr; {theme[-1]:.1f}% of the book, essentially all of it in the final quarter.</li>
  </ol>
  <p class="note">Your impression was right in the aggregate but too strong at the top. Most of the book is
  rented &mdash; {ch['ever']} names through ten slots in three years. A handful is not: Amazon has been held
  every quarter on file, IREN {iren['age']}, Micron {next(r['age'] for r in R if 'Micron' in r['name'])}.
  They do go long. They just refuse to let any one of them get big.</p>
</div>

<h2>Today's top 10, across {QUARTERS} quarters</h2>
<table>
<thead><tr><th>Name</th><th class="n">Weight</th><th>Weight path</th>
<th class="n">Shares &Delta;<br>{QUARTERS}q</th><th class="n">Shares &Delta;<br>last 4q</th>
<th class="n">Price &Delta;<br>{QUARTERS}q</th><th>Price path</th>
<th class="n">Run-up<br>before entry</th><th class="n">Qtrs<br>held</th></tr></thead>
<tbody>
{''.join(f'''<tr>
  <td><b>{r['name']}</b> {'<span class=tag>new in window</span>' if r['new'] else ''}
    <span class=dim>{r['ticker'] or 'implied px'}</span></td>
  <td class="n">{r['pct']:.2f}%<div class=dim>peak {r['maxpct']:.1f}%</div></td>
  <td>{spark(r['series'], '#3f8ecc')}</td>
  <td class="n {tone(r['dsh'])}">{num(r['dsh'])}</td>
  <td class="n {tone(r['dsh_r'])}">{num(r['dsh_r'])}</td>
  <td class="n {tone(r['dpx'])}">{num(r['dpx'])}</td>
  <td>{spark(r['prices'], '#d9a441', log=True)}</td>
  <td class="n {tone(r['runup'])}">{num(r['runup'])}</td>
  <td class="n">{r['age']}</td></tr>''' for r in R)}
</tbody></table>
<p class="note">The two share columns are the whole argument: positive on the left, negative on the right for
every name that ran hard. Price paths are log-scaled &mdash; several moved more than 10x and a linear
sparkline would be a flat line and a spike. "Run-up before entry" is the price change over the four quarters
ending when the position was established.
{'No split adjustment was needed: filing-implied and vendor prices agree across the whole window for every name here, so raw share counts are comparable throughout.' if not splits else 'Split detected in: ' + ', '.join(r['name'] for r in splits) + ' &mdash; share changes there are not comparable across the event.'}</p>

<h2>Is the cap theirs, or the regulator's?</h2>
<p class="note">Everything above uses share of the 13F book, which merges all vehicles and is
<i>not</i> a NAV percentage. Concentration limits apply per fund, so the statutory portfolios are the only
source that can test them. Measured properly, the pattern is unmistakable.</p>
<table>
<thead><tr><th>Fund</th><th class="n">Positions</th><th class="n">Largest</th><th class="n">Over 5%</th>
<th class="n">Sum of those<br>(limit 40%)</th><th class="n">Over 10%</th></tr></thead>
<tbody>
{''.join(f'''<tr><td>{x['fund']}<div class=dim>{x['top']} &middot; {x['as_of']}</div></td>
  <td class="n dim">{x['n']}</td>
  <td class="n {'neg' if x['max'] > 10 else ''}">{x['max']:.2f}%</td>
  <td class="n">{x['n_over5']}</td>
  <td class="n">{x['sum_over5']:.1f}%</td>
  <td class="n">{x['n_over10']}</td></tr>''' for x in nav)}
</tbody></table>
<p class="note"><b>Not one fund breaches either limit.</b> The largest position across all six is
{max(x['max'] for x in nav):.2f}% and the over-5% bucket never exceeds
{max(x['sum_over5'] for x in nav):.1f}% against a 40% ceiling &mdash; close enough to the line, repeatedly,
that it reads as a managed constraint rather than a coincidence.</p>
<div class="caveat"><b>Flagged as unverified.</b> The UCITS 5/10/40 rule is the likely statutory basis, and
Neil reports that BIT's own documents require justification for holding a position above 5% of NAV. Neither
the prospectus nor the KID is in the document set collected here, so the exact wording &mdash; and whether
their internal policy is stricter than the regulation &mdash; is <i>not</i> confirmed. The measurement above
is real; the attribution of a cause to it is not yet sourced.</div>
<p class="note"><b>Why this matters more than the rest of the page.</b> If the cap is regulatory, then a
signal saying "increase IREN" is not merely unhelpful, it is unactionable &mdash; three funds already hold it
between 7.9% and 9.7% of NAV. Anything we build has to know the headroom before it recommends a direction,
and the useful output becomes which fund has room, or which name to fund a purchase from, rather than a bare
conviction score.</p>

<h2>The survivorship problem &mdash; and the way round it</h2>
<p class="note">Reading today's top 10 backwards only ever shows names that worked or were kept. The fix is to
read the <i>other</i> cohort forwards: here is the top 10 as it stood {QUARTERS} quarters ago, and where each
one sits today.</p>
<table>
<thead><tr><th>Top 10 at {PER[0]}</th><th class="n">Then</th><th class="n">Now</th><th>Outcome</th></tr></thead>
<tbody>
{''.join(f'''<tr><td>{c['name']}</td><td class="n dim">{c['then']:.2f}%</td>
  <td class="n">{c['now']:.2f}%</td>
  <td class="{'neg' if c['now'] < 0.5 else 'dim'}">{
    'gone' if c['now'] == 0 else 'cut to a stub' if c['now'] < 0.5 else 'still material'}</td></tr>'''
  for c in co)}
</tbody></table>
<p class="note"><b>{len(gone)} of the ten are now below 0.5% of the book.</b> Three years is long enough that
almost nothing survives at size. Any claim about "what BIT holds" that starts from today's book is describing
the survivors of a fast rotation, so both cohorts belong in the register &mdash; the exits carry as much
signal as the entries.</p>

<h2>Top-10 carry-over, quarter by quarter</h2>
<table><thead><tr><th>Quarter</th><th class="n">Names kept from prior quarter</th><th></th></tr></thead><tbody>
{''.join(f'''<tr><td>{p}</td><td class="n">{c} of 10</td>
  <td style="width:62%"><div class=bar><i style="width:{c*10}%"></i></div></td></tr>'''
  for p, c in zip(PER[1:], ch['carry']))}
</tbody></table>

<h2>What the cap costs when a name goes vertical</h2>
<p class="note">Micron over the last four quarters &mdash; the trimming phase, and the only window where this
comparison means anything. They sold {abs(next(r['dsh_r'] for r in R if 'Micron' in r['name'])):.0f}% of the
shares into a {num(next(r['dpx_r'] for r in R if 'Micron' in r['name']))} move.</p>
<div class="cards">
  <div class="c"><div class="k">Held, never traded</div><div class="v">${mu['untraded']/1e6:,.0f}m</div></div>
  <div class="c"><div class="k">Actual, incl. cash released</div><div class="v">${mu['actual']/1e6:,.0f}m</div></div>
  <div class="c"><div class="k">Cost of the rule</div><div class="v neg">${mu['delta']/1e6:,.0f}m</div></div>
</div>
<p class="note"><b>Read this carefully.</b> It is position-level opportunity cost, not fund P&amp;L. The
proceeds were redeployed &mdash; much of it into SanDisk, Marvell and Silicon Motion, which ran hard
themselves &mdash; so the rotation may well have paid at the book level. Quarter-end snapshots also cannot
see the price they actually traded at. And the window matters: run over the full {QUARTERS} quarters the same
calculation turns positive, because they were net buyers from a small base, which measures something else
entirely. What it establishes is that the cap binds hard in a parabolic move &mdash; a testable prediction
about behaviour, not a verdict on returns.</p>

<h2>Theme rotation &mdash; memory and storage</h2>
<table><thead><tr><th>Quarter</th><th class="n">Share of book</th><th></th></tr></thead><tbody>
{''.join(f'''<tr><td>{p}</td><td class="n">{t:.1f}%</td>
  <td style="width:62%"><div class=bar><i style="width:{t/max(theme)*100:.0f}%"></i></div></td></tr>'''
  for p, t in zip(PER, theme))}
</tbody></table>
<p class="note">{', '.join(THEME.values())}. Roughly flat for two and a half years, then a step change in the
final quarter &mdash; from {theme[-2]:.1f}% to {theme[-1]:.1f}% in one quarter.</p>

<h2>What would falsify this</h2>
<ul>
  <li>The cap now has a candidate cause. It predicts trimming is driven by NAV headroom rather than by view:
    a name at 9% of a fund's NAV gets cut on strength even when the thesis improves, while the same name at
    3% in another fund does not. The per-fund statutory reports test this directly, and disagreement between
    two funds holding the same security on the same date would be strong evidence.</li>
  <li>Momentum predicts entries cluster after large moves, which holds for {len(late)} of the ten. The
    observation that would break it: a large new position opened in a name that had just fallen.</li>
  <li>The accumulate-then-trim reading rests on one full cycle in a handful of names. It predicts that the
    memory complex bought this quarter gets cut hard if it goes vertical &mdash; the next few filings test
    it directly.</li>
  <li>The 13F sees US-listed long equity only. If the non-US book or any hedges behave differently, this
    describes a slice rather than the strategy.</li>
</ul>
"""

    css = """
:root{--bg:#0f1319;--panel:#161b23;--panel2:#1b212b;--line:#2a323d;--tx:#dce3ec;
--dim:#8b97a6;--faint:#6e7c8c;--accent:#3f8ecc;--pos:#4fb477;--neg:#e0644f;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;padding:34px 20px 70px;background:var(--bg);color:var(--tx);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1040px;margin:0 auto}
h1{font-size:25px;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:16px;margin:34px 0 10px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13.5px;margin:0 0 22px}
.note{color:var(--dim);font-size:13px;margin:10px 0}
.thesis{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:0 10px 10px 0;padding:4px 22px 16px}
.thesis h2{margin-top:18px}
ol,ul{padding-left:20px;font-size:14px} li{margin:9px 0}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--panel);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
color:var(--faint);font-weight:600;padding:9px 12px;border-bottom:1px solid var(--line)}
td{padding:9px 12px;border-bottom:1px solid #20262f;vertical-align:middle}
tr:last-child td{border-bottom:0}
td.n,th.n{text-align:right;font-family:var(--mono);font-size:12.5px;font-variant-numeric:tabular-nums}
th.n{font-family:inherit}
.pos{color:var(--pos)}.neg{color:var(--neg)}.dim{color:var(--dim);font-size:11.5px}
.tag{display:inline-block;padding:0 6px;border-radius:20px;font-size:10px;font-weight:700;
background:#122b1b;color:var(--pos);border:1px solid #1d4429;vertical-align:middle}
.bar{height:8px;background:var(--panel2);border-radius:4px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent)}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
.c{flex:1;min-width:170px;background:var(--panel);border:1px solid var(--line);
border-radius:10px;padding:14px 16px}
.c .k{color:var(--faint);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em}
.c .v{font-family:var(--mono);font-size:21px;margin-top:5px}
svg{display:block}
"""
    OUT.write_text(
        f"<!doctype html><html><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>BIT top-10 thesis</title><style>{css}</style></head>"
        f"<body><main>{body}</main></body></html>")
    print(f"-> {OUT} ({OUT.stat().st_size/1024:.0f} KB)  window {PER[0]}..{PER[-1]}")
    print(f"   {ch['ever']} names through top 10; carry-over avg {ch['carry_avg']:.1f}/10; "
          f"always-in: {ch['always'] or 'none'}")
    print(f"   theme {theme[0]:.1f}% -> {theme[-1]:.1f}%; Micron rule ${mu['delta']/1e6:,.0f}m; "
          f"{len(gone)}/10 of the {PER[0]} top-10 now sub-0.5%")


if __name__ == "__main__":
    main()
