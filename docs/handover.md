# Handover: research legs → production pipeline

Written for whoever builds the actual scheduled pipeline (`app/orchestrator.py`,
`worker_entrypoint.py`, Alembic, Cron deployment — none of which exist yet, see
§6). This explains what already exists under `research/`, why it's shaped the
way it is, and what's still missing before it can run unattended.

This is a map, not the source of truth. Rationale for individual decisions
lives in [`docs/decisions.md`](decisions.md) (D1–D20) — this document
summarizes and points there rather than repeating it. Cost figures live in
[`docs/cost.md`](cost.md).

## 0. What got built, concretely, and how to use it (read this first)

**You have a handful of standalone Python scripts — not a pipeline.** Nothing
calls them on a schedule. Each does one narrow job: given a lab, go get one
specific kind of data about it, and write the answer to a JSON file. None of
them is exotic — each one is an ordinary Python function you can `import`
and call, or run as `python script.py` from a terminal.

Three kinds of script, one per "leg":

1. **Announcements** — one script,
   [`fetch_announcements.py`](../research/announcements/fetch_announcements.py),
   covers all seven labs. Reads `config/sources.yaml`, and for every lab
   listed there, goes and fetches whatever that lab has published recently
   (blog posts, news pages). Writes one combined file:
   `research/docs/announcements.json`.
2. **Papers** — one script *per lab* (`meta_harvest.py`, `deepmind_harvest.py`,
   `mistral_harvest.py`, `deepseek_harvest.py`, `openai_harvest.py`, plus
   Anthropic's original `byline.py`/`llm_byline.py` pair — six scripts,
   one lab, xAI, has no script because it's confirmed to publish no papers
   at all, see §2). Each finds that lab's recent research papers and uses an
   LLM to read off who wrote each one. Writes to
   `research/docs/<lab>_contributors.json`.
3. **GitHub** — two scripts,
   [`harvest_github.py`](../research/github/harvest_github.py) and
   [`aggregate_github.py`](../research/github/aggregate_github.py), cover
   all seven labs (nine GitHub orgs — Meta AI alone needs two). Given a
   GitHub org name, the first pulls every commit from the last 12 months;
   the second works out who's genuinely lab staff, an outside contributor,
   or an automated bot. Writes to `research/docs/github_people_<org>.json`.

That's the entire inventory: **9 harvester scripts total** (1 + 6 + 2),
plus two small shared helper modules the papers scripts import from
(`llm_byline.py`, `arxiv_resolve.py` — not run directly). All seven labs in
the deep-coverage register (Anthropic, OpenAI, DeepSeek, Google DeepMind,
Mistral, xAI, Meta AI) now have all three legs' data sitting in
`research/docs/` as JSON files, produced by running these scripts by hand,
once each, this session.

**What does not exist: anything that calls these scripts automatically.**
No scheduler, no cron job, no "run every night," no logic to retry a source
that's down for an entire run, no alert when something breaks. You run each
script yourself, right now, from a terminal, and it does its job once. A
production pipeline is specifically the layer that would call these on a
timer and handle it when one of them fails — that layer is **entirely
unbuilt**. Nothing under `research/` needs to be rewritten to add it; it
needs to be built on top.

**How a production pipeline actually uses this code:**

- **Every script's real logic is one plain function, not CLI glue.** The
  `if __name__ == "__main__":` block at the bottom of each file is a thin
  wrapper — argparse, then call the function, then write the file. Your
  orchestrator imports the function directly (see §7 for exact names and
  import paths) and calls it in-process; it never needs to shell out to
  `python script.py`.
