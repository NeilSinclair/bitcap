# Cost log

Tokens and spend per workflow, recorded at the call site as runs happen. €100 budget,
reimbursed against receipts.

**Currency.** The API bills in USD. Every figure below is USD as charged; the € column
is left blank until the card statement gives the actual conversion rate applied. Do not
back-fill it with a spot rate — the receipt is what gets reimbursed.

| Date | Workflow | Model | Calls | In (tok) | Out (tok) | USD | EUR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-08-30 | Byline extraction eval, 17 Anthropic articles | claude-sonnet-5 | 17 | 370,210 | 12,127 | $0.8617 | *pending* |

| 2026-08-30 | Byline LLM fallback, 8 unparseable pages (12-mo window) | claude-sonnet-5 | 8 | — | — | $0.3637 | *pending* |

| 2026-08-30 | DeepSeek author-list eval, 3 papers (discarded — bad slice) | claude-sonnet-5 | 3 | — | — | $0.3992 | *pending* |
| 2026-08-30 | DeepSeek author-list eval, 3 papers | claude-sonnet-5 | 3 | — | — | $0.4891 | *pending* |

| 2026-08-31 | OpenAI eval, run 1 (truncated output — discarded) | claude-sonnet-5 | 1 | — | — | $0.3991 | *pending* |
| 2026-08-31 | OpenAI eval, run 2 (bad slice — discarded) | claude-sonnet-5 | 3 | — | — | $0.7670 | *pending* |
| 2026-08-31 | OpenAI credit-section eval, 3 papers | claude-sonnet-5 | 3 | — | — | $1.2281 | *pending* |

Running total: **$4.5079**

## Notes

**Byline extraction eval (2026-08-30).** `research/llm_byline.py`, prompt
`prompts/byline_extraction/v1.md`, cost written per call to
`research/docs/byline_llm_cost.json` so a crash cannot lose incurred spend.

- Sonnet 5 billed at the introductory $2.00/$10.00 per MTok rate, in effect through
  2026-08-31. At list ($3.00/$15.00) the same run costs **$1.29**; budget from list.
- Median latency 6.7s per page; most expensive single page $0.116.
- Input dominates 30:1. Cost here is a function of how much HTML is sent, not of the
  answer's size — pages were cut to 60,000 characters (`HTML_BUDGET`), which still sent
  370K tokens. Cutting harder to the byline region would drop cost by roughly an order
  of magnitude, at the price of no longer testing whether the model can *find* the
  byline.
- Deterministic extraction of the same 17 pages costs **$0.00** and runs in under a
  second.

**Byline LLM fallback (2026-08-30).** `harvest_contributors.py --llm-fallback`, cost
appended per call to `research/docs/fallback_cost.json`. Recovered **6 of 8** pages the
deterministic parser could not read, adding 33 bylines and 10 people. The two it left
empty genuinely have no byline — it declined to invent one, which is the behaviour the
tagging exists to guard against. $0.045/page, and it runs only on parser misses (8 of 54
articles), so the marginal cost of full coverage is ~7% of extracting everything with a
model.

**DeepSeek author-list eval (2026-08-30).** `research/eval_deepseek.py`, prompt
`prompts/byline_extraction/deepseek_v1.md`. Two runs: the first is recorded even though
its result was discarded, because the spend was real. It sent the last 90K characters of
each paper, and DeepSeek's author list is an appendix followed by *further* appendices,
so two of three papers never received the section at all. Re-run anchored on the section
itself. **Failed runs stay in this log** — a cost table that only shows successful runs
understates what the work cost.

$0.16/paper against 200-330 authors each, ~$0.0006 per author extracted — two orders of
magnitude cheaper per person than the Anthropic bylines, because DeepSeek's papers are
few and enormous.

