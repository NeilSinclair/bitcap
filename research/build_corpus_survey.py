"""Render a cross-lab survey of everything the research legs have collected.

This is the "what do we actually have" page. The per-lab pages in
`research/papers/` and `research/github/` each go deep on one lab; this one
goes wide, so the corpus can be judged as a whole: which labs are covered on
which leg, how strong the evidence behind each leg is, and which concrete
downstream sources the data has already handed us.

Deliberately does not average across labs. The registers were built by
different methods against very different amounts of public output, and a mean
would hide exactly the asymmetries that matter when deciding what to trust.

Usage:
    python research/build_corpus_survey.py
"""

from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "research" / "docs"
OUT = ROOT / "research" / "corpus_survey.html"

SOURCES = ROOT / "config" / "sources.yaml"
GITHUB_SOURCES = ROOT / "config" / "github_sources.yaml"

STAFF_TIERS = ("confirmed", "confirmed_org_wide", "profile", "handle")

# Papers register file stem per lab id, and how that register names its people
# as the lab's own. The three older registers predate the `is_lab_staff` field:
# Anthropic records a byline count instead, and the OpenAI and DeepSeek
# harvesters only ever collected papers whose authors are all the lab's own, so
# there is no staff/external split to report for them.
PAPERS = {
    "anthropic": ("anthropic", "byline"),
    "openai": ("openai", "all_own"),
    "deepseek": ("deepseek", "all_own"),
    "google-deepmind": ("deepmind", "flag"),
    "meta-ai": ("meta", "flag"),
    "mistral": ("mistral", "flag"),
}

PAGE_PAPERS = {
    "anthropic": "papers/anthropic_authors.html",
    "openai": "papers/openai_authors.html",
    "deepseek": "papers/deepseek_authors.html",
    "google-deepmind": "papers/deepmind_authors.html",
    "meta-ai": "papers/meta_authors.html",
    "mistral": "papers/mistral_authors.html",
}


def esc(value) -> str:
    """HTML-escape a value that may be None."""
    return html.escape(str(value)) if value is not None else ""


def primary_source(paper: dict) -> str | None:
    """Return the paper's resolvable primary source, whichever key holds it.

    The three register schemas disagree about where this lives. DeepMind, Meta
    and Mistral store the arXiv HTML in `source_url` and the lab's own page
    separately; OpenAI and DeepSeek put the arXiv abstract URL straight in
    `url`; Anthropic's `url` is the publishing venue itself, which is the
    primary source for work it never puts on arXiv.

    Args:
        paper: One paper record.

    Returns:
        The source URL, or None if the register holds no resolvable link.
    """
    return paper.get("source_url") or paper.get("url") or paper.get("meta_url")


def cite_kind(papers: list[dict]) -> str:
    """Describe what kind of source a register's papers resolve to.

    Args:
        papers: Paper records from one register.

    Returns:
        A short phrase naming the venue type.
    """
    if not papers:
        return "—"
    if all(p.get("source_url") for p in papers):
        return "arXiv + lab page"
    if all(p.get("arxiv_id") for p in papers):
        return "arXiv"
    return "publishing venue"


def load_papers(lab: str) -> dict | None:
    """Normalise one lab's papers register across the two register schemas.

    Args:
        lab: Lab id from config/sources.yaml.

    Returns:
        A dict of comparable counts, or None if the lab has no papers register.
        `staff` is None where the register cannot express the distinction
        rather than 0, which would read as "we checked and found none".
    """
    if lab not in PAPERS:
        return None
    stem, kind = PAPERS[lab]
    path = DOCS / f"{stem}_contributors.json"
    if not path.exists():
        return None
    reg = json.loads(path.read_text())
    papers = reg.get("papers") or reg.get("articles") or []
    people = reg["people"]

    if kind == "flag":
        staff = sum(1 for p in people if p.get("is_lab_staff"))
    elif kind == "byline":
        staff = sum(1 for p in people if p.get("anthropic_bylines", 0) > 0)
    else:
        staff = None  # register holds only the lab's own authors by construction

    affs = Counter(a for p in people for a in p.get("affiliations", []))
    return {
        "papers": len(papers),
        "people": len(people),
        "staff": staff,
        "repeat": sum(1 for p in people if p["appearances"] > 1),
        "resolved": sum(1 for p in papers if primary_source(p)),
        "cite_kind": cite_kind(papers),
        "affiliations": affs,
        "span": (
            f"{min(p['date'] for p in papers)} to {max(p['date'] for p in papers)}"
            if papers
            else "—"
        ),
    }


