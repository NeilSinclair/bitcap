# Frontend & API — handoff

What exists, what's real vs. stubbed, and what to know before wiring this up
to a live/automated ingestion pipeline. Written for whoever picks this up
next; the full diff is commit `cb02d9e` ("Built the frontend"), and the two
logged design decisions are in [decisions.md](decisions.md) under "A category
row loses to a contradicting mechanism row on the same holding". See
[next_steps_0309.md](next_steps_0309.md) for the broader roadmap this sits
inside — this doc only covers what's already built.

## Architecture

```
Postgres (app/models.py schema)
  ▲
  │ read-only, SQLAlchemy
  │
api/  (FastAPI: api/main.py, api/queries.py)
  ▲
  │ HTTP/JSON, no auth
  │
frontend/  (Next.js, client-rendered: frontend/app/page.js)
```

`api/` and `frontend/` are new. Everything upstream of Postgres — ingestion,
scoring, the `connections` join — is the existing `app/`/`research/` pipeline,
run manually via `bitcap-db` and the `research/announcements/*.py` scripts.
**Nothing new here triggers, schedules, or monitors ingestion.** The frontend
reads whatever is already in the database at page-load time and nothing else.

## What's real vs. what's a stub

Real: the database connection, every article/score/connection shown (it's
your actual 191-article corpus), the scoring and join logic underneath it.

Stubbed:
- **Login** — `frontend/app/page.js`'s `screen` state, `setScreen("dashboard")`
  on click. No credential check, no session, no server-side gate. `/api/items`
  and `/api/status` are open to anyone who can reach the port.
