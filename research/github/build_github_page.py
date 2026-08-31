"""Render the GitHub contributor register as a browsable HTML page.

The page is a research artefact, not a product surface: it exists so the
register can be read and challenged by eye. Every person shows the evidence
behind their employment classification rather than only the verdict.
"""

from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"

LABS = {
    "anthropics": ("Anthropic", "anthropic.com"),
    "openai": ("OpenAI", "openai.com"),
}

EVIDENCE = {
    "confirmed": ("commit email", "Committed under an @{domain} address."),
    "profile": ("profile", "GitHub profile names the lab as their employer."),
    "handle": ("handle", "Work-account naming convention only; weakest signal."),
    "vendor": ("vendor", "Commits under a vendor domain; contractor, not staff."),
    "unknown": ("none", "No employment evidence found."),
    "deleted": ("gone", "Account no longer exists."),
}


def esc(text) -> str:
    """HTML-escape a value that may be None."""
    return html.escape(str(text)) if text else ""


def person_row(p: dict, domain: str) -> str:
    """Render one contributor as a table row plus a detail row.

    Args:
        p: Enriched person record.
        domain: Lab email domain, for the evidence caption.

    Returns:
        HTML string for both rows.
    """
    prof = p.get("profile", {})
    name = prof.get("name") or (p["names"][0] if p["names"] else p["login"])
    tag, why = EVIDENCE[p["employment"]]
    why = why.format(domain=domain)

    channels = []
    if prof.get("blog"):
        url = prof["blog"]
        if not url.startswith("http"):
            url = "https://" + url
        channels.append(f'<a href="{esc(url)}" rel="noopener">site</a>')
    if prof.get("twitter"):
        channels.append(
            f'<a href="https://x.com/{esc(prof["twitter"])}" rel="noopener">'
            f'@{esc(prof["twitter"])}</a>'
        )

    repos = list(p["repos"])[:4]
    repo_html = " ".join(
        f'<span class="repo">{esc(r)}</span>' for r in repos
    )
    if len(p["repos"]) > 4:
        repo_html += f' <span class="more">+{len(p["repos"]) - 4}</span>'

    own = sorted(
        prof.get("personal_repos", []), key=lambda r: -r["stars"]
    )[:3]
    own_html = (
        " ".join(
            f'<a class="own" href="https://github.com/{esc(p["login"])}/{esc(r["name"])}"'
            f' rel="noopener">{esc(r["name"])} ★{r["stars"]}</a>'
            for r in own
        )
        or '<span class="none">—</span>'
    )

    aliases = (
        f' <span class="alias">+{esc(", ".join(p["merged_from"]))}</span>'
        if p["merged_from"]
        else ""
    )

    return f"""
<tr class="p" data-emp="{p['employment']}">
  <td class="num">{p['commits']}</td>
  <td class="num">{p['repo_count']}</td>
  <td class="who">
    <a href="https://github.com/{esc(p['login'])}" rel="noopener">{esc(p['login'])}</a>{aliases}
    <div class="name">{esc(name)}</div>
  </td>
  <td><span class="tag t-{p['employment']}" title="{esc(why)}">{esc(tag)}</span></td>
  <td class="rep">{repo_html}</td>
  <td class="ch">{" ".join(channels) or '<span class="none">—</span>'}</td>
  <td class="own-c">{own_html}</td>
  <td class="num dt">{esc(p['last_commit'][:10])}</td>
</tr>"""