def load_github(org: str) -> dict:
    """Load one GitHub org's register, enriched if that pass has been run.

    Args:
        org: GitHub organisation login.

    Returns:
        Counts plus the concrete downstream channels the org's staff expose.
    """
    enriched = DOCS / f"github_enriched_{org}.json"
    is_enriched = enriched.exists()
    reg = json.loads(
        (enriched if is_enriched else DOCS / f"github_people_{org}.json").read_text()
    )
    people = reg["people"]
    t = reg["totals"]
    staff = [p for p in people if p["employment"] in STAFF_TIERS]
    blogs = {
        p["profile"]["blog"] for p in staff if p.get("profile", {}).get("blog")
    }
    handles = {
        p["profile"]["twitter"] for p in staff if p.get("profile", {}).get("twitter")
    }
    own = sum(
        len(p.get("profile", {}).get("personal_repos", []) or []) for p in staff
    )
    return {
        "org": org,
        "enriched": is_enriched,
        "repos": t["repos"],
        "commits": t["commits"],
        "human_commits": t["commits"] - t["bot_commits"],
        "people": t["people"],
        "staff": len(staff),
        "confirmed": t["employment"].get("confirmed", 0),
        "org_wide": t["employment"].get("confirmed_org_wide", 0),
        "profile": t["employment"].get("profile", 0),
        "blogs": blogs,
        "handles": handles,
        "own_repos": own,
    }


def evidence_note(cfg: dict, gh: dict) -> tuple[str, str]:
    """Describe the strength of an org's employment evidence.

    Args:
        cfg: The org's config/github_sources.yaml entry.
        gh: The org's loaded register counts.

    Returns:
        A (css class, phrase) pair.
    """
    if not cfg.get("domain"):
        return ("weak", "no commit-email evidence exists; profile field only")
    if cfg.get("domain_shared"):
        return ("mid", "parent-company domain, not lab-specific")
    return ("ok", "lab-owned domain")


