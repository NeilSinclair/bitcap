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
