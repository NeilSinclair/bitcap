# Cost log

Tokens and spend per workflow, recorded at the call site as runs happen. €100 budget,
reimbursed against receipts.

**Currency.** The API bills in USD. Every figure below is USD as charged; the € column
is left blank until the card statement gives the actual conversion rate applied. Do not
back-fill it with a spot rate — the receipt is what gets reimbursed.

| Date | Workflow | Model | Calls | In (tok) | Out (tok) | USD | EUR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-08-30 | Byline extraction eval, 17 Anthropic articles | claude-sonnet-5 | 17 | 370,210 | 12,127 | $0.8617 | *pending* |

Running total: **$0.8617**

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

**Extrapolation.** ~$0.05 per article at this budget. A register covering 10 labs at
Anthropic's publication rate (~17 articles per 3 months) is ~680 articles/year, so
**~$34/year** for LLM byline extraction alone — cheap in absolute terms, but 100% of it
is avoidable on channels whose markup is already known.
