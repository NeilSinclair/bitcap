# bitcap — Frontier Lab Intelligence

Tracks frontier AI labs, scores what they publish against a deterministic rule,
and joins every signal to BIT's portfolio holdings and to AI-engineering
practice — with a verbatim quote behind every tag.

## Clone to running

Prerequisites: [uv](https://docs.astral.sh/uv/), and Docker for the Postgres
path (optional — without it the database falls back to a local sqlite file).
[Node.js](https://nodejs.org/) 18.18+ is needed too, but only for the frontend
(below).

```bash
git clone <repo> && cd bitcap
uv sync                        # env + deps from the committed lockfile
docker compose up -d           # local Postgres (skip for sqlite fallback)
echo "DATABASE_URL=postgresql+psycopg://bitcap:bitcap@localhost:5432/bitcap" >> .env
uv run bitcap-db rebuild       # schema + full load from committed data
uv run bitcap-db status        # last runs, counts, watermarks, cost
uv run pytest                  # full test suite
```

`DATABASE_URL` goes in `.env` rather than a plain `export` so every later
terminal — the API, the frontend, a fresh `bitcap-db` invocation — picks it up
the same way, instead of only the shell that ran this command.

`rebuild` needs **no API key**: it loads the committed artifacts — the scored
announcement corpus (June–Aug 2026), 26 holdings with their mechanism and
lab-exposure edges, and the full cost log — and derives the clean tables and
joins. It is always safe to re-run.

It is safe because everything it drops is derived from files in the repo. The
**operational** tables are the exception and are never dropped: `pipeline_runs`,
`run_sources`, `source_state`, `alerts` and `gold_snapshots` record what actually
happened on a run, and nothing in the repo can reproduce them. Schema changes go
through Alembic:

```bash
uv run alembic upgrade head    # bring a database to the current schema
uv run alembic current         # what revision is it on
```

`bitcap-db rebuild`/`load` call this for you. A database created before
migrations existed is stamped automatically on first use, so no manual step is
needed on an existing clone.

## What the database holds

| Layer | Tables | Source |
|---|---|---|
| Raw | `raw_articles`, `raw_classifications`, `raw_costs` | `research/docs/*.json`, verbatim, upsert-only |
| Reference | `ref_*`, `holdings`, `holding_*` | `config/*.yaml` (YAML stays the source of truth) |
| Clean | `articles`, `classifications` (+ both scores), `article_{mechanisms,categories,practices}` | derived; scores recomputed from `config/scoring.yaml` |
| Joins | `connections` (investment side), `article_practices` × `ai_score` (AI-team side) | deterministic, see `app/connect.py` |
| Ops | `pipeline_runs`, `gold_snapshots` | run state, watermarks, drift snapshots |

Every connection row carries the route that fired (`mechanism`, `category`,
`lab_exposure`, or `named`), the composed direction and strength, and **both
sides' explanations** — the article's reason and verbatim quote, and the
holding link's `why` from `config/companies.yaml`. One row answers "why was
this flagged":

```sql
SELECT a.title, h.name, c.direction, c.strength,
       c.article_reason, c.article_quote, c.holding_why
FROM connections c
JOIN articles a ON a.id = c.article_id
JOIN holdings h ON h.isin = c.isin
WHERE c.route = 'mechanism'
ORDER BY c.strength DESC LIMIT 5;
```

## Refreshing the data (needs API keys)

The research pipeline writes the artifacts the database loads. With
`ANTHROPIC_API_KEY` (and optionally `OPENAI_API_KEY`) in `.env`:

```bash
uv run python research/announcements/fetch_announcements.py   # free; HTML cached
uv run python research/announcements/score_announcements.py   # ~$5.50/full corpus, cached + idempotent
uv run bitcap-db load                                         # pick up the new artifacts
```

Costs are recorded per call as runs proceed (`docs/cost.md` has the ledger).

## The scheduled pipeline

`bitcap-worker` is one firing, start to finish: ingest every due source →
classify what is new, under a cost ceiling → derive the clean layer and the
joins → check the classifier against the gold set → raise alerts.

```bash
uv run bitcap-worker                          # a real firing
uv run bitcap-worker --dry-run                # same shape, no LLM spend, no alert delivery
uv run bitcap-worker --legs announcements     # override cadence, run one leg
```

Everything it does is configured in [`config/pipeline.yaml`](config/pipeline.yaml):

| Setting | Why it exists |
|---|---|
| `budget.per_run_usd` / `per_month_usd` | A cron making LLM calls with no ceiling is the one thing that can hurt on a fixed budget. Exceeding it stops classification; ingested data still lands. |
| `cadence` | Per leg. A rolling 12-month GitHub window barely moves in a day; re-harvesting nightly is the most expensive thing here in wall-clock. Firing 1 runs everything. |
| `alerts.source_down_runs` | One firing down and back up is noise. N in a row is an incident. |
| `alerts.max_deliveries_per_run` | Everything raised is recorded; only delivery is capped, so a first run over an existing corpus does not fire 135 notifications. |

**A dead source is not a dead run.** The orchestrator isolates sources so one
broken lab does not cost the other six, and the run status and exit code say the
same thing: a firing that completes exits `0` even with a source down, and
`source_down` escalates that after N consecutive runs. Non-zero means the firing
itself broke, so the platform's own cron alerting stays a signal rather than a
nightly red light.

## Deploying

One image, two entrypoints — the cron job runs `bitcap-worker`, the web service
runs uvicorn. The dashboard is neither: it exports to static files and is served
without a server of its own. [`render.yaml`](render.yaml) declares the database,
the schedule and both web services; Railway needs the same pieces configured in
its UI.

```bash
docker build -t bitcap .
docker run --rm -e DATABASE_URL=... bitcap bitcap-worker --dry-run
```

**A persistent volume at `/app/research/docs` is optional.** Every cache worth
keeping is in Postgres, so a cold container repeats work but never pays twice
for the same answer:

| Cache | Where | A cold start... |
|---|---|---|
| Classifier output | `raw_classifications`, keyed on prompt version | never re-classifies |
| GitHub commit history | `raw_github_repos`, keyed on `pushed_at` | walks only repos that were pushed to |
| Fetched pages, extracted bylines | `research/docs/` on disk | re-fetches and re-extracts |

The disk row barely fires. Each leg narrows its window to what has happened
since its last successful run (full window the first time, against an empty
database), and the announcements leg additionally skips any article already
stored and classified — it does not request the page at all, which beats caching
it. What is left on disk is a local convenience for re-running a harvester over
a window on purpose.

Render cron jobs cannot mount a disk, and the blueprint does not ask for one.

Secrets, none of which are in the repo:

| Variable | Needed for |
|---|---|
| `DATABASE_URL` | everything |
| `ANTHROPIC_API_KEY` | classification and drift. Needed on **both** `bitcap-worker` and `bitcap-api` — the pipeline tab runs firings from the API service, and without it the gold-set check measures nothing (D45) |
| `GITHUB_TOKEN` | the GitHub leg |
| `ALERT_WEBHOOK_URL` | only when `alerts.channel` is `webhook` |
| `AUTH_EMAIL` / `AUTH_PASSWORD_HASH` / `AUTH_SECRET` | the sign-in — see below |

The two non-secret URLs are a pair, and each is only knowable once the other
service exists. Deploy the blueprint, then set them from the URLs Render
assigns and let both services redeploy:

| Variable | On | Value |
|---|---|---|
| `FRONTEND_ORIGIN` | `bitcap-api` | the static site's URL — the API's CORS allow-list. Must not be blank: an empty value overrides the `http://localhost:3000` default and blocks every origin. |
| `NEXT_PUBLIC_API_URL` | `bitcap-web` | the API's URL. Inlined into the bundle at build time, so a change rebuilds rather than restarts. |

## Running the web app

A FastAPI service (`api/`) queries the database `bitcap-db` built above; a
Next.js frontend (`frontend/`) calls it. Both need the Postgres container from
the quickstart running, and `DATABASE_URL` in `.env` (the clone-to-running steps
above already set this up).

```bash
uv run uvicorn api.main:app --reload --port 8000   # http://localhost:8000
cd frontend && npm install && npm run dev           # http://localhost:3000
```

### Sign-in

The site is behind a single account, supplied by the environment — there is no
users table and no signup. Generate the credentials:

```bash
uv run python -m api.auth      # prompts for a password, prints the two values
```

Put `AUTH_EMAIL`, `AUTH_PASSWORD_HASH` and `AUTH_SECRET` in `.env` locally, and
in the Render dashboard for `bitcap-api`. The password itself is never stored:
`AUTH_PASSWORD_HASH` is a salted scrypt hash, and rotating `AUTH_SECRET` logs
every session out.

Every route requires a bearer token except `/api/health`, which stays open
because it is the platform's deploy probe. The frontend's login screen is
ergonomics — the site is a static export, so its HTML is public either way; the
guarantee is that the API returns 401 without a token and the page has nothing
to render.

### The pipeline tab

`/pipeline` runs the pipeline on demand: tick the legs, press run, watch it
finish. The run happens in a background thread on the API and takes 9–30
minutes, so the page polls rather than waiting on a request — closing the tab
does not stop the run, and reopening it picks the run back up. One run at a
time, enforced against the database so the nightly cron counts too. Manual runs
are recorded as `kind: manual` and deliberately do not advance the cadence
counter that decides when the GitHub leg is due.

## Layout

- `config/` — what's tracked: labs, mechanisms, categories, practices,
  holdings, scoring rules. Adding a lab or holding is a config change;
  `config/validate.py` gates every load.
- `prompts/` — versioned LLM prompts (current classifier: `announcement_scoring/v7.md`)
- `research/` — ingestion, classification, evaluation (gold set), analyses
- `app/` — the database package (`bitcap-db`)
- `api/` — FastAPI service over the same database: the frontend's read model,
  the single-account gate (`api/auth.py`), and the manual trigger (`api/pipeline.py`)
- `frontend/` — Next.js app (investment/AI-team dashboards, insight detail view)
- `docs/` — planning, running decision log, cost ledger
- `tests/` — `uv run pytest`