def build() -> str:
    """Assemble the survey page.

    Returns:
        Complete HTML document.
    """
    reg = yaml.safe_load(SOURCES.read_text())
    window = reg["window_months"]
    lab_ids = [lab["id"] for lab in reg["labs"]]
    lab_label = {lab["id"]: lab.get("label") or lab["id"] for lab in reg["labs"]}

    org_cfg = yaml.safe_load(GITHUB_SOURCES.read_text())["orgs"]
    orgs_by_lab: dict[str, list[str]] = {}
    for org, cfg in org_cfg.items():
        orgs_by_lab.setdefault(cfg["lab"], []).append(org)

    ann = Counter(a["lab"] for a in json.loads((DOCS / "announcements.json").read_text()))
    gh = {org: load_github(org) for org in org_cfg}
    papers = {lab: load_papers(lab) for lab in lab_ids}

    # ---- corpus totals -------------------------------------------------
    all_blogs = set().union(*(g["blogs"] for g in gh.values()))
    all_handles = set().union(*(g["handles"] for g in gh.values()))
    tot_org_repos = sum(g["repos"] for g in gh.values())
    tot_own_repos = sum(g["own_repos"] for g in gh.values())
    tot_staff = sum(g["staff"] for g in gh.values())
    tot_papers = sum(p["papers"] for p in papers.values() if p)
    tot_authors = sum(p["people"] for p in papers.values() if p)
    unenriched = [g["org"] for g in gh.values() if not g["enriched"]]

    def stat(value, caption, note=""):
        return (
            f'<div class="stat"><b>{value}</b><span>{caption}</span>'
            f"<em>{note}</em></div>"
        )

    stats = "".join([
        stat(len(lab_ids), "labs in the register", f"{window}-month window"),
        stat(f"{tot_org_repos:,}", "lab-owned repositories",
             "each one a release and changelog feed"),
        stat(f"{tot_staff:,}", "people evidenced as staff",
             "across all GitHub orgs"),
        stat(f"{len(all_blogs)}+{len(all_handles)}", "blogs and X handles",
             "personal channels, staff only"),
        stat(f"{tot_own_repos:,}", "staff personal repositories",
             "side projects, published early"),
        stat(f"{tot_papers}", "papers harvested", f"{tot_authors:,} author slots"),
    ])

    # ---- coverage matrix -----------------------------------------------
    matrix = []
    for lab in lab_ids:
        p = papers[lab]
        orgs = orgs_by_lab.get(lab, [])
        gh_repos = sum(gh[o]["repos"] for o in orgs)
        gh_staff = sum(gh[o]["staff"] for o in orgs)
        n_ann = ann.get(lab, 0)

        def cell(ok, main, sub):
            klass = "yes" if ok else "no"
            return f'<td class="cell {klass}"><b>{main}</b><span>{sub}</span></td>'

        matrix.append(
            f'<tr><td class="lab">{esc(lab_label[lab])}</td>'
            + cell(n_ann > 0, n_ann, "announcements")
            + (
                cell(True, p["papers"], f'{p["people"]} authors')
                if p
                else cell(False, "—", "no papers leg")
            )
            + cell(bool(orgs), gh_repos or "—",
                   f'{len(orgs)} org{"s" if len(orgs) != 1 else ""}, {gh_staff} staff'
                   if orgs else "no org tracked")
            + "</tr>"
        )

    # ---- github table ---------------------------------------------------
    gh_rows = []
    for org, g in sorted(gh.items(), key=lambda kv: -kv[1]["repos"]):
        cfg = org_cfg[org]
        klass, phrase = evidence_note(cfg, g)
        chan = (
            f'{len(g["blogs"])} blogs · {len(g["handles"])} X · {g["own_repos"]:,} repos'
            if g["enriched"]
            else '<span class="warn">not enriched</span>'
        )
        gh_rows.append(
            f'<tr><td class="ti"><a href="github/github_{esc(org)}.html">{esc(cfg["label"])}</a>'
            f'<span class="sub2">{esc(org)}</span></td>'
            f'<td class="n">{g["repos"]:,}</td>'
            f'<td class="n">{g["human_commits"]:,}</td>'
            f'<td class="n">{g["people"]:,}</td>'
            f'<td class="n">{g["staff"]:,}</td>'
            f'<td><span class="tag t-{klass}">{esc(phrase)}</span></td>'
            f'<td class="ch">{chan}</td></tr>'
        )

    # ---- papers table ---------------------------------------------------
    pa_rows = []
    for lab in lab_ids:
        p = papers[lab]
        if not p:
            pa_rows.append(
                f'<tr class="empty"><td class="ti">{esc(lab_label[lab])}</td>'
                f'<td colspan="7" class="none">No papers leg &mdash; nothing '
                f"findable by any method tried.</td></tr>"
            )
            continue
        ok = p["resolved"] == p["papers"]
        cite = (
            f'{p["resolved"]}/{p["papers"]}'
            if ok
            else f'<span class="warn">{p["resolved"]}/{p["papers"]}</span>'
        )
        staff = p["staff"] if p["staff"] is not None else "—"
        pa_rows.append(
            f'<tr><td class="ti"><a href="{esc(PAGE_PAPERS[lab])}">{esc(lab_label[lab])}</a></td>'
            f'<td class="n">{p["papers"]}</td>'
            f'<td class="n">{p["people"]:,}</td>'
            f'<td class="n">{staff}</td>'
            f'<td class="n">{p["repeat"]}</td>'
            f'<td class="n">{cite}</td>'
            f'<td class="dt">{esc(p["cite_kind"])}</td>'
            f'<td class="dt">{esc(p["span"])}</td></tr>'
        )

    # ---- candidate institutions ----------------------------------------
    # Only the registers that record affiliations can contribute. An
    # institution is a candidate source when it co-authors with a tracked lab
    # and is not itself one.
    lab_words = {"anthropic", "openai", "deepmind", "google", "meta", "facebook",
                 "fair", "mistral", "deepseek", "xai"}
    inst = Counter()
    for p in papers.values():
        if p:
            for name, n in p["affiliations"].items():
                if not any(w in name.lower() for w in lab_words):
                    inst[name] += n
    top_inst = inst.most_common(15)
    inst_rows = "".join(
        f'<tr><td class="ti">{esc(name)}</td>'
        f'<td class="barcell"><span class="bar">'
        f'<i style="width:{round(n / top_inst[0][1] * 100)}%"></i></span></td>'
        f'<td class="n">{n}</td></tr>'
        for name, n in top_inst
    ) or '<tr><td colspan="3" class="none">None.</td></tr>'

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    unenriched_list = ", ".join(f"<code>{esc(o)}</code>" for o in unenriched)
    unenriched_note = (
        f"<p><b>Two orgs are unenriched.</b> {unenriched_list} "
        "have had no profile pass run, so their Channels column is empty. That is "
        "the largest single gap on this page: together they are "
        f'{sum(gh[o]["staff"] for o in unenriched):,} evidenced staff whose blogs, X '
        "handles and personal repositories have simply not been fetched yet. Their "
        "repository and commit data is complete.</p>"
        if unenriched
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Frontier lab corpus — what we have collected</title>
<style>
  :root{{
    --bg:#0d1117; --panel:#151b23; --line:#2a323d; --tx:#e6edf3;
    --dim:#9aa7b4; --faint:#6e7c8c; --accent:#4c9aff;
    --ok:#3fb950; --mid:#d29922; --weak:#f85149;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--tx);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased}}
  a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
  .wrap{{max-width:1080px;margin:0 auto;padding:0 22px 80px}}
  header{{border-bottom:1px solid var(--line);padding:34px 0 22px;margin-bottom:26px}}
  h1{{margin:0 0 6px;font-size:27px;letter-spacing:-.02em}}
  h2{{font-size:15px;margin:38px 0 6px;color:var(--dim);font-weight:600;
    text-transform:uppercase;letter-spacing:.06em}}
  .sub{{color:var(--dim);font-size:14px;margin:0 0 12px}}
  .sub b{{color:var(--tx);font-weight:600}}
  .stats{{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0 0}}
  .stat{{background:var(--panel);border:1px solid var(--line);border-radius:8px;
    padding:12px 15px;flex:1;min-width:150px}}
  .stat b{{display:block;font-size:21px;letter-spacing:-.02em}}
  .stat span{{display:block;color:var(--faint);font-size:11.5px;
    text-transform:uppercase;letter-spacing:.05em;margin-top:2px}}
  .stat em{{display:block;color:var(--faint);font-size:11.5px;font-style:normal;
    margin-top:5px;line-height:1.4}}
  .scroll{{overflow-x:auto;border:1px solid var(--line);border-radius:8px;
    background:var(--panel)}}
  table{{width:100%;border-collapse:collapse;font-size:13.5px}}
  th{{text-align:left;font-weight:600;font-size:11px;text-transform:uppercase;
    letter-spacing:.05em;color:var(--faint);padding:10px 12px;
    border-bottom:1px solid var(--line);white-space:nowrap}}
  td{{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:middle}}
  tr:last-child td{{border-bottom:0}}
  .n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  .dt{{color:var(--dim);white-space:nowrap;font-size:12px;
    font-variant-numeric:tabular-nums}}
  .ti{{min-width:170px;font-weight:600}}
  .sub2{{display:block;color:var(--faint);font-size:11.5px;font-weight:400}}
  .lab{{font-weight:600;min-width:150px}}
  .cell{{text-align:center;min-width:110px}}
  .cell b{{display:block;font-size:17px;font-variant-numeric:tabular-nums}}
  .cell span{{display:block;color:var(--faint);font-size:11px;margin-top:1px}}
  .cell.no b{{color:var(--faint)}}
  .cell.yes b{{color:var(--tx)}}
  .tag{{font-size:11px;padding:2px 8px;border-radius:4px;background:#0d1117;
    white-space:nowrap}}
  .t-ok{{color:var(--ok)}} .t-mid{{color:var(--mid)}} .t-weak{{color:var(--weak)}}
  .warn{{color:var(--mid)}}
  .ch{{font-size:12px;color:var(--dim);white-space:nowrap}}
  .barcell{{width:200px}}
  .bar{{display:block;height:6px;background:#0d1117;border-radius:3px;overflow:hidden}}
  .bar i{{display:block;height:100%;background:var(--accent);border-radius:3px}}
  .note{{background:var(--panel);border:1px solid var(--line);
    border-left:3px solid var(--accent);border-radius:0 8px 8px 0;
    padding:14px 17px;margin:14px 0 0;font-size:13.5px;color:var(--dim)}}
  .note.warnbox{{border-left-color:var(--mid)}}
  .note b{{color:var(--tx);font-weight:600}}
  .note p{{margin:0 0 9px}} .note p:last-child{{margin:0}}
  .note code{{background:#0d1117;padding:1px 5px;border-radius:3px;font-size:12px}}
  .none{{color:var(--faint)}}
  .empty td{{opacity:.65}}
  footer{{color:var(--faint);font-size:12px;margin-top:36px;
    border-top:1px solid var(--line);padding-top:14px}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Frontier lab corpus &mdash; what we have collected</h1>
  <p class="sub">Every research leg, every lab, as of {generated}. The per-lab
  pages go deep on one lab each; this one goes wide, to show where the corpus is
  solid and where it is thin.</p>
  <div class="stats">{stats}</div>
</header>

<h2>Coverage</h2>
<p class="sub">Seven labs in the register, three ingestion legs. A dash is a
real absence, not a pending task.</p>
<div class="scroll"><table>
<thead><tr><th>Lab</th><th>Announcements</th><th>Papers</th><th>GitHub</th></tr></thead>
<tbody>{"".join(matrix)}</tbody>
</table></div>

<h2>GitHub &mdash; what sources this leg hands us</h2>
<p class="sub">Two kinds of source come out of this leg. The <b>repositories</b>
are sources in their own right: {tot_org_repos:,} of them, each with releases,
tags and a changelog. The <b>people</b> are a route to more &mdash; a blog, an X
handle, or their own side projects, which is often where work appears before the
lab announces it.</p>
<div class="scroll"><table>
<thead><tr><th>Organisation</th><th>Repos</th><th>Human commits</th><th>People</th>
<th>Staff</th><th>Evidence quality</th><th>Channels found</th></tr></thead>
<tbody>{"".join(gh_rows)}</tbody>
</table></div>

<div class="note warnbox">
<p><b>Evidence quality is not comparable across labs, and the column says so.</b>
Anthropic, OpenAI, DeepSeek and xAI have lab-owned commit domains, so a match
names the lab. Google DeepMind and both Meta orgs only have parent-company
domains &mdash; an <code>@google.com</code> commit proves Alphabet, not DeepMind.
Mistral has no usable commit-email evidence at all; every person identified
there comes from the profile field alone.</p>
{unenriched_note}
</div>

<h2>Papers &mdash; what this leg gives us downstream</h2>
<p class="sub">Three things worth using: a <b>resolvable primary source</b> per
paper, a set of <b>named people</b> to follow, and the <b>institutions</b> those
people co-author with.</p>
<div class="scroll"><table>
<thead><tr><th>Lab</th><th>Papers</th><th>Author slots</th><th>Lab staff</th>
<th>On 2+ papers</th><th>Cites source</th><th>Source kind</th><th>Window</th></tr></thead>
<tbody>{"".join(pa_rows)}</tbody>
</table></div>

<div class="note">
<p><b>The two register schemas are not the same, and the table does not pretend
otherwise.</b> DeepMind, Meta and Mistral record a per-author affiliation, so a
staff/external split is real for them. Anthropic records a byline count instead.
The OpenAI and DeepSeek harvesters only ever collected papers whose authors are
all the lab's own, so a "lab staff" figure would be the author count restated
&mdash; shown as a dash rather than a number that means something different.</p>
<p><b>Every paper in every register resolves to a primary source, but not under
the same key.</b> DeepMind, Meta and Mistral store the arXiv HTML in
<code>source_url</code> alongside the lab's own page. OpenAI and DeepSeek put
the arXiv abstract URL straight in <code>url</code>. Anthropic's
<code>url</code> is the publishing venue itself &mdash;
<code>alignment.anthropic.com</code>, <code>transformer-circuits.pub</code>
&mdash; which is the primary source for work it never puts on arXiv, not a
weaker substitute for one. Anything reading across registers has to normalise
all three shapes.</p>
</div>

<h2>Candidate sources the papers already name</h2>
<p class="sub">Institutions that co-author with a tracked lab and are not
themselves tracked. Ranked by how many people carry the affiliation &mdash; the
cheapest available shortlist of what to consider adding next.</p>
<div class="scroll"><table>
<thead><tr><th>Institution</th><th></th><th>People</th></tr></thead>
<tbody>{inst_rows}</tbody>
</table></div>

<div class="note warnbox">
<p><b>These are raw affiliation strings, deliberately unmerged.</b> "MATS", "MATS
Program" and "ML Alignment and Theory Scholars" are one organisation appearing
three times, and several university entries differ only by campus or comma. The
counts are therefore a floor, not a total. Merging them needs an alias map
&mdash; the same entity-resolution problem the register already handles for
people &mdash; and guessing at it here would bury the collision rather than
show it. <code>Independent</code> is not an institution at all and is listed
only because that is what the byline said.</p>
</div>

<footer>Generated {generated} by <code>research/build_corpus_survey.py</code>
from <code>research/docs/</code>. Register: <code>config/sources.yaml</code>,
<code>config/github_sources.yaml</code>.</footer>
</div>
</body>
</html>"""


if __name__ == "__main__":
    OUT.write_text(build())
    print(f"written {OUT}")