- **Every function takes plain arguments and returns plain data** — a lab's
  config dict, a cutoff date, a model name in; a list of dicts back out. It
  does not talk to a database and does not know a production pipeline
  exists. Your orchestrator decides what happens to the returned data —
  write it to the same JSON files these scripts already write (simplest,
  works today), or hand it to a new Postgres-writing step instead (see §3
  for why that schema doesn't exist yet for papers/GitHub).
- **Every function is already safe to call on a schedule, unmodified.**
  Every fetch is cached to disk keyed by URL, so calling the same function
  every hour costs nothing extra for anything it's already seen. "Run this
  leg every N hours" needs zero changes to the functions themselves —
  that's the one big piece of "production-readiness" that's already done.
- **What your orchestrator has to add on top, that isn't here today:**
  looping over (lab, leg) pairs and catching one lab's failure so it
  doesn't take the whole run down (every script already does this
  *within* itself, per-item — see §5 — but nothing does it *across*
  sources yet); writing a record of each run (succeeded / failed / how far
  it got); and alerting someone when a source has been broken for more
  than one run in a row, distinct from a "we found something interesting"
  content alert. None of that exists in any form yet — it's the actual
  scope of "build the production pipeline," not a refinement of what's
  here.

The rest of this document is the detailed map — per-leg config file shapes,
output JSON shapes, known gotchas, and the exact functions to call. Skip to
§7 for the copy-pasteable import list if that's all you need right now.

## 1. What exists, in one paragraph

Three independent "legs" per lab — **announcements**, **papers**, **GitHub** —
each a config-driven register plus a set of standalone Python CLI scripts
under `research/{announcements,papers,github}/`. Every script is idempotent
(disk-cached per URL/API call, so a re-run costs nothing for anything already
fetched or extracted), retries transient failures with exponential backoff at
the fetch layer, and records LLM cost at the call site into a JSON file. None
of this is scheduled or orchestrated — each script is run by hand
(`python research/announcements/fetch_announcements.py`, etc.) and writes to
flat JSON files under `research/docs/`. There is no cron, no worker process,
no cross-run retry/alerting layer. A separate, unrelated `app/` module (built
by a different session, "bitcap-dc") loads the **announcements** leg's output
into Postgres via a CLI (`bitcap-db`) — papers and GitHub data have no
database presence at all yet, only JSON files.

## 2. Register coverage — what's built, per lab, per leg

Deep-coverage register is seven labs (D7, amended D14): Anthropic, OpenAI,
DeepSeek, Google DeepMind, Mistral, xAI, Meta AI.

| Lab | Announcements | Papers | GitHub |
|---|---|---|---|
| Anthropic | ✅ (pre-existing) | ✅ (pre-existing, v1 prompt) | ✅ (pre-existing) |
| OpenAI | ✅ (pre-existing) | ✅ (pre-existing) | ✅ (pre-existing) |
| DeepSeek | ✅ (pre-existing) | ✅ (pre-existing) | ✅ **D19, this session** |
| Google DeepMind | ✅ D11 | ✅ D13 | ✅ D12 |
| Mistral | ✅ D11 | ✅ D17 | ✅ D12 |
| Meta AI | ✅ D15 | ✅ D16 | ✅ D15 |
| xAI | ✅ **D20, this session** (`wayback_cdx`, 35 real in-window articles) | ❌ confirmed empty, not a gap (D16, reconfirmed D17) | ✅ D12 |

Everything in the "this session" / D11–D20 range is what an earlier
conversation in this thread built, on top of the pre-existing three-lab
(Anthropic/OpenAI/DeepSeek) manual scripts this repo already had before that
work started. **As of D20, register coverage across all three legs for all
seven deep-coverage labs is complete**, except xAI's papers leg, which is
correctly empty rather than unbuilt.

**xAI's missing papers leg is a real finding, not unfinished work.** Checked
twice, live: arXiv has no affiliation-search field at all (confirmed against
its own API docs), and every query tried for "xAI" or "Grok" either collides
with an unrelated acronym/word ("XAI" = eXplainable AI; "Grok" = an ML
phenomenon called grokking) or surfaces third-party papers *about* Grok, never
one published *by* xAI. Do not build a papers harvester for xAI without new
evidence that a corpus now exists.