**OpenAI eval (2026-08-31).** Three runs, two discarded, all recorded. Run 1 died on
truncated JSON: adaptive thinking shares the `max_tokens` budget with the answer, and a
330-name credit section did not fit in 32K. Run 2 anchored its slice on the last textual
match for "Contributors", which landed on a red-teaming acknowledgement in the GPT-4o
card and mid-list in o1's. Run 3 anchored on the section heading, as the deterministic
parser already did.

**$2.39 for one lab, 62% of it on discarded runs.** OpenAI is the most expensive lab so
far by a wide margin — 1,014 authors across three papers, ~130K characters of context per
call, and every retry pays full price. Worth noting for any future decision to run
LLM extraction across the register at scale.

**Extrapolation.** ~$0.05 per article at this budget. A register covering 10 labs at
Anthropic's publication rate (~17 articles per 3 months) is ~680 articles/year, so
**~$34/year** for LLM byline extraction alone — cheap in absolute terms, but 100% of it
is avoidable on channels whose markup is already known.

---

## Announcement scoring — three labs, six months (2026-08-31)

375 announcements classified against `config/mechanisms.yaml` and
`config/categories.yaml`. Model `claude-sonnet-5`, no thinking, structured output.

| | |
|---|---|
| Calls | 375 (0 failed, 0 retried) |
| Input tokens | 1,661,066 |
| Output tokens | 192,298 |
| **Total** | **$5.2451** |
| Median per call | $0.0111 (3,842 in / 239 out) |

**Where the money goes: the system prompt, not the articles.** Median input is 3,842
tokens against a median output of 239. The mechanism and category vocabularies are
re-sent on every call and account for most of that — an OpenAI RSS item is ~100 tokens of
content carried on ~3,700 tokens of instructions.

**Prompt caching is the obvious saving and was deliberately not done.** The system prompt
is byte-identical across all 375 calls, so caching it would cut input cost by roughly 80%
(~$4 saved on this run). At $5 for a full six-month backfill it was not worth the
complexity, but at register scale it is the first thing to add.

**Extrapolation.** $0.014 per announcement. The three labs produced 375 items in six
months; ten labs at that rate is ~2,500/year, so **~$35/year** uncached, or ~$8 cached.
The recurring cost of this pipeline is trivial — the backfill is the only real spend.

**Running total across all workflows: $9.75.**

---

## Model and variance experiments (2026-08-31)

Spend that produced **no register output at all**. It bought answers about reliability and
model choice, and is recorded separately so the cost of the pipeline is never confused with
the cost of finding out whether the pipeline can be trusted.

| Experiment | Calls | Cost |
|---|---|---|
| Variance probe, prompt v3, Sonnet 5 (12 items x 3) | 36 | $0.92 |
| Variance probe, prompt v4, Sonnet 5 (12 items x 3) | 36 | $0.87 |
| Variance probe, prompt v4, Haiku 4.5 (12 items x 3) | 36 | $0.20 |
| Voting, Sonnet 5 (6 items x 3 votes x 2 passes) | 36 | $1.15 |
| Voting, GPT-5-mini (6 items x 3 votes x 2 passes) | 36 | $0.25 |
| **Subtotal** | **180** | **~$3.39** |

Plus an earlier, discarded v3 probe and a partial v2 re-scoring run, both included in the
running total.

**Per-model unit economics on the same six articles, 3 votes each:**

| Model | Per voted item | Voted score reproducible |
|---|---|---|
| Sonnet 5 | $0.19 | 6/6 |
| GPT-5-mini | $0.04 | 4/6 |
| Haiku 4.5 | ~$0.05 (single-run probe) | not voted; 9/12 agreement with Sonnet |

GPT-5-mini pricing is **UNVERIFIED** — token counts are exact, the $/token conversion in
`providers.py` was not checked against OpenAI's published rates.

**Lesson recorded in [planning.md](planning.md) 6a: keep variance samples small.** Probing
cost ~$3.39 and produced nothing shippable. Future probes use 5-6 items at 5 runs on a
fixed named sample, never the whole corpus.

