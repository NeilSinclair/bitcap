"""Render scored lab announcements as a browsable HTML page.

The page exists to answer two questions by eye: what is the pipeline picking
up, and does the scoring rule agree with a human reading the same item. Every
scored item therefore shows the evidence behind its score — the mechanism tags,
their quotes, and what the model was actually allowed to read.
"""

from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"
VERSION = "v2"
SCORED = DOCS / f"scored_announcements_{VERSION}.json"
COST = DOCS / "announcement_cost.json"
CATEGORIES = ROOT / "config" / "categories.yaml"
MECHANISMS = ROOT / "config" / "mechanisms.yaml"
OUT = Path(__file__).parent / "announcements.html"

LAB_LABEL = {"anthropic": "Anthropic", "openai": "OpenAI", "deepseek": "DeepSeek"}


def esc(value) -> str:
    """HTML-escape a value that may be None."""
    return html.escape(str(value)) if value else ""


def tag_html(tag: dict, labels: dict, kind: str) -> str:
    """Render one mechanism or category tag with its supporting quote.

    Args:
        tag: Tag dict from the model.
        labels: id -> human label.
        kind: "mech" or "cat", for styling.

    Returns:
        HTML fragment.
    """
    mag = f" · {esc(tag['magnitude'])}" if "magnitude" in tag else ""
    quote = (
        f'<div class="q">“{esc(tag["quote"])}”</div>' if tag.get("quote") else ""
    )
    return f"""
<div class="tag {kind}">
  <div class="th"><span class="tid">{esc(labels.get(tag['id'], tag['id']))}</span>
    <span class="sg s-{esc(tag['sign'])}">{esc(tag['sign'])}</span>
    <span class="meta">{esc(tag['confidence'])} confidence{mag}</span></div>
  <div class="rz">{esc(tag['reason'])}</div>
  {quote}
</div>"""


def item_html(a: dict, mech_labels: dict, cat_labels: dict) -> str:
    """Render one scored announcement.

    Args:
        a: Scored announcement record.
        mech_labels: Mechanism id -> label.
        cat_labels: Category id -> label.

    Returns:
        HTML fragment.
    """
    tags = "".join(tag_html(m, mech_labels, "mech") for m in a["mechanisms"])
    tags += "".join(tag_html(c, cat_labels, "cat") for c in a["categories"])
    body = f'<div class="tags">{tags}</div>' if tags else ""

    notable = (
        f'<div class="notable">Flagged notable — {esc(a["notable_reason"])}</div>'
        if a.get("notable")
        else ""
    )
    src = (
        '<span class="src warn">RSS summary only</span>'
        if a["text_source"] == "rss_summary"
        else '<span class="src">full text</span>'
    )

    return f"""
<article class="item b-{a['band']}" data-lab="{a['lab']}" data-band="{a['band']}"
         data-notable="{str(a.get('notable', False)).lower()}">
  <div class="sc"><span class="n">{a['score']:g}</span></div>
  <div class="bd">
    <div class="hd">
      <span class="lab l-{a['lab']}">{LAB_LABEL[a['lab']]}</span>
      <span class="dt">{esc(a['date'])}</span>
      <span class="ev">{esc(a['event_type'].replace('_', ' '))}</span>
      {src}
    </div>
    <h3><a href="{esc(a['url'])}" rel="noopener">{esc(a['title'])}</a></h3>
    <p class="sm">{esc(a['summary'])}</p>
    {notable}
    {body}
  </div>
</article>"""