def build(org: str) -> str:
    """Assemble the full page.

    Args:
        org: GitHub organisation login.

    Returns:
        HTML document body.
    """
    label, domain = LABS[org]
    reg = json.loads((DOCS / f"github_enriched_{org}.json").read_text())
    t = reg["totals"]
    emp = t["employment"]
    staff = [
        p for p in reg["people"] if p["employment"] in ("confirmed", "profile", "handle")
    ]
    rows = "".join(person_row(p, domain) for p in reg["people"])

    def card(caption, value, note=""):
        return (
            f'<div class="card"><div class="v">{value}</div>'
            f'<div class="l">{caption}</div><div class="n">{note}</div></div>'
        )

    cards = "".join(
        [
            card("repositories", t["repos"], "pushed in last 12 months"),
            card("human commits", f"{t['commits'] - t['bot_commits']:,}",
                 f"{t['bot_commits']:,} bot commits removed"),
            card("distinct people", t["people"], f"{len(reg['aliases'])} accounts merged"),
            card("identified as staff", len(staff),
                 f"{emp['confirmed']} by commit email, {emp['profile']} by profile"),
        ]
    )

    mirrors = ", ".join(reg.get("mirrors_excluded", [])) or "none"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return f"""<title>{label} GitHub Register</title>
<style>
:root {{
  --bg: #fbfaf8; --fg: #1a1a18; --muted: #6b6b64; --line: #e2e0da;
  --card: #ffffff; --accent: #b8552e; --chip: #f0eee8;
  --ok: #2d6a4a; --mid: #8a6d1f; --weak: #8a4a4a;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #16161a; --fg: #e8e6e1; --muted: #93918a; --line: #2c2c33;
    --card: #1d1d22; --accent: #e08a5c; --chip: #26262c;
    --ok: #6bbf8f; --mid: #d4b155; --weak: #d98a8a;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #16161a; --fg: #e8e6e1; --muted: #93918a; --line: #2c2c33;
  --card: #1d1d22; --accent: #e08a5c; --chip: #26262c;
  --ok: #6bbf8f; --mid: #d4b155; --weak: #d98a8a;
}}
* {{ box-sizing: border-box; }}
body {{
  background: var(--bg); color: var(--fg); margin: 0;
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 40px 22px 80px; }}
h1 {{ font-size: 27px; margin: 0 0 6px; letter-spacing: -0.02em; }}
.sub {{ color: var(--muted); margin: 0 0 30px; font-size: 14px; }}
.cards {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); margin-bottom: 26px; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 9px; padding: 15px 16px; }}
.card .v {{ font-size: 25px; font-weight: 600; letter-spacing: -0.02em; }}
.card .l {{ font-size: 13px; margin-top: 1px; }}
.card .n {{ font-size: 11.5px; color: var(--muted); margin-top: 5px; }}
.note {{ background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--accent);
  border-radius: 0 8px 8px 0; padding: 13px 16px; margin: 0 0 26px; font-size: 13.5px; }}
.note b {{ font-weight: 600; }}
.note p {{ margin: 0 0 8px; }} .note p:last-child {{ margin: 0; }}
.filters {{ display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 14px; }}
.filters button {{ font: inherit; font-size: 13px; padding: 5px 12px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--card); color: var(--fg); cursor: pointer; }}
.filters button[aria-pressed="true"] {{ background: var(--fg); color: var(--bg); border-color: var(--fg); }}
.scroll {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 9px; background: var(--card); }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
th {{ text-align: left; font-weight: 600; font-size: 11.5px; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--muted); padding: 11px 10px; border-bottom: 1px solid var(--line);
  white-space: nowrap; position: sticky; top: 0; background: var(--card); }}
td {{ padding: 9px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }}
tr:last-child td {{ border-bottom: 0; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.dt {{ color: var(--muted); font-size: 12px; }}
.who a {{ color: var(--fg); text-decoration: none; font-weight: 600; }}
.who a:hover {{ color: var(--accent); }}
.who .name {{ color: var(--muted); font-size: 12px; }}
.alias {{ color: var(--muted); font-size: 11px; font-weight: 400; }}
.tag {{ font-size: 11px; padding: 2px 7px; border-radius: 4px; background: var(--chip);
  white-space: nowrap; cursor: help; }}
.t-confirmed {{ color: var(--ok); }} .t-profile {{ color: var(--ok); }}
.t-handle {{ color: var(--mid); }} .t-vendor {{ color: var(--weak); }}
.repo {{ display: inline-block; background: var(--chip); border-radius: 4px;
  padding: 1px 6px; font-size: 11.5px; margin: 1px 2px 1px 0; }}
.more, .none {{ color: var(--muted); font-size: 11.5px; }}
.ch a, .own {{ color: var(--accent); text-decoration: none; font-size: 12px;
  margin-right: 7px; white-space: nowrap; }}
.ch a:hover, .own:hover {{ text-decoration: underline; }}
.own-c {{ max-width: 260px; }}
footer {{ color: var(--muted); font-size: 12px; margin-top: 24px; }}
</style>
<div class="wrap">
<h1>{label} GitHub Register</h1>
<p class="sub">Contributors to the <code>{org}</code> organisation, 12 months to {generated}.</p>

<div class="cards">{cards}</div>

<div class="note">
<p><b>How employment is decided.</b> A commit made from an
<code>@{domain}</code> address is direct evidence and outranks everything else
({emp['confirmed']} people). A GitHub profile naming the lab is accepted next
({emp['profile']}). The <code>-ant</code> work-handle convention is used only when
neither is present ({emp['handle']}), and is the one signal here that is a guess.</p>
<p><b>What was removed.</b> {t['bot_commits']:,} of {t['commits']:,} commits were made by
release automation and code generators, which otherwise outrank every human in the
org. Mirrored repositories were excluded ({esc(mirrors)}): the first is an upstream
open-source project whose contributors are not staff, the second duplicates commits
already counted elsewhere.</p>
<p><b>Read the ranking with care.</b> Commit count measures who maintains public
code, not seniority or influence. It is shown alongside repository breadth and
recency rather than collapsed into a single score.</p>
</div>

<div class="filters">
  <button data-f="all" aria-pressed="true">All ({t['people']})</button>
  <button data-f="staff" aria-pressed="false">Staff ({len(staff)})</button>
  <button data-f="confirmed" aria-pressed="false">Commit-email confirmed ({emp['confirmed']})</button>
  <button data-f="unknown" aria-pressed="false">No evidence ({emp['unknown']})</button>
</div>

<div class="scroll"><table>
<thead><tr>
  <th>Commits</th><th>Repos</th><th>Contributor</th><th>Evidence</th>
  <th>Where they commit</th><th>Channels</th><th>Own projects</th><th>Last seen</th>
</tr></thead>
<tbody>{rows}</tbody>
</table></div>

<footer>Generated {generated} from the GitHub REST and GraphQL APIs.
Source: <code>research/github/</code>. Counts cover the default branch only.</footer>
</div>
<script>
const staffSet = ["confirmed", "profile", "handle"];
document.querySelectorAll(".filters button").forEach(b => {{
  b.addEventListener("click", () => {{
    document.querySelectorAll(".filters button")
      .forEach(x => x.setAttribute("aria-pressed", x === b));
    const f = b.dataset.f;
    document.querySelectorAll("tr.p").forEach(r => {{
      const e = r.dataset.emp;
      const show = f === "all" || (f === "staff" ? staffSet.includes(e) : e === f);
      r.style.display = show ? "" : "none";
    }});
  }});
}});
</script>"""


if __name__ == "__main__":
    org = sys.argv[1] if len(sys.argv) > 1 else "anthropics"
    out = ROOT / "research" / "github" / f"github_{org}.html"
    out.write_text(build(org))
    print(f"written {out}")
