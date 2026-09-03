"""Render the DeepSeek contributor table as a standalone HTML page.

Every author on a DeepSeek paper is DeepSeek's own, so unlike the Anthropic page
there is no employment filter. What matters instead is contribution role and
whether the lab has marked the person as departed.

Usage:
    python research/build_deepseek_page.py
"""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
REGISTER = ROOT / "research" / "docs" / "deepseek_contributors.json"
OUT = ROOT / "research" / "papers" / "deepseek_authors.html"

ROLE_CLASS = {
    "Core Contributors": "core",
    "Contributors": "contrib",
    "Research & Engineering": "reng",
    "Data Annotation": "data",
    "Business & Compliance": "biz",
}


def render(register: dict) -> str:
    """Build the page.

    Args:
        register: Parsed DeepSeek register.

    Returns:
        Complete HTML document.
    """
    people = register["people"]
    papers = register["papers"]
    span = f"{min(p['date'] for p in papers)} to {max(p['date'] for p in papers)}"
    top = max(p["appearances"] for p in people)
    dist = Counter(p["appearances"] for p in people)
    departed = [p for p in people if p["ever_departed"]]
    core = [p for p in people if p["core"]]

    body = []
    for i, p in enumerate(people, 1):
        tags = []
        for role in p["roles"]:
            tags.append(
                f'<span class="tag {ROLE_CLASS.get(role, "other")}">{html.escape(role)}</span>'
            )
        if p["ever_departed"]:
            tags.append('<span class="tag gone">departed</span>')
        titles = "".join(
            f'<li><a href="{html.escape(a["url"])}">{html.escape(a["title"])}</a>'
            f'<span class="d">{a["date"]}</span></li>'
            for a in sorted(p["papers"], key=lambda a: a["date"], reverse=True)
        )
        bar = round(p["appearances"] / top * 100)
        cls = " gone-row" if p["ever_departed"] else ""
        body.append(f"""
    <details class="row{cls}">
      <summary>
        <span class="rank">{i}</span>
        <span class="name">{html.escape(p["name"])}</span>
        <span class="bar"><i style="width:{bar}%"></i></span>
        <span class="count">{p["appearances"]}</span>
        <span class="flags">{"".join(tags)}</span>
      </summary>
      <ul class="papers">{titles}</ul>
    </details>""")

    hist = "".join(
        f'<div class="hb"><span class="hn">{n}</span>'
        f'<i style="height:{round(c / max(dist.values()) * 60) + 2}px"></i>'
        f'<span class="hc">{c}</span></div>'
        for n, c in sorted(dist.items())
    )
    paper_rows = "".join(
        f"<tr><td>{p['date']}</td>"
        f"<td><a href=\"{html.escape(p['url'])}\">{html.escape(p['title'][:58])}</a></td>"
        f"<td class=n>{len(p['authors'])}</td>"
        f"<td class=n>{sum(1 for a in p['authors'] if a['departed'])}</td>"
        f"<td class=s>{'roles' if p['roles_source'] == 'author_list' else '—'}</td></tr>"
        for p in sorted(papers, key=lambda p: p["date"], reverse=True)
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DeepSeek contributors — papers authored</title>
<style>
  :root{{
    --bg:#0d1117; --panel:#151b23; --line:#2a323d; --tx:#e6edf3;
    --dim:#9aa7b4; --faint:#6e7c8c; --accent:#4c9aff;
    --core:#d29922; --contrib:#58a6ff; --reng:#3fb950; --data:#8b949e;
    --biz:#a371f7; --gone:#f85149;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--tx);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased}}
  a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
  .wrap{{max-width:1000px;margin:0 auto;padding:0 22px 80px}}
  header{{border-bottom:1px solid var(--line);padding:34px 0 22px;margin-bottom:24px}}
  h1{{margin:0 0 6px;font-size:26px;letter-spacing:-.02em}}
  h2{{font-size:15px;margin:34px 0 10px;color:var(--dim);font-weight:600;
    text-transform:uppercase;letter-spacing:.06em}}
  .sub{{color:var(--dim);font-size:14px}} .sub b{{color:var(--tx);font-weight:600}}
  .stats{{display:flex;gap:24px;flex-wrap:wrap;margin:20px 0 0}}
  .stat{{background:var(--panel);border:1px solid var(--line);border-radius:8px;
    padding:12px 16px;min-width:112px}}
  .stat b{{display:block;font-size:22px;letter-spacing:-.02em}}
  .stat span{{color:var(--faint);font-size:12px;text-transform:uppercase;letter-spacing:.05em}}
  table{{width:100%;border-collapse:collapse;font-size:13.5px}}
  th{{text-align:left;color:var(--faint);font-weight:500;font-size:11.5px;
    text-transform:uppercase;letter-spacing:.05em;padding:6px 8px;border-bottom:1px solid var(--line)}}
  td{{padding:7px 8px;border-bottom:1px solid #1b222c;color:var(--dim)}}
  td.n{{text-align:right;font-variant-numeric:tabular-nums;color:var(--tx)}}
  td.s{{color:var(--faint);font-size:12px}}
  .hist{{display:flex;align-items:flex-end;gap:6px;margin:14px 0 4px;
    background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;
    overflow-x:auto}}
  .hb{{display:flex;flex-direction:column;align-items:center;gap:3px}}
  .hb i{{display:block;width:20px;background:var(--accent);border-radius:2px;opacity:.75}}
  .hn{{font-size:11px;color:var(--faint);order:3}} .hc{{font-size:11px;color:var(--dim)}}
  .caption{{color:var(--faint);font-size:12px;margin:0 0 20px}}
  .row{{border-bottom:1px solid var(--line)}}
  .row summary{{display:grid;grid-template-columns:40px minmax(130px,1fr) 80px 30px auto;
    gap:12px;align-items:center;padding:8px 4px;cursor:pointer;list-style:none}}
  .row summary::-webkit-details-marker{{display:none}}
  .row:hover summary{{background:#121821}}
  .gone-row .name{{color:var(--gone)}}
  .rank{{color:var(--faint);font-size:12px;text-align:right}}
  .name{{font-weight:500}}
  .bar{{background:#1c232d;border-radius:3px;height:7px;overflow:hidden}}
  .bar i{{display:block;height:100%;background:var(--accent);opacity:.8}}
  .count{{font-variant-numeric:tabular-nums;color:var(--dim);font-size:13px}}
  .flags{{display:flex;gap:5px;flex-wrap:wrap}}
  .tag{{font-size:10.5px;padding:2px 7px;border-radius:20px;white-space:nowrap;
    border:1px solid currentColor}}
  .core{{color:var(--core)}} .contrib{{color:var(--contrib)}} .reng{{color:var(--reng)}}
  .data{{color:var(--data)}} .biz{{color:var(--biz)}} .gone{{color:var(--gone)}}
  .other{{color:var(--faint)}}
  .papers{{margin:0 0 12px;padding:2px 0 2px 52px;list-style:none}}
  .papers li{{padding:3px 0;font-size:13.5px;color:var(--dim)}}
  .d{{color:var(--faint);font-size:11.5px;margin-left:9px;font-variant-numeric:tabular-nums}}
  footer{{margin-top:34px;color:var(--faint);font-size:12.5px;line-height:1.7}}
  @media(max-width:640px){{
    .row summary{{grid-template-columns:30px 1fr 30px}} .bar,.flags{{display:none}}
  }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>DeepSeek contributors — papers authored</h1>
  <div class="sub">
    Every author credited on a <b>DeepSeek</b> paper, {span}. Built from arXiv author
    metadata plus the <b>Author List</b> appendix the papers themselves publish, which
    groups authors by contribution role and marks departures.
    Click a name for its papers.
  </div>
  <div class="stats">
    <div class="stat"><b>{len(people)}</b><span>people</span></div>
    <div class="stat"><b>{len(papers)}</b><span>papers</span></div>
    <div class="stat"><b>{sum(1 for p in people if p['appearances'] >= 2)}</b><span>2+ papers</span></div>
    <div class="stat"><b>{len(core)}</b><span>core contributors</span></div>
    <div class="stat"><b>{len(departed)}</b><span>marked departed</span></div>
  </div>
</header>

<h2>Papers</h2>
<table>
<tr><th>Date</th><th>Title</th><th style="text-align:right">Authors</th>
<th style="text-align:right">Departed</th><th>Author list</th></tr>
{paper_rows}
</table>

<h2>Distribution</h2>
<div class="hist">{hist}</div>
<p class="caption">Papers authored (x) by number of people (y).
{dist[1]} of {len(people)} appear on exactly one paper.</p>

<h2>Contributors</h2>
{"".join(body)}

<footer>
  <b>Reading it.</b> DeepSeek files its papers under a single collective byline, so
  every name here is DeepSeek's own — there is no employment filter, unlike the
  Anthropic register. <b>Author order carries no signal:</b> the papers state authors
  are listed alphabetically by first name, so first- and last-author position is
  meaningless and is not scored.
  <span style="color:var(--core)">Core Contributors</span> and the other role tags are
  taken verbatim from each paper's own Author List section.
  <span style="color:var(--gone)">departed</span> means a paper marked that person as
  having left the team — DeepSeek prints this itself, which is a personnel signal no
  other lab in the register publishes.<br><br>
  <b>Caveats.</b> Only three of the {len(papers)} papers carry an Author List appendix;
  for the rest, roles are unknown and only arXiv metadata is available, so a person's
  absence from the role tags means the paper did not publish them, not that they had no
  role. Papers DeepSeek publishes without the collective byline are found by title and
  may be incompletely enumerated.
</footer>
</div>
</body>
</html>
"""


def main() -> None:
    """Write the page."""
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    OUT.write_text(render(register), encoding="utf-8")
    print(f"{len(register['people'])} people -> {OUT}")


if __name__ == "__main__":
    main()