## 3. The `app/` Postgres layer — what it covers and what it doesn't

`app/` (module: `bitcap-db`, entry point `app/cli.py`) is a different
session's work. It loads **announcements only** — `Article`, `Classification`,
`ArticleMechanism/Category/Practice`, `Connection`, `Holding`,
`RefLab/Mechanism/Category/Practice`, `PipelineRun` — from the committed
`research/docs/scored_announcements_v7.json` / `announcements.json` files into
Postgres via `cmd_load`/`cmd_rebuild`/`cmd_connect` (see `app/cli.py`,
`app/load_refs.py`, `app/load_raw.py`, `app/transform.py`, `app/connect.py`).

**There is no schema for papers or GitHub data.** `app/models.py` has zero
tables for contributor bylines, author employment tiers, or GitHub commit
data. If the production pipeline needs those in Postgres, that schema has to
be designed from scratch — nothing to extend, only the announcements tables
to use as a style reference (each table has a clean single purpose, FK-linked
to `RefLab`, no denormalized blobs except a deliberate `raw JSONB` escape
hatch pattern used throughout the research scripts themselves, see §5).

There is also no FastAPI app, no `main.py`, no Alembic migrations directory —
`bitcap-db` creates the schema directly via SQLAlchemy `create_all`/`drop_all`
(see `app/db.py`), which is fine for a CLI tool but not for a system anyone
will run migrations against in production.

## 4. Per-leg architecture

### 4a. Announcements

- **Config**: [`config/sources.yaml`](../config/sources.yaml) — one entry per
  lab: `id`, `label`, `method`, `index_url`, `url_contains`, `text_source`,
  `date_from`, plus method-specific fields. `window_months: 3` is global.
- **Discovery methods** (`research/announcements/fetch_announcements.py`,
  `METHODS` dict): `sitemap`, `rss`, `listing_pagination`, `wayback_cdx`. Each
  is a function `(lab: dict, cutoff: datetime) -> list[dict]`. Adding a lab
  whose site fits an existing method is a config-only change; a genuinely new
  site shape needs a new method function, registered in `METHODS`.
  - `wayback_cdx` (this session, xAI) is the interesting one: discovers
    articles via the Internet Archive's CDX API rather than a live fetch,
    because live `x.ai` is fully Cloudflare-blocked including `sitemap.xml`
    itself. Read its docstring before extending it to another blocked lab —
    the naive approach (fetch the sitemap *through* Wayback) doesn't work
    because archived sitemaps go stale; the CDX wildcard query on the article
    path pattern is what actually works, and every candidate's date comes
    from its own JSON-LD, never the archive crawl timestamp.
- **Entry point**: `collect() -> list[dict]` in `fetch_announcements.py`,
  called from `if __name__ == "__main__"` and written to
  `research/docs/announcements.json`. Clean to import and call directly from
  an orchestrator — no CLI-only logic in the way.
- **Scoring** (`research/announcements/score_announcements.py`): separate
  step, **deactivated by default** via `SCORING_ENABLED` env var (unset ⇒
  no-op unless `--force`). This was a deliberate, explicit scope boundary
  for this session ("don't run the scoring piece") — an orchestrator should
  keep this gate, not remove it, unless the product owner says otherwise.
  Supports both interactive (`classify_one`, threaded) and Anthropic Batches
  API (`run_batch`, 50% cost) modes; cost recorded per call via `call_cost()`.
- **Output**: `research/docs/announcements.json` (all labs, one file) —
  fields: `lab`, `url`, `date`, `title`, `text`, `text_source`,
  `feed_category`, and for `wayback_cdx` articles also `archive_snapshot`
  (the live `url` is always preserved as the primary citation; the archive
  snapshot is corroborating evidence for when the live site can't be reached
  directly by a human clicking the link either).

### 4b. Papers

