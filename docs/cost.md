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