- **Everything design_thoughts.md asked for beyond the core loop** — register
  browser, report history, model-drift/reliability tab, alerts. Deliberately
  scoped out (Neil's call, mid-build) in favor of shipping login → two
  dashboards → detail view well. `next_steps_0309.md`'s "Design" section is
  effectively the backlog for this.
- **Deployment** — runs locally only (`docker compose`, two `uv run`/`npm run
  dev` processes). Not on Railway/Render yet.

## Files

| File | What it does |
|---|---|
| `api/main.py` | FastAPI app. Two endpoints, no others. One module-level `engine`, reused across requests (was created per-request originally — leaked a Postgres connection every call until fixed; don't reintroduce that). |
| `api/queries.py` | `build_items(session, prompt_version)` — the one query. Joins articles + classifications + tags + connections into the nested shape the frontend renders, resolving every ref id (mechanism/category/practice/holding) to its display label. This is where you'd add fields if the frontend needs more. |
| `frontend/app/page.js` | The entire app — login screen, dashboard, detail panel — as one client component (453 lines). Not split into components; that was a deliberate simplicity call for a single-page prototype, not an oversight. Fetches `/api/items` and `/api/status` once on mount; no polling, no refresh. |
| `frontend/app/globals.css` | Design tokens (colors, spacing) lifted from BIT Capital's actual marketing site, not invented. |
| `frontend/app/layout.js` | Root layout, Google Fonts link (Source Serif 4). |
| `app/connect.py` | Unchanged in shape; gained `_drop_contradicted_category_rows`, called at the end of `connections_for`. See decisions.md — this is a data-generation-time rule, not a display filter, so it applies no matter what reads `connections` next. |

## Endpoints

**`GET /api/items`** — every article with a classification for `PROMPT_VERSION`
(imported from `app.cli`, currently `"v7"`), each with its mechanism/category/
practice tags and holding connections inline. No pagination, no query params —
the whole corpus in one response, filtered and sorted client-side. This was
fine at ~200 rows; revisit before it isn't (see "Before you scale this up"
below).

**`GET /api/status`** — the single most recent `pipeline_runs` row: `status`,
`kind`, `started_at`, `finished_at`, `cost_usd`. That's it — `stats`,
`watermarks`, and `error` exist on the `PipelineRun` model and are populated
by every real run, but nothing here reads them, and there's no history
endpoint (only the latest row). If you're building a run-history or
system-failure view, this is the gap to fill first.

## What "connecting the ingestion pipeline to the frontend" actually means here

Right now these are two separate worlds that happen to share a database:

1. **Ingestion** (`research/announcements/fetch_announcements.py` →
   `score_announcements.py` → `bitcap-db load`) runs by hand, from a terminal,
   whenever someone runs it. Nothing schedules it. Nothing in `api/` or
   `frontend/` can start, stop, or watch a run in progress.
2. **The frontend** shows whatever is in Postgres *right now*, once, on page
   load. If ingestion runs while someone has the dashboard open, they will not
   see new data without reloading the page.

Concretely, things to know before extending this:

- **`PROMPT_VERSION` is one constant** (`app/cli.py:28`), imported by both the
  CLI and `api/main.py`. If a new classifier version ships, `bitcap-db load`
  and this constant have to move together — bump one without the other and
  the API either serves nothing (wrong version has no rows) or, worse, serves
  article text/scores from one version next to `connections` rows built from
  a different one (see decisions.md's note on this — it's a known sharp edge,
  not fixed, just documented).
- **`connections` has no version column.** `connect()` does a wholesale
  delete-and-rebuild every time it runs, for whichever `prompt_version` it's
  given. `bitcap-db load` already chains `transform` + `connect` on the same
  version, so this is safe *through the CLI*. It would stop being safe the
  moment something calls `connect()` on its own with a different version than
  whatever `transform` last used — worth an assertion if a scheduler starts
  calling these independently.
- **No live signal at all.** No websocket, no SSE, no polling interval. A
  scheduled worker (the "Deployment" section of `next_steps_0309.md`) writing
  to Postgres in the background will be invisible to an open browser tab.
- **`pipeline_runs.error` and `.alerted_at` are unused by the UI.** The schema
  already carries what CLAUDE.md's "system-failure alerting distinct from
  content alerts" non-negotiable needs; nothing surfaces it yet.

## Design decisions worth knowing before you touch the query/display layer

Full rationale for both is in `decisions.md`:

- **Category-vs-mechanism contradiction suppression** lives in
  `app/connect.py`, at data-generation time — not a frontend filter. If a
  holding's own mechanism-route evidence contradicts a category-route claim
  on the same article, the category row is dropped. Applies to every future
  consumer of `connections`, not just this frontend.
- **Portfolio-impact connections are grouped by holding** in the detail panel
  (`api/queries.py`'s `_connection_label` resolves a real label per route;
  `frontend/app/page.js`'s `connectionGroups` groups and sorts). A flat list
  of routes was unreadable on articles that touch a dozen holdings.
- **Audience filtering is client-side**, not server-side: `/api/items` returns
  everything, and `frontend/app/page.js`'s `visible` memo decides which items
  show for "Investment" (`score > 0`) vs. "AI team" (`aiScore > 0`), and
  applies the band filter and sort. Moving this server-side is one of the
  first things worth doing if the item count grows.

## Before you scale this up

`/api/items` shipping the entire corpus in one response, unpaginated, was a
deliberate simplicity call for ~200 rows. `next_steps_0309.md`'s data-
collection items (more labs, X, a leadership register) will multiply that
row count. Add pagination/filtering to `api/queries.py` and the endpoint
before that happens, rather than after the frontend starts hanging.

## Running it locally

```bash
docker compose up -d                                 # Postgres, if not already running
uv run uvicorn api.main:app --reload --port 8000      # http://localhost:8000
cd frontend && npm run dev                            # http://localhost:3000
```

`DATABASE_URL` is already in `.env` (gitignored, not committed — a fresh
clone needs the quickstart's `echo ... >> .env` step in the README run once).

## Recent review

A full adversarial review (reproducing the repo's `bitcap-reviewer` agent
definition) ran against this work: one CRITICAL (the per-request connection
leak in `api/main.py`, fixed), three MAJOR (the `PROMPT_VERSION` duplication,
a README gap, a missing decisions.md entry — all fixed), and several
MINOR/NIT items, all fixed except the auth stub, which the review itself
flagged as a pre-deploy item rather than a now item. Nothing in this handoff
describes an unreviewed state.
