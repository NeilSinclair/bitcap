# app/ — database and ETL

This package owns the Postgres (or sqlite-fallback) schema and the load
pipeline that fills it from committed files. It is intentionally boring: no
LLM calls happen here, nothing is inferred — every table is a deterministic
function of `research/docs/*.json` and `config/*.yaml`. That's what makes
`rebuild` always safe to re-run.

See the root [README](../README.md) for the quickstart and table overview.
This doc is the "how it fits together" reference for working on the pipeline
itself.

## Three layers, one direction

```
config/*.yaml  ─┐
                 ├─▶ load_refs ─▶ ref_* / holdings / holding_*  ─┐
research/docs/*  ─▶ load_raw  ─▶ raw_*                            ├─▶ transform ─▶ articles / classifications / article_*  ─▶ connect ─▶ connections
```

* **raw_*** (`load_raw.py`) — verbatim JSON payloads (`raw_articles`,
  `raw_llm_responses`, `raw_costs`), **upsert-only** on natural keys
  (URL, or URL+prompt_version). Nothing here is ever deleted, so the DB
  accumulates history the file register doesn't keep — `research/docs/`'s
  fetch step drops articles older than ~3 months, but a row that already
  landed in `raw_articles` stays.
* **ref_* / holding*** (`load_refs.py`) — a mirror of `config/*.yaml`
  (labs, mechanisms, categories, practices, holdings, and the
  mechanism/lab-exposure edges). YAML stays the source of truth; this
  table exists purely so the join can be written in SQL. **Wipe-and-reload**
  every run, inside one transaction, and refuses to run if
  `config/validate.py` reports errors — a mirror of an invalid config would
  launder the invalidity into the clean tables.
* **articles / classifications / article_* / connections** (`transform.py`,
  `connect.py`) — fully derived from the two layers above. Deleted and
  rebuilt per article/run rather than diffed, because the derivation is
  deterministic: a re-run converges on the same rows, so there's no
  reconciliation logic to get wrong.

Data flows one way only: raw and config in, clean and joins out. Nothing
downstream is ever hand-edited or fed back upstream.

## Pipeline stages, in order

`cli.cmd_load` runs all four, in this order, inside **one transaction**
(`app.runs.tracked`):

1. **`load_refs`** — wipe and reload `ref_*`/`holdings`/`holding_*` from
   YAML. This also deletes the entire clean layer (`connections` down to
   `articles`), because everything downstream references the refs it just
   replaced — see the ordered delete list in `load_refs.py`.
2. **`load_raw`** (`load_articles`, `load_classifications`, `load_costs`) —
   upsert the research pipeline's JSON artifacts.
3. **`transform`** — raw articles + raw classifications → clean `articles`
   and `classifications`, recomputing both scores (`app.scoring`) from
   `config/scoring.yaml` rather than trusting whatever score the file
   register carried. `score` doubles as a reconciliation check;
   `ai_score` is new — the file register never had an AI-team axis.
4. **`connect`** — rebuild `connections` from the clean tables across four
   routes (mechanism, category, lab_exposure, named); see the module
   docstring in `connect.py` for the routing rules and sign/strength math.

Connect is chained onto load rather than left as a separate step, because
step 1 deletes `connections` — a load that stopped short of the join would
leave the table empty and look like a finding rather than an interrupted run.

Order matters within a stage too: parents (labs, categories, holdings) are
flushed before children (edges, tags) because the models declare foreign
keys without ORM `relationship()`s, so SQLAlchemy's unit of work can't infer
insert order on its own — Postgres enforces the FK, sqlite (used in tests)
silently doesn't, which is exactly the kind of dialect gap that hides a real
ordering bug (see `app/db.py`'s sqlite `PRAGMA foreign_keys=ON`).

## Idempotency and failure handling

* **Transactional wipe-and-rebuild.** `tracked()` (`runs.py`) wraps the
  whole `cmd_load` body in one transaction. Stages inside it `flush()`, not
  `commit()`; only `tracked()` commits, on success, or rolls back on any
  exception. That matters because stage 1 starts by deleting the derived
  layer — without a single spanning transaction, a failure in `transform`
  or `connect` would leave the database *empty* rather than *stale*, and
  empty is strictly worse for a system other people are reading from.
* **Run tracking.** Every invocation writes a `pipeline_runs` row —
  committed immediately on entry (so a crash leaves a visible `running`
  corpse, not silence), then updated to `succeeded`/`failed` with
  whatever `stats` had accumulated stage-by-stage. `status='failed' AND
  alerted_at IS NULL` is the standing query a system-failure alerter polls
  (the notifier itself is a deferred piece — see `docs/decisions.md`).
* **`rebuild` vs `load`.** `rebuild` drops and recreates the schema first;
  `load` assumes it exists. Both end up calling the same `cmd_load`, so
  the load logic itself doesn't care which — `kind` is just what gets
  recorded on the run row.
* **Raw layer is upsert, never deleted** — re-running `load` is always
  safe and never loses history, even though the clean layer is rebuilt
  from scratch every time.

## Module map

| File | Responsibility |
|---|---|
| `db.py` | Engine/session plumbing; `DATABASE_URL` resolution (sqlite fallback); `create_all`/`drop_all`. No migrations — the schema is fully derived, so `rebuild` replaces them. |
| `models.py` | SQLAlchemy ORM schema, grouped by layer (Operations / Raw / Reference / Clean). Vocabulary values (sign, magnitude, action, …) are plain TEXT — the vocabulary itself lives in `config/` and is enforced by `config/validate.py` at load time, not by DB enums. |
| `load_refs.py` | `config/*.yaml` → `ref_*`/`holdings`/`holding_*`. Wipe-and-reload, gated on config validation. |
| `load_raw.py` | `research/docs/*.json` → `raw_*`. Upsert-only, keyed on natural keys (URL / URL+prompt_version / URL+at). |
| `transform.py` | `raw_*` → `articles`/`classifications`/`article_*`. Recomputes both scores; deletes and rebuilds tag rows per article. |
| `scoring.py` | Pure functions: tags in, `(score, band)` out, driven entirely by `config/scoring.yaml`. The LLM never emits a score — this is the only place numbers come from. Shared with `research/announcements/score_announcements.py`. |
| `connect.py` | Clean tables → `connections`. Four routes (mechanism, category, lab_exposure, named), each with its own sign/strength math; see its module docstring for the full rules. |
| `runs.py` | `tracked()` context manager (transaction + run-row bookkeeping) and `watermarks()` (per-lab max published date, for both `status` and the run record). |
| `cli.py` | `bitcap-db` entry point: `rebuild` / `load` / `connect` / `status`. |

## Running it

```bash
uv run bitcap-db rebuild   # drop + recreate schema, full load — safe anytime
uv run bitcap-db load      # refs + raw + transform + connect, incrementally
uv run bitcap-db connect   # rebuild connections only (e.g. after editing config/scoring.yaml)
uv run bitcap-db status    # last 5 runs, watermarks, table counts
```

All four need `DATABASE_URL` (falls back to a local `bitcap.db` sqlite file
if unset) and read `.env` if present — no API keys required, since this
package only ever reads files already committed to the repo. Fetching new
articles or classifying them is the research pipeline's job
(`research/announcements/`), not this one; `app/` only loads what already
exists on disk.
