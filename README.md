# bitcap — Frontier Lab Intelligence

Tracks frontier AI labs, scores what they publish against a deterministic rule,
and joins every signal to BIT's portfolio holdings and to AI-engineering
practice — with a verbatim quote behind every tag.

## Live

**<https://bitcap-web.onrender.com/>** — the deployed system, running the nightly
pipeline against managed Postgres.

The whole site is behind a single login. That is a deliberate trade-out and not
an accident of setup: `/pipeline` runs a real firing that spends real money on a
public URL, and gating only the button while leaving the dashboard open would
have put the credential check on the wrong side of the thing worth protecting
(`docs/decisions.md` D39). **Credentials are in the submission email**, not here.

Both services run on Render's `starter` plan, which does not spin down on idle.
That is a requirement rather than a preference: a firing takes 9–30 minutes and
runs in a thread on the API instance, so a plan that slept would kill a run
mid-flight and leave a `running` row that blocks every later firing through the
concurrency guard. It is the one failure here that code cannot defend against,
and it is recorded on the service in [`render.yaml`](render.yaml) so the
constraint travels with the config rather than living in someone's memory.

Where to look first, in the order the brief asks its questions:

| Page | Answers |
|---|---|
| `/digest` | Did it surface something worth knowing, and what did it suppress to get there |
| `/` | The whole scored corpus, filterable by band, lab and holding |
| `/register` | Who is tracked — and the four possible researcher moves in it |
| `/ops` | Can any of the above be trusted: run history, source health, spend, classifier drift |
| `/pipeline` | Run it yourself |

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
| `enabled` | The kill switch, one line per leg. `false` means not fetched, not landed, and not classified — including rows the leg ingested on earlier firings, which stay in bronze and would otherwise keep costing money. It outranks `cadence` and an explicit `--legs`. A leg absent from the map is on. |
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

On this deployment those are `https://bitcap-web.onrender.com` and
`https://bitcap-api.onrender.com` respectively. Because `NEXT_PUBLIC_API_URL` is
baked in at build time, a frontend change needs a rebuild, not a restart — which
is also why a new page appears as a 404 on the live site until the static export
is redeployed.

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

### The digest

`/digest` is the periodic report: one dated cut of the corpus per audience,
covering a 48-hour publication window. Two things make it different from the
dashboard.

**It states what it suppressed.** Every edition carries `considered`,
`surfaced` and `suppressed`, and the page shows the suppressed count as
prominently as the items — the cut is the product, and a digest that cannot say
how much it discarded is just a shorter list.

**Its unit is the event, not the connection.** One sentence can fire against
fourteen holdings at once; the digest renders that as one row naming the top
four and counting the rest, rather than fourteen rows for one fact.

Editions are published as phase 6 of every firing and stored, not recomputed on
read: a digest is a record of what the product said on a date, and rebuilding it
against a later re-scored corpus would restate last week's edition in this
week's terms. `digests` therefore survives `rebuild`, like the other ops tables.
Past editions are in the selector at the top of the page.

Thresholds, the window, and the fan-out cap are in `config/digest.yaml`. The
48-hour default is measured rather than assumed — see `docs/decisions.md` D48
for the replay over the whole corpus that chose it.

### The register

`/register` browses the people the system tracks — 4,941 across seven labs, from
paper bylines and GitHub commit history — and leads with the thing that falls
out of it: **possible researcher moves**.

The register keeps one record per (lab, person) and refuses to merge a name
across labs, because same name is not same person. That refusal is what makes a
move visible: a researcher at two labs is one name with two records rather than
a row that got tidied away. Finding them is a group-by, not a model.

Fifty cross-lab names exist; four are shown. The other 46 are GitHub logins with
no lab-owned email domain on either side — drive-by open-source contributors,
not moves — and the page reports that reduction rather than just the survivors.
Every candidate carries both sides' dates, evidence tiers and primary source
URLs, and is labelled a candidate, not a conclusion.

No figure on the page is a bare head count: only 1,222 of the 4,941 have any
employment-tier evidence, so every total carries its tier breakdown. See
`docs/decisions.md` D49.

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
- `app/` — the database package (`bitcap-db`), the scheduled pipeline
  (`app/pipeline/`), and the digest (`app/digest.py`)
- `api/` — FastAPI service over the same database: the frontend's read model,
  the single-account gate (`api/auth.py`), and the manual trigger (`api/pipeline.py`)
- `frontend/` — Next.js app (investment/AI-team dashboards, insight detail view)
- `docs/` — planning, running decision log, cost ledger
- `tests/` — `uv run pytest`
