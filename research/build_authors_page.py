"""Render the Anthropic-affiliated contributor table as a standalone HTML page.

Includes anyone who carried an Anthropic staff byline on at least one paper in
the window, so people who moved in from the Fellows programme are kept and pure
external collaborators are not.

Usage:
    python research/build_authors_page.py
"""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent
REGISTER = ROOT / "research" / "docs" / "anthropic_contributors.json"
OUT = ROOT / "research" / "anthropic_authors.html"


def rows(register: dict) -> list[dict]:
    """Select and rank the Anthropic-affiliated people.

    Args:
        register: Parsed contributor register.

    Returns:
        Person records with an Anthropic staff byline, most papers first.
    """
    people = [p for p in register["people"] if p["anthropic_bylines"] > 0]
    people.sort(key=lambda p: (-p["appearances"], -p["core"], -p["last_author"], p["name"]))
    return people


def render(register: dict) -> str:
    """Build the page.

    Args:
        register: Parsed contributor register.

    Returns:
        Complete HTML document.
    """
    people = rows(register)
    arts = register["articles"]
    span = f"{min(a['date'] for a in arts)} to {max(a['date'] for a in arts)}"
    dist = Counter(p["appearances"] for p in people)
    top = max(p["appearances"] for p in people)
    multi = sum(1 for p in people if p["appearances"] > 1)

    body = []
    for i, p in enumerate(people, 1):
        alias = (
            f'<span class="alias">merged: {html.escape(", ".join(p["aliases"]))}</span>'
            if p["aliases"]
            else ""
        )
        flags = []
        if p["core"]:
            flags.append(f'<span class="tag core">core &times;{p["core"]}</span>')
        if p["last_author"]:
            flags.append(f'<span class="tag last">last author &times;{p["last_author"]}</span>')
        if p["fellow"]:
            flags.append('<span class="tag fellow">also fellow</span>')
        if p["llm_sourced"]:
            flags.append('<span class="tag llm">llm-sourced byline</span>')
        titles = "".join(
            f'<li><a href="{html.escape(a["url"])}">{html.escape(a["title"])}</a>'
            f'<span class="d">{a["date"]}</span></li>'
            for a in sorted(p["articles"], key=lambda a: a["date"], reverse=True)
        )
        bar = round(p["appearances"] / top * 100)
        body.append(f"""
    <details class="row">
      <summary>
        <span class="rank">{i}</span>
        <span class="name">{html.escape(p["name"])}{alias}</span>
        <span class="bar"><i style="width:{bar}%"></i></span>
        <span class="count">{p["appearances"]}</span>
        <span class="flags">{"".join(flags)}</span>
      </summary>
      <ul class="papers">{titles}</ul>
    </details>""")

    hist = "".join(
        f'<div class="hb"><span class="hn">{n}</span>'
        f'<i style="height:{round(c / max(dist.values()) * 60) + 2}px"></i>'
        f'<span class="hc">{c}</span></div>'
        for n, c in sorted(dist.items())
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Anthropic contributors — papers authored</title>
<style>
  :root{{
    --bg:#0d1117; --panel:#151b23; --line:#2a323d; --tx:#e6edf3;
    --dim:#9aa7b4; --faint:#6e7c8c; --accent:#4c9aff;
    --core:#d29922; --last:#3fb950; --fellow:#a371f7; --llm:#f85149;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--tx);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased}}
  a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
  .wrap{{max-width:960px;margin:0 auto;padding:0 22px 80px}}
  header{{border-bottom:1px solid var(--line);padding:34px 0 22px;margin-bottom:24px}}
  h1{{margin:0 0 6px;font-size:26px;letter-spacing:-.02em}}
  .sub{{color:var(--dim);font-size:14px}}
  .sub b{{color:var(--tx);font-weight:600}}
  .stats{{display:flex;gap:26px;flex-wrap:wrap;margin:20px 0 0}}
  .stat{{background:var(--panel);border:1px solid var(--line);border-radius:8px;
    padding:12px 16px;min-width:120px}}
  .stat b{{display:block;font-size:22px;letter-spacing:-.02em}}
  .stat span{{color:var(--faint);font-size:12px;text-transform:uppercase;letter-spacing:.05em}}
  .hist{{display:flex;align-items:flex-end;gap:7px;margin:22px 0 4px;
    background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}}
  .hb{{display:flex;flex-direction:column;align-items:center;gap:3px}}
  .hb i{{display:block;width:22px;background:var(--accent);border-radius:2px;opacity:.75}}
  .hn{{font-size:11px;color:var(--faint);order:3}}
  .hc{{font-size:11px;color:var(--dim)}}
  .caption{{color:var(--faint);font-size:12px;margin:0 0 26px}}
  .row{{border-bottom:1px solid var(--line)}}
  .row summary{{display:grid;grid-template-columns:34px minmax(150px,1fr) 90px 34px auto;
    gap:12px;align-items:center;padding:9px 4px;cursor:pointer;list-style:none}}
  .row summary::-webkit-details-marker{{display:none}}
  .row:hover summary{{background:#121821}}
  .rank{{color:var(--faint);font-size:12px;text-align:right}}
  .name{{font-weight:500}}
  .alias{{display:block;color:var(--faint);font-size:11px;font-weight:400}}
  .bar{{background:#1c232d;border-radius:3px;height:7px;overflow:hidden}}
  .bar i{{display:block;height:100%;background:var(--accent);opacity:.8}}
  .count{{font-variant-numeric:tabular-nums;color:var(--dim);font-size:13px}}
  .flags{{display:flex;gap:5px;flex-wrap:wrap}}
  .tag{{font-size:10.5px;padding:2px 7px;border-radius:20px;white-space:nowrap;
    border:1px solid currentColor}}
  .core{{color:var(--core)}} .last{{color:var(--last)}}
  .fellow{{color:var(--fellow)}} .llm{{color:var(--llm)}}
  .papers{{margin:0 0 12px;padding:2px 0 2px 46px;list-style:none}}
  .papers li{{padding:3px 0;font-size:13.5px;color:var(--dim)}}
  .d{{color:var(--faint);font-size:11.5px;margin-left:9px;font-variant-numeric:tabular-nums}}
  footer{{margin-top:34px;color:var(--faint);font-size:12.5px;line-height:1.7}}
  @media(max-width:640px){{
    .row summary{{grid-template-columns:26px 1fr 34px;}}
    .bar,.flags{{display:none}}
  }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Anthropic contributors — papers authored</h1>
  <div class="sub">
    Everyone carrying an <b>Anthropic staff byline</b> on at least one publication,
    {span}. Derived from author lists on
    <a href="https://alignment.anthropic.com/">alignment.anthropic.com</a> and
    <a href="https://transformer-circuits.pub/">transformer-circuits.pub</a>.
    Click a name for its papers.
  </div>
  <div class="stats">
    <div class="stat"><b>{len(people)}</b><span>people</span></div>
    <div class="stat"><b>{len(arts)}</b><span>articles</span></div>
    <div class="stat"><b>{multi}</b><span>more than one paper</span></div>
    <div class="stat"><b>{top}</b><span>most by one person</span></div>
  </div>
</header>

<div class="hist">{hist}</div>
<p class="caption">Distribution: papers authored (x) by number of people (y).
The register is a long tail — {dist[1]} of {len(people)} appear exactly once.</p>

{"".join(body)}

<footer>
  <b>Reading it.</b> Counts are papers in the window, not career totals.
  <span style="color:var(--core)">core</span> is the lab's own core-contributor
  marker, which only transformer-circuits uses;
  <span style="color:var(--last)">last author</span> usually indicates the senior
  author on a paper.
  <span style="color:var(--fellow)">also fellow</span> means the person appears
  under the Anthropic Fellows Program on another paper — affiliation drift, and a
  candidate fellow-to-staff conversion.
  <span style="color:var(--llm)">llm-sourced</span> flags a byline recovered by a
  model where the deterministic parser could not read the page; treat its
  affiliation as model-asserted rather than page-stated.<br><br>
  <b>Caveats.</b> Anthropic publishes to channels this does not cover, so absence
  from this list is not evidence someone does not work there. Names are merged via
  confirmed entries in <code>config/aliases.yaml</code>; where a merge happened it
  is shown under the name.
</footer>
</div>
</body>
</html>
"""


def main() -> None:
    """Write the page."""
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    OUT.write_text(render(register), encoding="utf-8")
    print(f"{len(rows(register))} people -> {OUT}")


if __name__ == "__main__":
    main()
