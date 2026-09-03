"""Render the DeepMind, Meta and Mistral contributor registers as HTML pages.

These three registers share one schema -- a person carries `is_lab_staff`,
`is_fellow` and `affiliations` rather than the `core`/`roles` fields the
Anthropic, OpenAI and DeepSeek registers use -- so one builder covers all
three rather than three near-identical scripts.

The page is a research artefact, not a product surface. It exists to answer
two questions by eye: does every paper resolve to a primary source, and which
of the people and institutions on it are worth following downstream.

Usage:
    python research/papers/build_lab_authors_page.py deepmind
    python research/papers/build_lab_authors_page.py            # all three
"""

from __future__ import annotations

import html
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"
OUTDIR = ROOT / "research" / "papers"

# Each harvester records the lab's own landing page under a different key --
# DeepMind publishes a publications page, Meta a research listing, Mistral only
# ever a blog announcement -- so the key is per lab, not a shared field.
LABS = {
    "deepmind": {"lab_url_key": "url", "lab_url_label": "DeepMind page"},
    "meta": {"lab_url_key": "meta_url", "lab_url_label": "Meta page"},
    "mistral": {"lab_url_key": "announcement_url", "lab_url_label": "Announcement"},
}

# How a paper was matched to its arXiv record. DeepMind links arXiv directly
# from its own page, so it has no matching step and no `resolution` value.
RESOLUTION = {
    "exact_title": ("exact", "Title matched an arXiv record exactly."),
    "relaxed": ("relaxed", "Matched on relaxed title plus abstract-vocabulary overlap."),
    None: ("direct link", "arXiv link taken from the lab's own page; no matching needed."),
}


def esc(value) -> str:
    """HTML-escape a value that may be None."""
    return html.escape(str(value)) if value else ""


def classify(person: dict) -> tuple[str, str]:
    """Assign a person to a display tier.

    Args:
        person: Person record from the register.

    Returns:
        A (css class, label) pair.
    """
    if person["is_lab_staff"]:
        return ("staff", "lab staff")
    if person["is_fellow"]:
        return ("fellow", "fellow")
    return ("external", "external collaborator")


def person_rows(people: list[dict]) -> str:
    """Render every person as an expandable row, most papers first.

    Args:
        people: Person records from the register.

    Returns:
        HTML string.
    """
    people = sorted(people, key=lambda p: (-p["appearances"], p["name"]))
    top = max(p["appearances"] for p in people)
    out = []
    for i, p in enumerate(people, 1):
        cls, label = classify(p)
        affs = " ".join(
            f'<span class="aff">{esc(a)}</span>' for a in p["affiliations"]
        ) or '<span class="none">no affiliation stated</span>'
        titles = "".join(
            f'<li><a href="{esc(a["url"])}" rel="noopener">{esc(a["title"])}</a>'
            f'<span class="d">{esc(a["date"])}</span></li>'
            for a in sorted(p["papers"], key=lambda a: a["date"], reverse=True)
        )
        out.append(f"""
    <details class="row" data-tier="{cls}">
      <summary>
        <span class="rank">{i}</span>
        <span class="name">{esc(p["name"])}</span>
        <span class="bar"><i style="width:{round(p["appearances"] / top * 100)}%"></i></span>
        <span class="count">{p["appearances"]}</span>
        <span class="flags"><span class="tag t-{cls}">{label}</span></span>
      </summary>
      <div class="affs">{affs}</div>
      <ul class="papers">{titles}</ul>
    </details>""")
    return "".join(out)


def paper_rows(papers: list[dict], url_key: str) -> str:
    """Render the paper table, newest first.

    Args:
        papers: Paper records from the register.
        url_key: Register key holding the lab's own page for the paper.

    Returns:
        HTML string of table rows.
    """
    out = []
    for p in sorted(papers, key=lambda p: p["date"], reverse=True):
        tag, why = RESOLUTION[p.get("resolution")]
        staff = sum(1 for a in p["authors"] if a["is_lab_staff"])
        lab_url = p.get(url_key)
        links = []
        if lab_url:
            links.append(f'<a href="{esc(lab_url)}" rel="noopener">lab</a>')
        if p.get("source_url"):
            links.append(f'<a href="{esc(p["source_url"])}" rel="noopener">arXiv</a>')
        out.append(
            f'<tr><td class="dt">{esc(p["date"])}</td>'
            f'<td class="ti">{esc(p["title"])}</td>'
            f'<td class="n">{len(p["authors"])}</td>'
            f'<td class="n">{staff}</td>'
            f'<td><span class="tag t-res" title="{esc(why)}">{esc(tag)}</span></td>'
            f'<td class="lk">{" ".join(links)}</td></tr>'
        )
    return "".join(out)


