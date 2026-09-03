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

`rebuild` needs **no API key**: it loads the committed artifacts — 191 scored
articles (June–Aug 2026), 26 holdings with their mechanism and lab-exposure
edges, and the full cost log — and derives the clean tables and joins. It is
always safe to re-run; the database is entirely derived from files in the repo.

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

## Running the web app

A read-only FastAPI service (`api/`) queries the database `bitcap-db` built
above; a Next.js frontend (`frontend/`) calls it. Both need the Postgres
container from the quickstart running, and `DATABASE_URL` in `.env` (the
clone-to-running steps above already set this up).

```bash
uv run uvicorn api.main:app --reload --port 8000   # http://localhost:8000
cd frontend && npm install && npm run dev           # http://localhost:3000
```

Sign-in on the frontend is a UI placeholder, not real auth — this is a
prototype of the login/dashboard/detail flow wired to real data, not yet a
deployed or access-controlled app.

## Layout

- `config/` — what's tracked: labs, mechanisms, categories, practices,
  holdings, scoring rules. Adding a lab or holding is a config change;
  `config/validate.py` gates every load.
- `prompts/` — versioned LLM prompts (current classifier: `announcement_scoring/v7.md`)
- `research/` — ingestion, classification, evaluation (gold set), analyses
- `app/` — the database package (`bitcap-db`)
- `api/` — read-only FastAPI service over the same database, for the frontend
- `frontend/` — Next.js app (investment/AI-team dashboards, insight detail view)
- `docs/` — planning, running decision log, cost ledger
- `tests/` — `uv run pytest`
