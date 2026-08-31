# Papers research — deriving a lab's people from what it publishes

**Status: complete, not carried forward.** The question this line of work asked was
answered, and the answer was mostly negative. Kept because the negative result is a
finding worth reporting and the evaluation harness is reusable.

Full reasoning is in [docs/decisions.md](../../docs/decisions.md); the procedure it
produced is [planning.md](../../docs/planning.md) §11, §11a, §11b.

## The question

Can a frontier lab's staff — and which of them matter — be derived from the author
lists on what the lab publishes? The specific hope was to reach the layer *below* the
media spotlight without LinkedIn or press coverage.

## What was built

| File | Purpose |
| --- | --- |
| `byline.py` | Nesting-aware byline extraction for Anthropic's three markup forms |
| `harvest_contributors.py` | Anthropic register: channels → articles → people, with LLM fallback |
| `llm_byline.py` | LLM extractor, cost recorded per call |
| `eval_byline.py` | Scores any extractor against the hand-checked reference |
| `alias_candidates.py` | Proposes person merges from the co-authorship graph |
| `deepseek_harvest.py`, `eval_deepseek.py` | Same, for DeepSeek's arXiv author lists |
| `openai_harvest.py`, `eval_openai.py` | Same, for OpenAI's credit sections |
| `build_*_page.py` | The three HTML contributor pages |

Prompts are versioned in `prompts/byline_extraction/`. Data lands in `../docs/`.
Total LLM spend across all three labs: **$4.51**, logged in [docs/cost.md](../../docs/cost.md).

## What worked

**Extraction, everywhere.** Both extractors performed well on all three labs, and after
adjudicating every disagreement by hand, **neither hallucinated a person**:

| Lab | Corpus | People | LLM F1 vs deterministic |
| --- | --- | --- | --- |
| Anthropic | 54 articles / 12 months | 175 | 0.978 |
| DeepSeek | 8 papers | 415 | 0.996 |
| OpenAI | 7 papers | 1,079 | 0.879 |

**Deterministic parsing beat the LLM where markup is known** — free, instant,
reproducible, and its failures are visible rather than plausible. The LLM earned a
narrow role as a fallback on pages the parser cannot read; it recovered 8 of 8 such
pages at Anthropic.

**The evaluation harness is the durable output.** It scores any extractor against a
hand-checked reference and prints every disagreement rather than averaging them. It
found more bugs in the deterministic parsers than in the LLM, and every catastrophic
score it ever produced turned out to be a harness fault, never a model fault.

**Roster aggregates are real signal.** Author counts across releases give headcount and
churn for private labs from primary sources: OpenAI 281 → 420 → 486, DeepSeek
197 → 264 → 328, with 49 people absent between two DeepSeek releases and 20 marked
departed by DeepSeek itself.

## What did not work

**Publication-derived importance does not generalise.** It needs frequent publication
*and* small author lists. Only Anthropic has both.

| | articles/yr | authors/paper | does frequency discriminate? |
| --- | --- | --- | --- |
| Anthropic | ~54 | 3–20 | yes |
| DeepSeek | ~3 | 200–330 | no — it measures tenure |
| OpenAI | ~2 | 300–486 | no |

Appearing on OpenAI's GPT-5 card places a person among 486. No threshold creates signal
that is not in the data.

**The one success case is narrower than it looks.** Anthropic's output is overwhelmingly
safety, alignment and interpretability, so the register is *"people who publish safety
research at Anthropic"*, not *"important people at Anthropic"*. Pretraining, RL,
inference and product engineering are largely invisible to it.

**Role labels do not transfer between labs.** "Core contributor" covers ~219 people at
OpenAI, 18 at DeepSeek, ~12 of 117 at Anthropic. Same string, elite at one lab and most
of the staff at another.

**Role structure is disappearing.** DeepSeek stopped marking core contributors after R1
(Jan 2025); OpenAI after o1 (Dec 2024). Two of three labs now publish author names with
no contribution structure at all.

**Corpus enumeration was manual at every lab.** No single query returns a lab's papers;
labs file under assorted author strings and have no index page. This turned out to be
harder than the extraction it feeds.

**`openai.com` is unreachable** — its CDN returns 403 to non-browser clients although
robots.txt permits crawling. arXiv was the only usable channel for OpenAI.

## What follows

- Person-level tracking is viable at Anthropic-shaped labs only. Coverage across the
  register is therefore deliberately uneven, and the design doc says so.
- No cross-lab importance score. Any such score would be dominated by publication
  cadence and would rank a mid-tier Anthropic researcher above OpenAI's chief scientist.
- Importance is sourced, not computed: the leadership register (planning §12) and
  press-reported moves, where the press does the filtering.
- Reaching non-publishing staff needs different sources. GitHub commit authorship is the
  next thing being tested (`../github/`).