**Projected backfill with the chosen configuration** (Sonnet 5, 3-vote consensus, 375
announcements): ~$21 uncached, ~$10 batched. Recurring incremental runs are a fraction of
that, since only new announcements are classified.

**Running total across all workflows: $14.18.**

### Gold-set run with both axes (2026-09-01)

20 gold articles, `claude-sonnet-5`, prompt v5, cache bypassed. First run
carrying the practice axis alongside mechanisms.

| | |
|---|---|
| Articles | 20 |
| Paid calls | 20 |
| Cost | **$0.8173** ($0.0409/article) |
| Wall clock | 62s at 10 workers |
| Failures | 0 |

Per-article cost is ~2.8x the 192-article corpus rate of $0.0148 because v5 adds
the practice vocabulary and its dimension list to every system prompt. Worth
knowing before the full re-classification: 192 articles at this rate is ~$7.85,
not the ~$2.84 estimated from v4 pricing.

Reviewed by hand in [gold_review.md](gold_review.md).

### OpenAI archive backfill (2026-09-01) — €0.00, and it raises the next bill

No LLM calls: 1 CDX query plus 138 archive fetches, all free. The cost entry
that matters is what it does to the *next* classification run.

| | Before | After |
|---|---|---|
| Articles | 192 | 191 (one dropped as out-of-window) |
| Median OpenAI text | 205 chars | 9,270 chars |
| Corpus text | ~485k chars | **1,908k chars** |
| RSS-summary articles | 152 | 11 |

The gold set is 20 full-text articles, so the measured **$0.0409/article** is
already the full-text rate rather than the summary rate. Re-classifying the
corpus under v5 is therefore ~**$7.80**, essentially unchanged by the backfill —
the earlier ~$2.84 figure was the v4-on-summaries rate and is obsolete.

Wall clock for the backfill was ~19 minutes, almost all of it the deliberate
2s serial delay. That is a rate limit, not inefficiency.

### Gold-set rebuild and re-prefill (2026-09-01)

20 articles, `claude-sonnet-5`, prompt v5, both axes, confidence gate and
dimension cap live.

| | |
|---|---|
| Articles | 20 (10 retained, 10 blind-drawn) |
| Paid calls | 20 |
| Cost | **$0.8768** ($0.0438/article) |
| Failures | 0 |

Up 7% per article on the previous gold run ($0.0409) because nine articles that
were 200-character RSS summaries are now full archived text. That is the real
per-article rate for a full-text corpus and is the number to plan the corpus run
against: **191 x $0.0438 = ~$8.40**.

The rebuild itself (`rebuild_gold.py`, `refresh_gold_text.py`) cost nothing —
no LLM calls, and the archive is free.


### Summarise-before-classify experiment (2026-09-02)

Research question: cheap-model summarisation ahead of extraction/scoring.
Full results: `research/summarisation_results.md`.

| Step (20 gold articles) | Model | Cost | $/article |
|---|---|---|---|
| Summarise | claude-haiku-4-5 | $0.1462 | $0.0073 |
| Summarise | gpt-5-mini (low effort) | $0.0542 | $0.0027 |
| Classify raw (baseline, run earlier today) | claude-sonnet-5, v6 | $0.9490 | $0.0475 |
| Classify Haiku summaries | claude-sonnet-5, v6 | $0.6903 | $0.0345 |
| Classify GPT summaries | claude-sonnet-5, v6 | $0.7357 | $0.0368 |

Experiment spend this session (excl. the pre-existing raw baseline): **$1.63**.