No shared config file — each lab has its own `research/papers/<lab>_harvest.py`
script, because discovery methods differ too much per lab for a uniform
schema to be worth it (confirmed the hard way: DeepMind links arXiv directly,
Meta needs title-based arXiv resolution, Mistral needs to source candidate
titles from its own announcements corpus since it has no papers listing page
at all). What *is* shared:

- **`research/papers/llm_byline.py`** — the LLM extraction core.
  `extract_page(raw_html, model, lab_label=None) -> (parsed_byline, cost)`.
  `lab_label` set ⇒ uses the generalized v2 prompt/schema
  (`prompts/byline_extraction/v2.md`, `is_lab_staff` field, affiliation
  interpolated from `lab_label` rather than hardcoded); unset ⇒ Anthropic's
  original v1 path, untouched. `prepare_html()` handles the HTML→prompt-budget
  cut (`HTML_BUDGET = 60_000` chars) — this needed two real fixes this
  session (skip site chrome before `<article>`; locate a
  "Contributors"/"Author List" heading and window around it when the byline
  sits deep in the document rather than near the top) — **read its docstring
  before assuming any HTML page's byline will be captured by a flat
  head-of-document cut.** `ExtractionError(message, cost)` is raised (not a
  bare exception) when a call is billed but its JSON output fails to parse,
  specifically so the cost is never lost — every papers harvester's
  extraction-failure handler checks `getattr(exc, "cost", None)` and records
  it before giving up on that item.
- **`research/papers/arxiv_resolve.py`** — shared title→arXiv resolution
  (`resolve_title(title, desc) -> dict | None`), used by both
  `meta_harvest.py` and `mistral_harvest.py`. Exact-title match first, then a
  relaxed all-fields search accepted only when the best-scoring candidate
  (not just the top-ranked one — a real bug, see D16) clears an empirically
  calibrated overlap threshold against a description of the paper. If a third
  lab ever needs title-based resolution, use this module, don't copy it.
- Each `<lab>_harvest.py` script: `collect(months, model, limit=None) ->
  (list[Paper], list[dict])` — papers with a parsed byline, and unresolved
  candidates each with a `reason` (never silently dropped — this is a direct
  instance of the project's "no citation, no insight" rule: an
  unresolvable/unextractable paper is recorded as unresolved, not guessed
  at). `main()` writes `research/docs/<lab>_contributors.json` and prints a
  summary. Idempotent via `<lab>_extraction_cache.json` (LLM results, keyed
  by source URL) separate from the raw-page disk cache
  (`research/docs/<lab>_cache/`).
- **Output shape** (`<lab>_contributors.json`): `{generated, lab, papers:
  [...], people: [...]}`. `people` is a per-person aggregate (`appearances`,
  `affiliations`, `is_lab_staff`, `papers: [{title, url, date}]`) built by
  each script's own `aggregate()`.

### 4c. GitHub

- **Config**: [`config/github_sources.yaml`](../config/github_sources.yaml) —
  keyed by GitHub org login (not lab id, since a lab can have more than one
  org — Meta AI needs two, `facebookresearch` + `meta-llama`). Each entry:
  `lab`, `domain` (string, list, or `null`), `domain_shared` (bool),
  `work_suffix` (regex or `null`), `notes`.
