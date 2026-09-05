# Handover: OpenAI full text, and the eval blind spot behind it

Written 2026-09-05, on branch `fix/openai-fulltext` (off `deployment-dev`).
Rationale lives in [`decisions.md`](decisions.md) §D55; this is the operational
picture — what is done, what is not, and what to be careful of.
The review that followed is §D55a.

---

## 1. The one-paragraph version

OpenAI's articles were 201-character RSS blurbs, not articles, because text
recovery ran *after* the fetch that overwrites the file. It now runs inside the
fetch, in both the script path and the pipeline path. 131 of 149 OpenAI articles
are recoverable from the Internet Archive. **The code is merged-ready; the
corpus has not been re-fetched or re-scored yet.** That is the next step and it
costs money.

## 2. State when this was written

| | |
|---|---|
| branch | `fix/openai-fulltext`, 5 commits, pushed |
| tests | 1,225 pass, 1 skipped, config clean |
| corpus | re-fetched 2026-09-05: 259 articles, 22 still on `rss_summary` (D56) |
| register | `scored_announcements_v8.json`, scored against the *degraded* text |
| `PROMPT_VERSION` | `v9` (D56) |

## 3. What to do next, in order

**a. Re-fetch the corpus.** `uv run python research/announcements/fetch_announcements.py`.
Expect ~131 archive fetches at 2s each on a cold cache — roughly 5 minutes — and
OpenAI's mean text to go from ~201 to several thousand characters. Sanity-check
before scoring anything:

```
uv run python -c "
import json,pathlib,collections
c=json.loads(pathlib.Path('research/docs/announcements.json').read_text())
oa=[a for a in c if a['lab']=='openai']
print(collections.Counter(a['text_source'] for a in oa))
print('mean chars', sum(len(a['text']) for a in oa)//len(oa))"
```

**b. Re-score at v8.** ~251 articles × ~$0.025 ≈ **$6.25**, against a $15
per-run ceiling. The `per_run_usd` note in `config/pipeline.yaml` was raised to
15.00 specifically so a full re-classification fits in one firing — do not let
it abort halfway, which leaves the corpus split across two prompt versions.

**c. Check the result against the gold set, not against a vibe.**
`research/announcements/test/articles/15.json` is Jalapeño with human labels of
`custom_silicon_substitution` positive/high/high. If the re-score does not
produce something close to that, something is still wrong with the text.

## 4. Traps

**The gold set and production read different text.** This is the thing that hid
the bug for two days and it is only half fixed. `test/articles/*.json` hold
their own copy of each document — 11 of the 20 were 2×–98× richer than what the
pipeline actually scored. So gold agreement stayed high while the product
degraded, and **the drift check could not see any of it**. After step (a) the
two should converge; a check that flags divergence is still worth writing.

**Do not re-add a length gate on tag confidence.** It was measured and rejected:
articles under 500 characters produced 15 mechanism tags, zero high-confidence,
zero high bands. The v8 prompt already handles it. D55 has the numbers.

**Do not move recovery back outside the fetch.** Any enrichment written to
`announcements.json` after `collect()` runs is erased by the next fetch. That is
the entire bug. `tests/test_announcements.py::TestTheConfigKeyIsRead` and
`tests/test_adapters.py::TestTheBackfillRunsInThePipeline` go red if either
wiring is removed — both were checked by removing them.

**The wildcard CDX index lags the exact one.** `_exact_snapshot` exists because
of this and is not redundant belt-and-braces. Removing it silently loses
articles the archive does hold, reported as "not archived yet".

**Do not add `collapse=urlkey` to the bulk CDX query.** It is the obvious way
to cut the row count and it keeps the *oldest* snapshot of every article, since
CDX returns rows oldest-first. Confirmed live: with collapse,
`jalapeno-first-results` resolves to a 2026-08-25 snapshot instead of the
2026-08-29 one. `CDX_ROW_LIMIT` plus the truncation warning is the intended
guard instead. Same reason `_exact_snapshot` uses a *negative* limit.

**The archive returns partial bodies.** `_cdx_json` tolerates them and drops
the cached copy. Do not simplify it back to a bare `json.loads` -- a truncated
response is cached before anything validates it, so one bad read becomes hours
of them.

## 5. Known-open

**17 articles the archive has never crawled** — 11 customer stories, 4 policy
posts, 2 academy pages. Only `safety-overview-gpt-6-astra` matters. They are
listed in each run's `unresolved` rows with `kind: backfill`.

They are marked settled after their first run and never retried, so archive lag
becomes permanent: an article published today and archived tomorrow stays on its
blurb for ever. Two ways to close it, neither taken:

- **Save Page Now** — `web.archive.org/save/<url>` asks the archive to crawl on
  demand. It is an ordinary API call and belongs inside the pipeline, not a
  manual step. The archive can clearly reach openai.com (it holds 131 of 149),
  so this plausibly works where our own fetch gets 403. Untested — it makes a
  third party fetch a page, so it wants a deliberate n=1 first.
- **Do not settle a summary-only article**, so later runs retry it. Cheap
  mechanically, but it implies a **re-score**: a classification already exists
  at this prompt version, so better text would otherwise never reach a score.

## 6. Where the evidence lives

- `docs/decisions.md` §D55 — full reasoning, including what was rejected
- `docs/agentic-log.md` — the wildcard-index defect and how it surfaced
- `research/announcements/fetch_announcements.py` — `enrich_wayback`,
  `_exact_snapshot`, `_cdx_prefixes`, `_archive_key`
- `app/pipeline/adapters.py::fetch_announcements` — the production wiring
- `research/announcements/backfill_openai.py` — **now redundant**. Left in place
  rather than deleted; it still works standalone, but it writes to a file the
  next fetch overwrites, which is exactly the trap. Retiring it is a judgement
  call nobody has made. Its last live importer was `refresh_gold_text.py`,
  which now uses the pipeline's own recovery instead — so gold and production
  read a page the same way.

## 7. The follow-on nobody has decided

`fetch_wayback` caches to `research/docs/announcement_cache/`, which is in
`.gitignore` and `.dockerignore`, on a Render cron with no disk — the same
shape D53 found in the papers leg and fixed by moving to Postgres
`fetch_cache`. Steady state here is a handful of articles a night, so this is a
first-firing and re-backfill cost, not a nightly one. But the module docstring
still claims re-runs cost no requests, which is false in the deployed shape.
Migrating this leg onto `fetch_cache` is the obvious next piece of work.