Outcome: summarise-first saves only 12–17% end-to-end (the v6 fixed prompt is
~7.3k of the ~10.8k input tokens per call, and output doesn't shrink) while
mechanism recall drops 0.81 → 0.56–0.63. Rejected; prompt caching on the fixed
prompt saves more (~$0.25/run of $0.95) at zero quality cost.

### Prompt caching verification (2026-09-02)

Two live `classify()` calls on the smallest gold article to verify the cache
lands and the accounting is right: cold call 7,059 cache-write tokens $0.0207;
identical warm call 7,059 cache-read tokens $0.0043. Spend: **$0.025**.
Measured fixed-prompt size: 7,059 tokens. Expected saving ~$0.24 per 20-article
gold run, ~$2.40 per 191-article corpus run.

### Vocabulary v2 / prompt v7 validation (2026-09-02)

Three fresh gold runs while sharpening the vocabularies and prompt (one
truncated-output casualty, one adopt-heavy iteration, one final):
$0.5552 + $0.7294 + $0.7364 = **$2.02**. Final config: mechanisms F1 0.88,
categories F1 1.00, investment MAE 5.7 (`gold_run_20260902T080200Z_v7_vocab2c`).

### Full corpus run, v7 prompt + v2 vocab (2026-09-02)

191 articles (2026-06-01 to 2026-08-31), `claude-sonnet-5`, prompt caching live.

| | |
|---|---|
| Articles | 191, 0 failures |
| Cost | **$5.44** ($0.0285/article) |
| Cache | 1.73M tokens read at 0.1x, 9k written |
| Wall clock | 6m 52s |

$1.6M of the 2.3M total input tokens came from cache at 90% off — the measured
rate is $0.0285/article against $0.0475 uncached ($9.06 projected), so caching
saved ~$3.60 on this run alone. Note: the script's progress line prints the
cumulative cost log, not the run; the $12.92 on screen includes prior runs.

### Database layer + join implementation (2026-09-02)

Zero marginal LLM spend: the DB load reads committed artifacts. Full Postgres
rebuild (191 articles, 706 connections) runs in ~6s locally.

### Google DeepMind papers harvest (2026-09-02)

`research/papers/deepmind_harvest.py`, prompt `prompts/byline_extraction/v2.md`
(generalized from v1 — see that prompt's own diff note), `claude-sonnet-5`.
Cost written per call to `research/docs/deepmind_llm_cost.json`.

| | |
|---|---|
| Window | 3 months, 17 candidate papers from the sitemap |
| Papers processed | 15 (2 skipped — genuine source-fetch failures, not billed) |
| Distinct authors | 65 (38 tagged `is_lab_staff`) |
| Cost | **$0.8569** (~$0.057/paper) |
| First proving run (--limit 3, discarded — see below) | $0.1534 |

Two live bugs found and fixed mid-run, both from hand-checking actual output rather
than from a test written in advance: (1) DeepMind's HTML renders attributes unquoted
(`href=https://...`), which a quote-only regex silently matched nothing against — the
first 3-paper proving run returned 0 authors on 3/3 papers before this was caught,
and that $0.1534 is discarded, not representative. (2) `fetch()` had no retry/backoff;
a transient 403 from arXiv mid-batch crashed the whole run until fixed to match
`fetch_announcements.py`'s existing pattern. Full account in
[`research/papers/README.md`](../research/papers/README.md).

Extraction results are cached per-paper URL (`deepmind_extraction_cache.json`), so
re-running this script — e.g. to extend the window — only bills genuinely new papers.

### Meta AI papers harvest (2026-09-03)

`research/papers/meta_harvest.py`, same v2 prompt and model as DeepMind. Cost
written per call to `research/docs/meta_llm_cost.json`.

| | |
|---|---|
| Window | 3 months, 27 candidate papers (2026, the only year the window touches) |
| Papers processed | 5 in-window after arXiv-date resolution (1 more resolved but correctly out-of-window) |
| Distinct authors | 41 |
| Cost | **$0.4327** (6 calls — one paper re-extracted after a truncation fix, see below) |
| Discovery proving run (5 papers, discarded — see below) | $0.4771 |

Meta's detail pages carry no resolvable arXiv link or date in server HTML (JS-populated,
confirmed live), so every paper is resolved to arXiv by title match first, before any
extraction call — the $0.4771 proving-run figure predates the real 3-month window and is
not representative; it proved the resolution design, not the corpus.

Two live bugs found and fixed after hand-checking output against the real arXiv HTML,
both in `llm_byline.py`/`meta_harvest.py` shared code, not Meta-specific: (1) the
60,000-char `HTML_BUDGET` cut from byte 0 of the page, and one paper's real byline sat
past ~66KB of arXiv's own site chrome — the model correctly reported `no_byline` on a
page it had never actually seen, at a cost of $0.0591 for a wasted call. Fixed by
starting the budget window at `<article>`; re-extraction cost $0.0604. (2) the
relaxed-search arXiv fallback checked only the top-ranked candidate — the correct match
for one real paper ranked second, behind an unrelated paper that shared only an acronym.
Fixed to score every candidate and take the best. Full account in
[`docs/decisions.md`](decisions.md) D16.

Extraction results are cached per-paper URL (`meta_extraction_cache.json`); unresolved
candidates are recorded to `meta_unresolved.json` with a reason rather than billed or
guessed at.

### Mistral AI papers harvest (2026-09-03)

`research/papers/mistral_harvest.py`, same v2 prompt and model as DeepMind/Meta. Cost
written per call to `research/docs/mistral_llm_cost.json`.

| | |
|---|---|
| Window | 3 months, 9 candidate titles (Mistral's own in-window announcements) |
| Papers processed | 2 resolved (Shieldstral, Robostral Navigate), 7 correctly unresolved |
| Distinct authors | 23 |
| Cost | **$0.0891** (final, correct run) |
| Discarded debugging spend (see below) | ~$0.34 |

Three head-only/head+tail extraction attempts on the same two real papers returned 0
authors each before a correct fix was found — each is real, billed spend, not a proving
run predating the corpus this time, so it's disclosed rather than folded into the final
figure: $0.1208, then $0.1250 after a first (insufficient) fix, then $0.0925 for one
correct call plus one call whose JSON output failed to parse after hitting the output
truncation the still-too-wide window caused. That failed call's own cost was billed by
the API but did not reach `mistral_llm_cost.json` at the time — `extract()` built the
cost record before parsing the model's JSON, but only returned it alongside a
successfully parsed result, so a `JSONDecodeError` lost it. Fixed the same session it was
found: `llm_byline.py` now raises `ExtractionError` carrying the cost record on a parse
failure, and all three papers harvesters (`meta_harvest.py`, `mistral_harvest.py`,
`deepmind_harvest.py`) record it before treating the paper as unresolved, instead of
losing it. Not reconstructable after the fact, so worth being exact that this is what
happened rather than rounding it away.

Full account of what caused three attempts -- a "Contributors" heading positioned deep in
the document rather than near the top or the true end, and a still-too-wide window that
first overran into a references section -- in [`docs/decisions.md`](decisions.md) D17.

Extraction results are cached per-announcement URL (`mistral_extraction_cache.json`);
unresolved candidates are recorded to `mistral_unresolved.json` with a reason rather than
billed or guessed at.

### xAI announcements leg (2026-09-03)

`research/announcements/fetch_announcements.py`'s `wayback_cdx` method (D20). **$0.00** --
no LLM involved, same as the GitHub leg. All cost here is wall-clock, not money: the
Internet Archive's own rate limit (`WAYBACK_DELAY_SECONDS = 2.0`, matching
`backfill_openai.py`'s existing pacing) makes a cold run of ~35-80 candidate snapshot
fetches take a few minutes; a re-run is near-instant once cached (confirmed live: 2.1s for
a fully-cached re-run of all seven labs combined, versus several minutes for the first
xAI run).

## Scheduled-pipeline classification and drift (2026-09-03)

First spend under the budget governor (`config/pipeline.yaml`, D24). Everything
here is from `raw_costs` / `announcement_cost.json`, recorded per call as the
runs proceeded.

| Workflow | Calls | $ | Note |
|---|---:|---:|---|
| Budget-guard probe #1 (ceiling $0.08) | 15 | 0.5315 | Overshot 6.6x — the finding, see below |
| Budget-guard probe #2 (ceiling $0.20) | 7 | 0.2112 | Overshot 5.6% after the fix |
| Backlog classification (61 articles) | 39 | 1.0762 | The four newest labs, first time scored |
| First drift measurement (6 gold items) | 6 | 0.1949 | Uncached by design |
| Register rebuild (236 articles) | 0 | 0.0000 | Fully cached — idempotence, demonstrated |

**Subtotal this session: $2.014.** Running total across all workflows: **$16.19**.

**$0.53 of that was the price of finding a real bug, and it was worth it.** The
first probe set a deliberately tiny $0.08 ceiling to prove the guard stopped a
live run. It did stop it — after spending $0.53. With 12 concurrent workers,
every worker tested a running total none of its peers had contributed to yet, so
the fan-out overshot by roughly `workers x per-call cost`. At the real $3.00
ceiling that would have been a ~14% overrun and probably never noticed; at $0.08
it was unmissable. The guard now counts in-flight calls (`begin_call`/`end_call`)
and the second probe overshot by 5.6%.

**Calibration.** 61 articles cost $1.0762 across 39 paid calls (22 were already
cached from the probes) = **$0.0276 per article**, against the $0.029 seed in
`budget.DEFAULT_CALL_USD` taken from the v7 corpus. Drift ran $0.0325 per item,
higher because it bypasses the cache and pays a cache *write* on its first call.

**Standing cost of drift monitoring.** 6 uncached items per firing at ~$0.195.
Daily, that is ~$5.85/month against the $20 monthly ceiling — the single largest
recurring cost in the pipeline, and it buys no product output at all. It is
here because planning.md §6a requires the app to report its own variance, and
because a scorer that quietly degrades is the failure this system exists to
catch. If the monthly budget ever binds, `cadence.drift` is the first dial to
turn.

---

## Release notes leg, first firing (2026-09-04)

The releases leg ingests for free: GitHub's API is unmetered on an
authenticated token, and the first firing's 8 sources cost **$0.0000**.

| Workflow | Calls | USD | Note |
|---|---:|---:|---|
| Release ingestion, 8 orgs | ~400 REST | $0.0000 | free tier, 5,000/hour |
| Release classification | not yet run | — | 380 documents queued |

**380 documents are ingested and unclassified.** At the measured $0.028 per
release that is roughly **$10.64**, against a `per_run_usd` ceiling of $3.00 --
so the budget guard will spend $3, report the rest as `skipped_for_budget`, and
catch up over about four firings. That is the guard working, but it is not what
this leg is for, so `releases_watch` is now 10 per org rather than 40.

**First-mention detection costs nothing at all.** It is one query over
`raw_articles` and a regex; no LLM call is made, on any run.
## 2026-09-04 — v8 prompt probe, and the per-run ceiling raised

| Workflow | Calls | $ | Note |
|---|---:|---:|---|
| Astra re-score probe, v8 draft 1 (`model_spec` only) | 2 | 0.0836 | Score went *down*, 50 → 33.3 — see D52 |
| Astra re-score probe, v8 final (n=3, practice confidence) | 3 | 0.1420 | 100 / 33.3 / 100 — modal answer now high |

**Subtotal: $0.226.** Running total across all workflows: **$16.42**.

Paid deliberately rather than committing to a $6.25 corpus re-run on a guess.
It earned its price twice: it caught that the first fix made the score worse,
and n=3 exposed run-to-run instability (two identical calls disagreeing 66.7 vs
100 on the investment axis) that a single sample had hidden. Called
`classify()` directly, so nothing was written to cache, `announcements.json` or
the database — these are eval calls, not pipeline spend, and none of them
appear in `raw_costs`.

**Ceilings raised: `per_run_usd` 5.00 → 15.00, `per_month_usd` 75.00 → 250.00.**

*Per-run.* The old value no longer admitted what its own comment claimed: a
full re-classification is ~$6.25 at 250 articles ($0.025/article measured over
583 calls), so the v8 bump would have aborted mid-stage and split the corpus
across two prompt versions — spending the money without producing the result.

*Per-month.* A change of kind, not degree, and recorded plainly: **$250 is more than double the €100 budget**, so the config
no longer contains a control that protects it. It is a runaway guard — it stops
a loop or a retry storm, not deliberate overspend. From here the budget control
is this file and someone reading it, which is weaker than a number the
pipeline enforces. Decided by Neil, 2026-09-04.

**Committed but unspent: ~$6.25.** `PROMPT_VERSION` is `v8`, so all 250
articles are pending. Month stands at $14.58 in `raw_costs`, and drift adds
~$0.75 nightly, so the re-run lands September near $41. That is inside the
€100 project budget — which is now the only limit that matters, since the
config ceiling no longer binds below it.

## 2026-09-05 — v9 re-score after the OpenAI text recovery

| | |
|---|---|
| articles | 259 |
| failed | 0 |
| cost | **$7.16** |
| model | claude-sonnet-5 |
| ceiling | `per_run_usd: 15.00` |

Estimated $7.47 beforehand and it came in at $7.16. Worth recording why the
estimate had to be rebuilt rather than taken from the log: the standing
$0.025/article rate was measured when OpenAI — 59% of the corpus — was
200-character summaries. Recovering that text made the corpus 2.3x larger in
characters, so the old rate understated this run by about 15%.

Not spent **against the local database**: ~$10 that a v9 re-score of the 380
release documents would have cost. The rows were carried forward with a
`_carried_forward` provenance marker instead (D56, D56a).

Read that as local-only. `carry_forward_releases.py` writes to whichever
database it is pointed at and is wired into no deploy path, so any environment
whose Postgres has not had it run — and any `bitcap-db rebuild`, which drops the
carried rows — pays the ~$10 on its first firing. The saving is real and it is
not yet portable.

## Duplicate collapse (D59) — 2026-09-05

| Workflow | Model | Calls | Tokens | USD |
|---|---|---:|---:|---:|
| Embedding the corpus | `text-embedding-3-small` | 7 batches | 34,115 | $0.000682 |
| Duplicate adjudication | `claude-sonnet-5` | 41 | ~62,000 | $0.1834 |
| **Total** | | | | **$0.1841** |

Adjudication was billed three times, not once: at the original thresholds, again
after the labelling-order leak moved them (D59a), and again after the human
spot-check moved them a second time (D59c). **A production run pays for one
pass** — 19 pairs in the band, about six cents — and an incremental run pays for
almost none, since only new pairs reach it. Calibration is a build cost, not a
running one.

647 articles embedded for **under a tenth of a cent**, because what is embedded
is title + classifier summary rather than the article body — 37k tokens against
roughly 700k for the full text, and the bodies are mostly site chrome that
inflates similarity between any two pages from one lab.

**A re-run costs nothing.** Vectors are cached on a hash of the embedded text,
so an unchanged corpus embeds zero rows; verified live (647 embedded, then 0).
The adjudication band is the only per-pair spend and only 12 of 4,412 candidate
pairs fall inside it. Incremental runs adjudicate near zero.

The one recurring cost to watch is a **prompt-version bump**: that rewrites
every summary, which invalidates every cached vector. A v10 re-classification
therefore carries this $0.0007 with it — negligible, but it is the reason the
cache keys on the embedded text rather than on the article payload.

EUR: *pending* — with the rest, at the card statement.

---

## 2026-09-05 — papers become a scored corpus (D57)

| | |
|---|---|
| papers scored | 47 of 49 |
| failed | 0 |
| n=5 probes before the run | $0.100 |
| **corpus run** | **$0.7537** |
| model | claude-sonnet-5, prompt `p1` |
| ceiling | `per_run_usd: 15.00`, unbound |

Projected $1.20, came in at **$0.75**. The projection assumed abstracts near
5,000 characters; the corpus mean is 2,011, because 30 of 47 are structured
abstracts (arXiv `<blockquote>`, DeepMind's "Abstract" heading) rather than the
longer lead sections the Anthropic and Meta pages give.

**What the scope decision saved.** Full paper text was measured at ~59,200 input
tokens per paper — mean 182,974 visible characters across five arXiv `/html/`
pages, about 43x the mean article in this register. That run would have cost
**~$11.30**, and the case for it was that it makes `text_source: full_text`
literally true. It was rejected because every figure that made these papers
worth scoring is in the abstract (D57), so the extra $10.55 buys ablations,
appendices and bibliographies.

**What the version decision saved.** Papers classify under their own `p1`
version, so v9 and the 647 existing classifications were not touched. Sharing
`PROMPT_VERSION` would have meant a v10 bump at ~$0.0258/article — **~$16.70** to
re-ask an unchanged question of unchanged text.

Not free, and worth naming: the five probe calls at $0.100 were `classify_one`
against a scratch cache, so they wrote nothing to the register and are not in
`raw_costs`. Same convention as the v8 probes above — eval calls, not pipeline
spend. They are counted in the project total.

**Running total across all workflows: ~$18.05.** Month-to-date in `raw_costs`
stands at $29.29 against the `per_month_usd: 250.00` runaway guard, which as
recorded on 2026-09-04 no longer binds below the €100 project budget.

### Paper gold baseline (2026-09-05)

`research/papers/grade_paper_gold.py`, 10 gold papers under `p1`,
`claude-sonnet-5`, **uncached by design** — a grading run read through the
result cache reports perfect agreement forever.

| | |
|---|---|
| papers graded | 10 of 10, 0 errors |
| **cost** | **$0.1538** (~$0.015/paper) |
| result | `research/test_results/paper_gold_20260905T000000Z_p1.json` |

Not pipeline spend and not in `raw_costs`: this is an eval call against a
standalone script, the same convention as the v8 and `p1` probes above, and it
is counted in the project total. **A one-off, not a nightly charge** — D58
rejected the recurring drift check, so this is billed when `p1` or the
classification model changes, not on a cadence. Had it run nightly it would have
been ~$4.60/month on top of the announcement check's ~$22.

**Running total across all workflows: ~$18.20.**

### X posts leg — first full run (2026-09-05)

Two separate bills: X charges for the data, Anthropic for classifying it.

**X API**, pay-per-use at the published rates ($0.005/post read, $0.010/user
read, [pricing](https://docs.x.com/x-api/getting-started/pricing)):

| stage | requests | resources billed | cost |
|---|---:|---:|---:|
| resolve + verify 27 handles | 1 | 27 users | $0.27 |
| rate probe (`max_results=5`) | 27 | 74 posts | $0.10 |
| the pull (caps from the probe) | 19 | 473 posts | $2.37 |
| | | | **$2.74** |

The probe billed 74 posts rather than the 135 requested, because X bills what
*returns* — the asymmetry the allocator exploits (D63): asking a silent handle
for a full page costs nothing, so the expensive mistake is asking too little of
a prolific one, never too much of a quiet one.

**Classification**, 238 posts under `t1`, `claude-sonnet-5`: **$2.6053**
(~$0.011/post). Cheaper per item than the papers' $0.015 because a post is
short, but not proportionally so — a ~3k-token prompt dwarfs a 280-character
document, which is exactly why the deterministic prefilter runs first. Without
it the 235 dropped posts would have added ~$2.60 to confirm that "check our
model out!" is not an investment signal.

**Leg total: $5.35.** Recurring cost at the configured weekly cadence is the
pull plus classification only — the handle resolution and the rate probe are
manual steps re-run when the register changes, not per firing.

**Running total across all workflows: ~$23.55.**
