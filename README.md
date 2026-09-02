# bitcap — Frontier Lab Intelligence

Tracks frontier AI labs, scores what they publish against a deterministic rule,
and joins every signal to BIT's portfolio holdings and to AI-engineering
practice — with a verbatim quote behind every tag.

## Clone to running

Prerequisites: [uv](https://docs.astral.sh/uv/), and Docker for the Postgres
path (optional — without it the database falls back to a local sqlite file).

```bash
git clone <repo> && cd bitcap
uv sync                        # env + deps from the committed lockfile
docker compose up -d           # local Postgres (skip for sqlite fallback)
export DATABASE_URL=postgresql+psycopg://bitcap:bitcap@localhost:5432/bitcap
uv run bitcap-db rebuild       # schema + full load from committed data
uv run bitcap-db status        # last runs, counts, watermarks, cost
uv run pytest                  # full test suite
```

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

## Layout

- `config/` — what's tracked: labs, mechanisms, categories, practices,
  holdings, scoring rules. Adding a lab or holding is a config change;
  `config/validate.py` gates every load.
- `prompts/` — versioned LLM prompts (current classifier: `announcement_scoring/v7.md`)
- `research/` — ingestion, classification, evaluation (gold set), analyses
- `app/` — the database package (`bitcap-db`)
- `docs/` — planning, running decision log, cost ledger
- `tests/` — `uv run pytest`