- **`research/github/harvest_github.py`**: `collect(org, months=12) -> dict`
  — REST to enumerate repos, GraphQL to walk each repo's default-branch
  commit history. Per-repo disk cache under `research/docs/github_cache/`,
  so re-harvesting an org only fetches repos not already cached — **to force
  a genuine refresh, delete that org's cached files first** (`rm
  research/docs/github_cache/<org>__*.json`), there is no "max age" on the
  cache otherwise. Writes `research/docs/github_commits_<org>.json`.
- **`research/github/aggregate_github.py`**: `aggregate(org, org_domain,
  work_suffix, domain_shared) -> dict` — bot filtering, alias merging,
  mirror-repo exclusion, employment-tier assignment
  (`confirmed`/`confirmed_org_wide`/`handle`/`vendor`/`unknown`). Reads its
  per-org config from `config/github_sources.yaml` via `load_labs()`, not a
  hardcoded dict. Writes `research/docs/github_people_<org>.json`.
- **Bot detection is a hand-maintained list (`BOT_LOGINS`), not a generalized
  classifier**, plus a small regex (`BOT_PATTERNS`) for `[bot]`-suffixed and
  `-bot`-suffixed logins. This session found and added 6 real bots this way
  (`copybara-github`, `copilot`, `speakeasybot`, `maiengineering`,
  `goreleaserbot`, plus the pre-existing set) — every one was found by
  **hand-checking commit-level `login`/`name`/`email` fields directly**, not
  by the regex or by eyeballing the top-30 display. `maiengineering`
  specifically had a login with zero bot-shaped signal at all (only caught
  via its blank profile + role-based email + a commit `name` field literally
  reading "Buildkite CI"). **If integrating this into an orchestrator: expect
  to keep finding these by hand per new org, the list will never be
  complete, and that's an acceptable, documented tradeoff, not a bug to
  "fix" with a cleverer regex** (see `docs/decisions.md` D12/D15/D18 for the
  full reasoning each time this came up).
- **Mirror-repo detection** (`MIRROR_MARKER` regex on repo description,
  matching `\bmirror of\b`) is similarly a known-incomplete heuristic — a
  real repo can dominate an org's commit count without being a mirror at
  all (see D19's `deepseek-harness` finding: 97.5% of DeepSeek's corpus in
  one repo, checked by hand and confirmed genuine, not excluded). Don't
  assume `mirrors_excluded: []` in the output means "verified clean" —it
  means "nothing matched the description heuristic," which is a much weaker
  claim.
- **Output shape** (`github_people_<org>.json`): `{people: [...], aliases:
  [...], mirrors_excluded: [...], totals: {repos, commits, bot_commits,
  unattributed_commits, people, employment: {...}}}`.

## 5. Shared engineering conventions (apply these to whatever the orchestrator becomes)

- **Idempotent disk caching, keyed per URL/query**: every `fetch`-shaped
  function in every leg sanitizes the URL into a filename and checks a cache
  directory before making a request. This is what makes every script
  genuinely safe to re-run — cost and time are only spent on what's actually
  new. An orchestrator wrapping these scripts should **not** clear these
  caches between scheduled runs; that's what makes incremental runs cheap.
- **Retry/backoff exists, but only at the per-request level, not across
  runs.** Every `fetch()`/`fetch_wayback()`/`get()` function retries 3–4
  times with exponential backoff on a single HTTP failure. There is **no
  cross-run retry cadence** — if an entire source is down for a whole
  scheduled run (this happened live this session: the Internet Archive
  returned 503 for an extended period), nothing currently notices and
  retries on the *next* run specifically because of that failure; it just
  tries again next time on the normal schedule. `docs/planning.md` §4b
  discusses this gap explicitly and it's still open — this is one of the
  concrete things the orchestrator needs to add.
- **Cost is recorded at the call site, into a JSON file, every call** — never
  reconstructed after the fact. Every LLM-calling script writes a cost record
  (model, tokens, `$`, timestamp) to its own `<lab>_llm_cost.json` /
  `announcement_cost.json` immediately after each call succeeds. A pipeline
  wiring these together should preserve this — don't batch cost writes, don't
  compute cost only from final aggregates.
- **"No citation, no insight," operationalized as "record failures, don't
  drop them."** Every harvester's failure path (fetch failure, extraction
  failure, resolution failure) appends to an `unresolved`/`failed` list with
  a `reason` string, rather than silently omitting the item. Several output
  files have a sibling `<lab>_unresolved.json`. Preserve this shape if papers
  data ever gets a Postgres table — an "unresolved papers" table or column is
  not optional scope, it's load-bearing for the project's core evaluation
  requirement.
- **`raw JSONB`-shaped escape hatches, not per-lab bespoke columns.** Where
  the research scripts store lab-specific idiosyncrasy (DeepSeek's
  alphabetical-author-order flag and departure asterisks, xAI's
  `archive_snapshot`), they keep it in the main record rather than growing
  bespoke schema per lab. If a Postgres schema gets built for papers/GitHub,
  follow this same instinct rather than one column per lab's one-off field.

## 6. What still needs to be built (not done by this session)

In rough dependency order:

1. **A Postgres schema for papers and GitHub data**, if the product needs
   them queryable rather than JSON-file-based. Nothing exists yet — see §3.
2. **An orchestrator** that loops over (leg, lab) pairs, calls each script's
   `collect()`/`aggregate()` function (all already have clean, non-CLI-only
   entry points — see §4), and isolates one dead source from aborting the
   whole run (every script above already does this *within* itself for
   per-item failures; nothing does it *across* sources yet).
3. **Cross-run retry/rate-limit state** — persisted somewhere so a source
   that's down for an entire run gets flagged and retried with real cadence,
   not just silently attempted again on the next schedule (see §5).
4. **`worker_entrypoint.py`** as the actual Cron/scheduled-worker target —
   doesn't exist. The natural shape: sync labs from YAML config → run the
   orchestrator → write a `PipelineRun`-shaped record (a table with that name
   already exists in `app/models.py` for the announcements leg specifically;
   generalizing it or adding leg-specific run tables is a design choice for
   whoever builds this) → exit non-zero on any failure so the platform's own
   cron-failure alerting has something to key off, in addition to whatever
   in-app alerting gets built.
5. **System-failure alerting, distinct from content alerts** (CLAUDE.md
   non-negotiable #5) — does not exist in any form yet. No alert has ever
   been sent by any code in this repo; it's a pure design item.
6. **Alembic migrations** — `bitcap-db` uses `create_all`/`drop_all`
   directly, fine for a research CLI, not for anything that needs a real
   migration history.

## 7. Quick-reference: entry points to call from an orchestrator

```python
# Announcements
from fetch_announcements import collect as collect_announcements
articles = collect_announcements()  # all labs, per config/sources.yaml