def build() -> str:
    """Assemble the page.

    Returns:
        HTML document body.
    """
    data = json.loads(SCORED.read_text())
    scored, failures = data["scored"], data["failures"]
    costs = json.loads(COST.read_text()) if COST.exists() else []

    mech_labels = {
        m["id"]: m["label"] for m in yaml.safe_load(MECHANISMS.read_text())["mechanisms"]
    }
    cat_labels = {
        c["id"]: c["label"] for c in yaml.safe_load(CATEGORIES.read_text())["categories"]
    }

    bands = Counter(a["band"] for a in scored)
    labs = Counter(a["lab"] for a in scored)
    spend = sum(c["usd"] for c in costs)
    carried = [a for a in scored if a["score"] > 0]

    # Category reach: how many scored items route to each kind of company.
    cat_hits = Counter()
    for a in scored:
        for c in a["categories"]:
            cat_hits[c["id"]] += 1
    mech_hits = Counter()
    for a in scored:
        for m in a["mechanisms"]:
            mech_hits[m["id"]] += 1

    def card(value, label, note=""):
        return (
            f'<div class="card"><div class="v">{value}</div>'
            f'<div class="l">{label}</div><div class="n">{note}</div></div>'
        )

    cards = "".join(
        [
            card(len(scored), "announcements read", "six months, three labs"),
            card(
                f"{len(carried)}",
                "carried any signal",
                f"{100 * len(carried) / len(scored):.0f}% — the rest scored zero",
            ),
            card(
                bands["high"] + bands["medium"],
                "scored medium or high",
                f"{bands['high']} high, {bands['medium']} medium",
            ),
            card(f"${spend:.2f}", "total cost", f"{len(costs)} model calls"),
        ]
    )

    cat_rows = "".join(
        f'<tr><td>{esc(cat_labels.get(k, k))}</td><td class="num">{v}</td></tr>'
        for k, v in cat_hits.most_common()
    ) or '<tr><td colspan="2" class="none">none</td></tr>'

    mech_rows = "".join(
        f'<tr><td>{esc(mech_labels.get(k, k))}</td><td class="num">{v}</td></tr>'
        for k, v in mech_hits.most_common()
    ) or '<tr><td colspan="2" class="none">none</td></tr>'

    lab_rows = "".join(
        f"<tr><td>{LAB_LABEL[k]}</td><td class=\"num\">{v}</td>"
        f'<td class="num">{sum(1 for a in scored if a["lab"] == k and a["score"] > 0)}</td></tr>'
        for k, v in labs.most_common()
    )

    items = "".join(item_html(a, mech_labels, cat_labels) for a in scored)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fail_note = (
        f'<p><b>{len(failures)} items failed to classify</b> and are excluded.</p>'
        if failures
        else ""
    )

    return f"""<title>Lab Announcement Signal</title>
<style>
:root {{
  --bg:#fbfaf8; --fg:#1a1a18; --muted:#6b6b64; --line:#e4e2dc; --card:#fff;
  --accent:#b8552e; --chip:#f1efe9;
  --hi:#8c3a1c; --med:#8a6d1f; --lo:#5a7a5f; --zero:#9a988f;
  --pos:#2d6a4a; --neg:#8a3a3a; --mix:#7a6a2a;
}}
@media (prefers-color-scheme:dark) {{ :root:not([data-theme="light"]) {{
  --bg:#15151a; --fg:#e9e7e2; --muted:#94928b; --line:#2b2b32; --card:#1c1c21;
  --accent:#e08a5c; --chip:#26262c;
  --hi:#e0885f; --med:#d6b45c; --lo:#8fc0a0; --zero:#7c7a73;
  --pos:#6bbf8f; --neg:#d98a8a; --mix:#cbb46a;
}} }}
:root[data-theme="dark"] {{
  --bg:#15151a; --fg:#e9e7e2; --muted:#94928b; --line:#2b2b32; --card:#1c1c21;
  --accent:#e08a5c; --chip:#26262c;
  --hi:#e0885f; --med:#d6b45c; --lo:#8fc0a0; --zero:#7c7a73;
  --pos:#6bbf8f; --neg:#d98a8a; --mix:#cbb46a;
}}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--fg);margin:0;
  font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1000px;margin:0 auto;padding:40px 20px 80px}}
h1{{font-size:28px;margin:0 0 6px;letter-spacing:-.02em}}
.sub{{color:var(--muted);margin:0 0 28px;font-size:14px}}
.cards{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-bottom:24px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:14px 15px}}
.card .v{{font-size:25px;font-weight:600;letter-spacing:-.02em}}
.card .l{{font-size:13px}} .card .n{{font-size:11.5px;color:var(--muted);margin-top:4px}}
.note{{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:0 8px 8px 0;padding:13px 16px;margin:0 0 24px;font-size:13.5px}}
.note p{{margin:0 0 8px}} .note p:last-child{{margin:0}}
.cols{{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));margin-bottom:26px}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:9px;overflow:hidden}}
.panel h4{{margin:0;padding:10px 14px;font-size:11.5px;text-transform:uppercase;
  letter-spacing:.05em;color:var(--muted);border-bottom:1px solid var(--line)}}
.panel table{{width:100%;border-collapse:collapse;font-size:13px}}
.panel td{{padding:6px 14px;border-bottom:1px solid var(--line)}}
.panel tr:last-child td{{border-bottom:0}}
.num{{text-align:right;font-variant-numeric:tabular-nums;width:60px}}
.filters{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:16px}}
.filters button{{font:inherit;font-size:13px;padding:5px 12px;border-radius:999px;
  border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}}
.filters button[aria-pressed="true"]{{background:var(--fg);color:var(--bg);border-color:var(--fg)}}
.item{{display:flex;gap:14px;background:var(--card);border:1px solid var(--line);
  border-radius:9px;padding:14px;margin-bottom:10px}}
.item.b-none{{opacity:.62}}
.sc{{flex:0 0 46px;text-align:center}}
.sc .n{{display:block;font-size:19px;font-weight:600;font-variant-numeric:tabular-nums;
  padding:5px 0;border-radius:7px;background:var(--chip)}}
.b-high .sc .n{{color:var(--hi)}} .b-medium .sc .n{{color:var(--med)}}
.b-low .sc .n{{color:var(--lo)}} .b-none .sc .n{{color:var(--zero)}}
.bd{{flex:1;min-width:0}}
.hd{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:11.5px;
  color:var(--muted);margin-bottom:3px}}
.lab{{font-weight:600;color:var(--fg)}}
.ev,.src{{background:var(--chip);border-radius:4px;padding:1px 6px}}
.src.warn{{color:var(--med)}}
h3{{font-size:15.5px;margin:0 0 4px;font-weight:600;line-height:1.35}}
h3 a{{color:var(--fg);text-decoration:none}} h3 a:hover{{color:var(--accent)}}
.sm{{margin:0;font-size:13.5px;color:var(--muted)}}
.notable{{margin-top:8px;font-size:12.5px;color:var(--accent);font-weight:600}}
.tags{{margin-top:10px;display:grid;gap:7px}}
.tag{{border-left:2px solid var(--line);padding:5px 0 5px 10px}}
.tag.mech{{border-left-color:var(--accent)}}
.th{{display:flex;gap:7px;flex-wrap:wrap;align-items:baseline;font-size:12.5px}}
.tid{{font-weight:600}}
.sg{{font-size:11px;padding:0 5px;border-radius:3px;background:var(--chip)}}
.s-positive{{color:var(--pos)}} .s-negative{{color:var(--neg)}} .s-mixed{{color:var(--mix)}}
.th .meta{{color:var(--muted);font-size:11px}}
.rz{{font-size:12.5px;color:var(--muted);margin-top:2px}}
.q{{font-size:12px;font-style:italic;color:var(--muted);margin-top:3px;
  padding-left:8px;border-left:1px solid var(--line)}}
.none{{color:var(--muted)}}
footer{{color:var(--muted);font-size:12px;margin-top:26px}}
</style>
<div class="wrap">
<h1>Lab Announcement Signal</h1>
<p class="sub">Everything Anthropic, OpenAI and DeepSeek announced in the six months
to {generated}, classified against the transmission mechanisms and holding categories
in <code>config/</code>.</p>

<div class="cards">{cards}</div>

<div class="note">
<p><b>How the score works.</b> The model never emits a score. It classifies the
announcement and tags the mechanisms it touches, and <i>every tag must carry a verbatim
quote from the document</i>. The score is then computed deterministically from
<code>config/scoring.yaml</code> as
<code>100 × (0.5 × event_weight/5 + 0.5 × strongest_mechanism/9)</code>, where
<code>strongest_mechanism</code> is the largest <code>magnitude × confidence</code> across
the tags, both on a 1–3 scale. Inside a tag the two multiply, because the product is an
expected magnitude with confidence as a probability weight. Between the axes they add, so
neither can veto the other. Every number here can be argued line by line and changed
without touching code.</p>
<p><b>Zero is the expected answer.</b> Most lab output is regional expansion, grants,
hiring and policy comment, which carries no transmission to a semiconductor or
infrastructure investor. A zero is the filter working, not a miss.</p>
<p><b>Read OpenAI items with care.</b> openai.com blocks automated fetching behind
Cloudflare, so its items are scored from the official RSS title and summary rather than
full article text, and their tag confidence is capped at medium. Anthropic and DeepSeek
are scored on full text. Every item states which it was.{fail_note}</p>
</div>

<div class="cols">
  <div class="panel"><h4>Reach into holding categories</h4>
    <table>{cat_rows}</table></div>
  <div class="panel"><h4>Mechanisms tagged</h4>
    <table>{mech_rows}</table></div>
  <div class="panel"><h4>By lab (total / carried signal)</h4>
    <table>{lab_rows}</table></div>
</div>

<div class="filters">
  <button data-f="all" aria-pressed="true">All ({len(scored)})</button>
  <button data-f="signal" aria-pressed="false">Carried signal ({len(carried)})</button>
  <button data-f="notable" aria-pressed="false">Notable ({sum(1 for a in scored if a.get('notable'))})</button>
  <button data-f="anthropic" aria-pressed="false">Anthropic ({labs['anthropic']})</button>
  <button data-f="openai" aria-pressed="false">OpenAI ({labs['openai']})</button>
  <button data-f="deepseek" aria-pressed="false">DeepSeek ({labs['deepseek']})</button>
</div>

{items}

<footer>Generated {generated}. Sources declared in <code>config/sources.yaml</code>;
prompt in <code>prompts/announcement_scoring/v2.md</code>; scoring rule in
<code>config/scoring.yaml</code>. Every item links to its primary source.</footer>
</div>
<script>
document.querySelectorAll(".filters button").forEach(b => {{
  b.addEventListener("click", () => {{
    document.querySelectorAll(".filters button")
      .forEach(x => x.setAttribute("aria-pressed", x === b));
    const f = b.dataset.f;
    document.querySelectorAll(".item").forEach(el => {{
      let show;
      if (f === "all") show = true;
      else if (f === "signal") show = el.dataset.band !== "none";
      else if (f === "notable") show = el.dataset.notable === "true";
      else show = el.dataset.lab === f;
      el.style.display = show ? "" : "none";
    }});
  }});
}});
</script>"""


if __name__ == "__main__":
    OUT.write_text(build())
    print(f"written {OUT}")