def affiliation_rows(people: list[dict], lab_name: str) -> str:
    """Rank the institutions that co-author with the lab.

    The lab's own name is excluded: it is not a downstream source to add, it
    is the lab already being tracked.

    Args:
        people: Person records from the register.
        lab_name: The lab's display name, matched loosely to drop self-entries.

    Returns:
        HTML string of table rows.
    """
    key = lab_name.split()[-1].lower()  # "Google DeepMind" -> "deepmind"
    counts: Counter[str] = Counter()
    for p in people:
        for a in p["affiliations"]:
            if key not in a.lower():
                counts[a] += 1
    if not counts:
        return '<tr><td colspan="3" class="none">No external co-authoring institutions.</td></tr>'
    top = counts.most_common()[0][1]
    return "".join(
        f'<tr><td class="ti">{esc(name)}</td>'
        f'<td class="barcell"><span class="bar"><i style="width:{round(n / top * 100)}%"></i></span></td>'
        f'<td class="n">{n}</td></tr>'
        for name, n in counts.most_common(20)
    )


def render(lab: str) -> str:
    """Build one lab's page.

    Args:
        lab: Register key -- "deepmind", "meta" or "mistral".

    Returns:
        Complete HTML document.
    """
    cfg = LABS[lab]
    reg = json.loads((DOCS / f"{lab}_contributors.json").read_text())
    papers, people = reg["papers"], reg["people"]
    name = reg["lab"]

    staff = [p for p in people if p["is_lab_staff"]]
    external = [p for p in people if not p["is_lab_staff"] and not p["is_fellow"]]
    repeat = [p for p in staff if p["appearances"] > 1]
    resolved = sum(1 for p in papers if p.get("source_url"))
    affs = {a for p in people for a in p["affiliations"]}
    span = f"{min(p['date'] for p in papers)} to {max(p['date'] for p in papers)}"

    def stat(value, caption, note=""):
        return (
            f'<div class="stat"><b>{value}</b><span>{caption}</span>'
            f'<em>{note}</em></div>'
        )

    stats = "".join([
        stat(len(papers), "papers", span),
        stat(f"{resolved}/{len(papers)}", "cite a primary source",
             "every insight needs one"),
        stat(len(people), "distinct authors", f"{len(staff)} lab staff"),
        stat(len(external), "external co-authors", f"across {len(affs)} affiliations"),
        stat(len(repeat), "staff on 2+ papers", "the followable core"),
    ])

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(name)} contributors — papers authored</title>
<style>
  :root{{
    --bg:#0d1117; --panel:#151b23; --line:#2a323d; --tx:#e6edf3;
    --dim:#9aa7b4; --faint:#6e7c8c; --accent:#4c9aff;
    --staff:#3fb950; --fellow:#d29922; --external:#8b949e; --res:#58a6ff;
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
  .stats{{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0 0}}
  .stat{{background:var(--panel);border:1px solid var(--line);border-radius:8px;
    padding:12px 16px;min-width:130px;flex:1}}
  .stat b{{display:block;font-size:22px;letter-spacing:-.02em}}
  .stat span{{display:block;color:var(--faint);font-size:12px;
    text-transform:uppercase;letter-spacing:.05em}}
  .stat em{{display:block;color:var(--faint);font-size:11.5px;font-style:normal;margin-top:4px}}
  .note{{background:var(--panel);border:1px solid var(--line);
    border-left:3px solid var(--accent);border-radius:0 8px 8px 0;
    padding:13px 16px;margin:24px 0 0;font-size:13.5px;color:var(--dim)}}
  .note b{{color:var(--tx);font-weight:600}}
  .note p{{margin:0 0 8px}} .note p:last-child{{margin:0}}
  .scroll{{overflow-x:auto;border:1px solid var(--line);border-radius:8px;
    background:var(--panel)}}
  table{{width:100%;border-collapse:collapse;font-size:13.5px}}
  th{{text-align:left;font-weight:600;font-size:11px;text-transform:uppercase;
    letter-spacing:.05em;color:var(--faint);padding:10px 12px;
    border-bottom:1px solid var(--line);white-space:nowrap}}
  td{{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}}
  tr:last-child td{{border-bottom:0}}
  .n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  .dt{{color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}}
  .ti{{min-width:260px}} .lk{{white-space:nowrap}} .lk a{{margin-right:8px}}
  .barcell{{width:180px}}
  .filters{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}}
  .filters button{{font:inherit;font-size:13px;padding:5px 12px;border-radius:999px;
    border:1px solid var(--line);background:var(--panel);color:var(--tx);cursor:pointer}}
  .filters button[aria-pressed="true"]{{background:var(--tx);color:var(--bg);
    border-color:var(--tx)}}
  .row{{border:1px solid var(--line);border-radius:8px;background:var(--panel);
    margin-bottom:6px}}
  .row summary{{display:flex;align-items:center;gap:12px;padding:9px 14px;
    cursor:pointer;list-style:none}}
  .row summary::-webkit-details-marker{{display:none}}
  .rank{{color:var(--faint);font-size:12px;min-width:26px;
    font-variant-numeric:tabular-nums}}
  .name{{font-weight:600;min-width:190px}}
  .bar{{flex:1;height:6px;background:#0d1117;border-radius:3px;overflow:hidden;
    min-width:60px}}
  .bar i{{display:block;height:100%;background:var(--accent);border-radius:3px}}
  .count{{font-variant-numeric:tabular-nums;color:var(--dim);min-width:20px;
    text-align:right}}
  .flags{{min-width:150px;text-align:right}}
  .tag{{font-size:11px;padding:2px 7px;border-radius:4px;background:#0d1117;
    white-space:nowrap;cursor:help}}
  .t-staff{{color:var(--staff)}} .t-fellow{{color:var(--fellow)}}
  .t-external{{color:var(--external)}} .t-res{{color:var(--res)}}
  .affs{{padding:0 14px 4px 52px}}
  .aff{{display:inline-block;background:#0d1117;border-radius:4px;padding:1px 7px;
    font-size:11.5px;color:var(--dim);margin:0 4px 4px 0}}
  .papers{{margin:0;padding:4px 14px 12px 52px;list-style:none}}
  .papers li{{padding:3px 0;font-size:13px;border-top:1px solid var(--line)}}
  .papers .d{{color:var(--faint);font-size:11.5px;margin-left:8px;
    font-variant-numeric:tabular-nums}}
  .none{{color:var(--faint)}}
  footer{{color:var(--faint);font-size:12px;margin-top:30px;
    border-top:1px solid var(--line);padding-top:14px}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>{esc(name)} — contributors from papers</h1>
  <p class="sub">Every author on every {esc(name)} paper harvested in the window,
  <b>{esc(span)}</b>. Generated {esc(reg["generated"])}.</p>
  <div class="stats">{stats}</div>
</header>

<h2>Papers</h2>
<div class="scroll"><table>
<thead><tr><th>Date</th><th>Title</th><th>Authors</th><th>Staff</th>
<th>arXiv match</th><th>Links</th></tr></thead>
<tbody>{paper_rows(papers, cfg["lab_url_key"])}</tbody>
</table></div>

<h2>Co-authoring institutions</h2>
<p class="sub">Ranked by how many people carry the affiliation. Each is a
candidate source the register does not yet track.</p>
<div class="scroll"><table>
<thead><tr><th>Institution</th><th></th><th>People</th></tr></thead>
<tbody>{affiliation_rows(people, name)}</tbody>
</table></div>

<h2>People</h2>
<div class="filters">
  <button data-f="all" aria-pressed="true">All ({len(people)})</button>
  <button data-f="staff" aria-pressed="false">Lab staff ({len(staff)})</button>
  <button data-f="external" aria-pressed="false">External ({len(external)})</button>
</div>
{person_rows(people)}

<div class="note">
<p><b>What "lab staff" means here.</b> The person carried a {esc(name)}
affiliation on at least one paper's own byline. It is the paper's claim, not an
employment check &mdash; someone who has since left still shows as staff, and a
paper that states no affiliation for an author leaves them external by default.</p>
<p><b>Why the affiliation table matters.</b> Every institution in it is a group
that publishes with this lab and is not itself in the register. That is the
cheapest available list of sources to consider adding next, ranked by how
much of this lab's output they actually touch.</p>
</div>

<footer>Generated from <code>research/docs/{esc(lab)}_contributors.json</code>.
Built by <code>research/papers/build_lab_authors_page.py</code>.</footer>
</div>
<script>
document.querySelectorAll(".filters button").forEach(b => {{
  b.addEventListener("click", () => {{
    document.querySelectorAll(".filters button")
      .forEach(x => x.setAttribute("aria-pressed", x === b));
    const f = b.dataset.f;
    document.querySelectorAll(".row").forEach(r => {{
      r.style.display = (f === "all" || r.dataset.tier === f) ? "" : "none";
    }});
  }});
}});
</script>
</body>
</html>"""


if __name__ == "__main__":
    labs = sys.argv[1:] or list(LABS)
    for lab in labs:
        if lab not in LABS:
            sys.exit(f"unknown lab {lab}; expected one of {', '.join(LABS)}")
        out = OUTDIR / f"{lab}_authors.html"
        out.write_text(render(lab))
        print(f"written {out}")
