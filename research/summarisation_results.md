# Summarisation before classification: results

Question (from `summarisation_research.md`): is it feasible to summarise
articles with a cheap model before LLM extraction/scoring, or should the
classifier read the raw articles?

**Answer: classify the raw articles.** Summarise-first saves 12–17% of pipeline
cost and destroys the axis the pipeline exists for — mechanism recall falls
from 0.81 to 0.56–0.63. Prompt caching saves more than summarisation does, at
zero quality cost.

## Setup

- 20 gold-set articles (`research/announcements/test/articles/`), human-adjudicated
  tags on three axes (68 tags total).
- Summarisers: `claude-haiku-4-5` and `gpt-5-mini` (reasoning_effort=low), prompt
  `prompts/summarisation/v1.md` — same vocab blocks as the scoring prompt injected,
  explicit instructions to preserve figures and mechanism-bearing claims verbatim.
- Classifier held constant: `claude-sonnet-5`, prompt v6, original `text_source`
  preserved (so the rss_summary confidence cap doesn't confound the comparison).
- Judge: Fable 5 read every original + both summaries in-session
  (`research/test_results/summary_fidelity_fable.yaml`).

## Cost (measured, provider-reported tokens)

| Step (20 articles) | Input tok | Output tok | USD | $/article |
|---|---|---|---|---|
| Summarise, Haiku 4.5 | 106,307 | 7,977 | 0.146 | 0.0073 |
| Summarise, GPT-5-mini (low) | 95,766 | 15,146 (5,440 reasoning) | 0.054 | 0.0027 |
| Classify raw (sonnet-5, v6) | 216,893 | 51,523 | 0.949 | 0.0475 |
| Classify Haiku summaries | 153,646 | 38,304 | 0.690 | 0.0345 |
| Classify GPT summaries | 156,974 | 42,180 | 0.736 | 0.0368 |

Pipeline per article: raw **$0.0475**; Haiku→classify **$0.0418** (−12%);
GPT→classify **$0.0395** (−17%). Per 191-article corpus run: $9.06 vs $7.99 / $7.54.

Why the saving is small: the classifier's fixed prompt (vocab + instructions +
schema) is ~7,300 input tokens per call — the article is only ~a third of input,
and output (the tagged JSON) barely shrinks (51.5k → 38–42k tokens). Compression
of 80–84% of the text removes only ~30% of billed input.

**The cheaper lever:** caching the fixed 7.3k-token prompt (cache reads at 0.1×)
saves ~$0.25 of the $0.95 raw run — more than summarisation saves — with zero
quality loss.

## Quality

End-to-end, classifier output vs gold (single runs; sonnet-5 has known
run-to-run variance — see `research/docs/variance_v4_sonnet-5.json` — but both
summary runs degrade in the same direction and the fidelity review shows the
evidence is genuinely absent, so the direction is trusted):

| | raw | Haiku summaries | GPT summaries |
|---|---|---|---|
| mechanisms recall | **0.81** | 0.63 | 0.56 |
| mechanisms micro-F1 | **0.77** | 0.65 | 0.67 |
| practices micro-F1 | **0.84** | 0.82 | 0.74 |
| event-type agreement | 90% | 90% | 80% |
| investment score MAE | 11.2 | 9.7 | 12.7 |
| AI-team score MAE | **8.3** | 16.1 | 10.6 |

Fidelity (does the evidence behind each of the 68 gold tags survive into the
summary): Haiku 52 present / 11 partial / 5 absent (~85% retention);
GPT-5-mini 54 / 10 / 4 (~87%).

## What actually gets lost

Headline figures survive almost perfectly in both models. What dies is the
**low-salience mechanism-bearing sentence** — and both summarisers independently
dropped several of the *same* ones, so the loss is systematic, not sampling
noise: "high-volume work economical at much greater scale", "more
token-efficient than past Claude models", "smarter context management",
"works smoothly across agent frameworks", "we expect demand to be very high",
methodology footnotes. These read as filler to a summariser; they are exactly
what `inference_volume_up`, `inference_cost_down`, `orchestration` and
`evaluation` tags hang on.

Second failure mode: **framing loss**. An opinion piece compressed to its
embedded benchmarks reads as a model release (art. 26); an incident writeup
compressed to its mechanics loses its policy-response character (art. 01).
Event-type errors follow.

Third: **summariser trust**. Haiku produced 8 unsupported-content incidents
across 6 articles — including twice leaking the tagging vocabulary into the
summary (arts. 23, 24: a `lab_capital_access`/`export_controls` aside that
could bias the classifier), twice reporting navigation links as article
content, and one factual distortion ("obtained a phone number" — the model
tried and failed). GPT-5-mini at low effort was strictly extractive: 1 minor
framing slip, no fabrications. The classifier's citation gate can't catch any
of this, because quotes are verified against the summary, not the source.

## Verdict

Run the classifier/scorer on raw articles. Adopt prompt caching for the cost
concern. Summarisation could only earn a place for documents where text ≫
prompt (long papers, 50k+ chars) and would still need the evidence-loss risk
priced in.