# Papers (per lab, e.g. Meta)
from meta_harvest import collect as collect_meta_papers
papers, unresolved = collect_meta_papers(months=3, model="claude-sonnet-5")

# GitHub (per org)
from harvest_github import collect as harvest_org
from aggregate_github import aggregate, LABS
commits = harvest_org("google-deepmind", months=12)
domain, suffix, domain_shared = LABS["google-deepmind"]
people = aggregate("google-deepmind", domain, suffix, domain_shared)
```

Every module above lives under `research/{announcements,papers,github}/` and
needs that directory on `sys.path` to import directly (see any test file's
`sys.path.insert(0, str(ROOT / "research" / "papers"))` pattern for the
convention already used throughout `tests/`).

## 8. Where to look for more

- **`docs/decisions.md`** — every real bug, every register-composition call,
  in chronological order (D1–D20). Read the ones referenced above before
  touching the code they're about; several encode a genuinely non-obvious
  fix (e.g. D16/D17's HTML-budget windowing, D20's snapshot-date-vs-publish-
  date trap) that a naive rewrite would silently reintroduce.
- **`docs/cost.md`** — exact $ spent per workflow, including discarded/failed
  attempts, not just successful totals.
- **`research/papers/README.md`** and **`research/github/README.md`** —
  deeper per-leg narrative, written as findings accumulated (what worked,
  what didn't, per lab). The announcements leg has no equivalent README;
  its narrative lives only in `docs/decisions.md`.
- **`docs/planning.md`** §4b (retry cadence), §7 (open design questions —
  BIT's position coverage, register breadth, X/Twitter, digest cadence, none
  of which are code-shaped) and §13 (parked backlog — do not action without
  an explicit instruction, per that section's own header).
