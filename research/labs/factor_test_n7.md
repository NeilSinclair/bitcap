# Factor test, n=7 — reading real lab articles by hand

**Date:** 2026-08-26. **Purpose:** before automating extraction, read raw items from the six focus
labs and record which proposed factors actually populate, which are dead weight, and what breaks.
Per [planning.md](../docs/planning.md) §8.4.

Every item below was fetched live and is linked. Extraction was done by prompting for **verbatim
quoted spans only**, with "say *none stated* if absent" — the same discipline the pipeline will need.

## The sample

Deliberately mixed: 2 expected high-value, 2 mid, 3 expected noise. The noise items matter most —
"did it keep the noise out" is the question the submission is judged on.

| # | Lab | Item | Date | My prediction |
| --- | --- | --- | --- | --- |
| 1 | Mistral | [In-region inference, open models, new European infrastructure for sovereign AI](https://mistral.ai/news/regional-inference-open-models-new-compute/) | 2026-08-11 | mid |
| 2 | Mistral | [Mistral x HUMAIN](https://mistral.ai/news/mistral-x-humain/) | 2026-08-24 | high |
| 3 | Anthropic | [Funding better evaluations of AI's impact on wellbeing](https://www.anthropic.com/news/wellbeing-research-grants) | 2026-08-25 | noise |
| 4 | Anthropic | [Tino Cuéllar joins as Chief Global Affairs Officer](https://www.anthropic.com/news/tino-cuellar) | 2026-08-04 | mid |
| 5 | Google DeepMind | [Introducing Gemini 3.7 Flash](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-3-7-flash/) | 2026-08 | high |
| 6 | Google DeepMind | [WeatherNext: forecasting cyclones](https://deepmind.google/blog/weathernext-ai-model-achieves-breakthrough-in-forecasting-cyclones/) | 2026-08 | noise |
| 7 | DeepSeek | [DeepSeek-R1 Release](https://api-docs.deepseek.com/news/news250120) | 2025-01-20 | known outcome — calibration |

---

## F1 — Page furniture bleeds into extractions, and it is the worst failure class

**Both** Anthropic items pulled facts from *related-content sidebars* rather than the article body:

- On item 3 (wellbeing grants), the extractor reported **"Mariano-Florentino (Tino) Cuéllar,
  Chief Global Affairs Officer"** as a named individual. He is not in that article.
- On item 4 (the Cuéllar hire), it reported **"$5 million grant program"** as a monetary amount.
  That figure belongs to item 3.

This is more dangerous than hallucination. The string genuinely **is** on the page, so a naive
citation check — "does this text appear at the source URL?" — **passes**. The claim is wrong and
the verification says it is right.

**Requirement this produces:** the quoted span must be validated against the **article body
boundary**, not the fetched page. Extraction operates on main-content only, and the citation check
must resolve to that same subtree. Without this, the system's central promise — every insight cites
a resolvable primary source — is satisfiable by false insights.

## F2 — The highest-value investment item had the least promising headline

Item 1 reads like a product post. Its contents:

- **"up to 1 GW of capacity by 2030"** → `datacenter_power_up`
- **Christophe Fouquet, CEO of ASML, quoted** — ASML is Mistral's largest shareholder *and* a BIT
  holding, inside 7.77% European semiconductor exposure
- a partnership with **Z.ai** — a listed frontier lab (SEHK 2513)
- **NVIDIA**, the NVIDIA Nemotron Coalition, and the Open Secure AI Alliance
- CEOs of **CMA CGM, Capgemini, Caisse des Dépôts, Amadeus, Factory** all quoted
- four models released: Mistral OCR 4, Mistral Medium 3.5, Mistral Small 4, Voxtral TTS

A sovereign-EU compute buildout announcement wearing a product-launch headline.
**Any cheap headline-based pre-filter would have dropped it.** Triage must read the body.

## F3 — `named_counterparty` must never be filtered to holdings *(corrected)*

Item 5 named twelve customers: Box, Browser Use, Cartwheel, Databricks, Emergent, Harvey, Hebbia,
LangChain, Nunu.ai, Open Code, Pydantic, Stanford Department of Biology. Almost all private, none
held by BIT.

**The first version of this finding was wrong.** It proposed that the investment renderer keep
`is_public == true`, resolve to a ticker, and **drop the rest**. That reasoning treated "is this a
BIT holding?" as the admission test. It is not. The test is **"does this entity share a mechanism
with a holding?"** — and an entity that BIT does not own, or that is not listed at all, routes to
holdings perfectly well.

Worked example, in BIT's own book. Suppose an event names **Oracle**, which BIT does not hold. It
still implies `training_compute_up`, and Micron, TSMC and Marvell carry positive links on that
mechanism. The event reaches the book without Oracle ever being held, and without Oracle being
named anywhere near a holding.

Second path, which BIT has actually traded: **Micron → SanDisk**. `companies.yaml` already records
`peers_in_portfolio` on Micron — SanDisk as NAND supplier, Silicon Motion as controller silicon,
Marvell as custom accelerator and interconnect. The [research notes](human_research_notes.md)
record BIT rotating Micron gains into exactly those names. So read-across between comparables is
not a modelling invention; it is observed behaviour of this fund.

**Corrected rule:** `is_public` and `ticker` are **attributes, never gates**. Nothing is dropped for
being unheld, unlisted, or private. A private counterparty is still a node — Databricks, Harvey and
LangChain appearing as Gemini customers is a real `agentic_workflows` datapoint about where
application-layer value is accruing, not noise to be filtered.

## F4 — Inference cost is measurable, not a judgement call

| Item | Published price |
| --- | --- |
| Gemini 3.7 Flash | "$0.75/1M input tokens and $3.75/1M output tokens" introductory, "half the original 3.6 Flash cost per million tokens"; "$1.50/1M input tokens and $7.50/1M output tokens" from 2027-01-01 |
| DeepSeek-R1 | "$0.14 / million input tokens (cache hit)", "$0.55 / million input tokens (cache miss)", "$2.19 / million output tokens" |

`inference_cost_down` does not need an LLM to judge magnitude when the lab publishes a price. A
price delta is arithmetic. **Recommendation:** make this mechanism deterministic wherever a price is
published, and LLM-judged only where it is not. This is the first concrete agent-vs-code bet the
data has settled rather than argued.

Gemini 3.7 Flash also gave clean capability deltas: FrontierCode 43.6% vs 34.4%, DeepSWE 65.3% vs
49.0%, AutomationBench 30.4% vs 17.0%, WebDev Arena Elo 1588 vs 1538.

## F5 — Licence is the AI team's decisive field, and it extracts cleanly

Three items, three different adoption verdicts from one field:

- **DeepSeek-R1** — "Code and models are released under the MIT License: Distill & commercialize
  freely!" → adoptable in production
- **WeatherNext** — "we are open sourcing the code and model weights, making them freely available",
  with a GitHub URL → adoptable, reproducible
- **Gemini 3.7 Flash** — API access only, no weights → evaluate, cannot self-host

`licence` + `artifact_availability` is to the AI-team renderer what `named_counterparty` is to the
investment one: the field that converts interest into action.

## F6 — One of my three "noise" predictions was wrong

- **Item 3 (wellbeing grants)** — correctly near-zero. A "$5 million grant program", no partners, no
  artifacts. The system should suppress this, and it is the cleanest true-negative in the set.
- **Item 6 (WeatherNext)** — **I was wrong.** Zero investment signal, but real AI-team signal: open
  weights *and* code, "a single 15-day forecast in less than a minute on a TPU", ensemble scaled
  from 50 to 1,000 members, a mini variant that "run[s] on a single TPU in a free public Colab
  notebook". Also a faint `custom_silicon_shift` datapoint — TPU inference efficiency, disclosed.

**Consequence:** an item can be noise for one audience and signal for the other. Scoring must be
**per-audience**, not a single score with two renderings. This is a design change, and it was found
by reading, not by reasoning.

## F7 — `personnel_move` needs a function field

Item 4 is a senior hire — first Chief Global Affairs Officer, previously "President of the Carnegie
Endowment for International Peace" and "Justice of the Supreme Court of California". But it is a
**policy** hire, not research and not dealmaking. Investment relevance is low-to-moderate: it
signals regulatory posture, nothing about capability or capex.

The brief names departures and stealth-startup formation as top-tier signal. Those are *research*
and *founder* moves. Without a `function` discriminator — research · dealmaking · policy · product —
a policy hire and a star researcher's departure score identically. They should not.

## F8 — Lab-published deal announcements are systematically vaguer than press reports

Item 2 announces "a new strategic collaboration with HUMAIN to support sovereign AI in Saudi Arabia
and across the Middle East", quantified only as **"a collaboration in the hundreds of millions of
Euros"** — no megawatts, no duration, no named individuals, and explicit hedging: *"Actual results
may differ materially due to various risks and uncertainties and subsequent commercial agreements."*

Compare the press-reported Anthropic deals of the same class: **Riot, $9bn, 20 years, 191MW**;
**Nscale, $45bn, 460MW**. The lab's own channel is the *weaker* source for the highest-value event
type in the system.

**This is a coverage decision with cost implications.** If `lab_compute_contract` is the most
valuable extraction target (D7), and labs under-disclose it, then press is not optional
supplementary coverage — it is the primary source for that event type. Needs deciding and recording.

## F9 — Dead-weight and blocked fields

- **`training_hardware` is almost always absent.** Not stated for Gemini 3.7 Flash or DeepSeek-R1.
  Labs do not disclose it. Make optional; do not build scoring that depends on it.
- **Numbers in images.** DeepSeek-R1's benchmark table is an image; only the prose claims
  ("Performance on par with OpenAI-o1") were extractable. A text-only pipeline silently loses the
  quantitative half of a model-release page.
- **openai.com returns HTTP 403 to the fetcher.** A hard ingestion constraint on the #2 focus lab.
  Needs a route — different user agent, RSS, or press as proxy — recorded before it becomes a
  silent coverage hole.

---

## F10 — The entity registry has to be wider than the holdings file

F3 generalises into a structural problem. `config/companies.yaml` is documented as "one entry per
security held by a BIT fund". Under the corrected model that is too narrow: most entities named in
lab publications are not BIT holdings, and some are not listed at all.

Three classes are needed, and only the first exists today:

| Class | Example | Has ticker | Has mechanism links |
| --- | --- | --- | --- |
| **Held** | Micron, IREN, Amazon | yes | yes — the current file |
| **Reference** | Oracle, CoreWeave, Broadcom, Vertiv, Constellation | yes | yes, but no position |
| **Private** | Nscale, Volta, Databricks, Harvey, Anysphere, the labs themselves | no | yes |

Propagation becomes **event → named entity → mechanism → holdings**, where only the last step
touches the portfolio. Two distinct routes reach the book:

- **Shared mechanism (the general case).** The named entity and a holding both carry links to the
  same mechanism. No relationship between them is needed.
- **Peer read-across.** The named entity *is* a comparable of a holding — Micron/SanDisk,
  SK Hynix/Micron. `peers_in_portfolio` is the seed of this and should become a first-class,
  bidirectional relation rather than a per-company annotation.

**Consequence:** adding a reference or private entity is a config change that costs one entry and
its mechanism links — no position data, no price series, no `held_by`. That keeps it cheap enough
to add entities as the pipeline meets them, which it will do constantly.

**This also deflates an earlier claim.** Three labs sitting inside listed issuers (SPCX, SEHK 2513,
SEHK 100) was written up as structurally special. It is convenient, not special — the mechanism
route was always the main road, and a direct ticker just removes one hop.

## Factor schema, revised by this pass

**Shared core** — `lab`, `people[] {name, role, function}`, `event_type`, `date`, `source_url`,
`quoted_span` *(must resolve inside article body — F1)*, `counterparties[] {name, entity_class, is_public,
ticker}` *(F3, F10 — attributes, never gates)*, `quantities {currency_amount, power_mw, duration, parameters, price_per_1m_in,
price_per_1m_out, benchmark_deltas[]}`.

**Investment last-mile** — `mechanism_ids[]`, `sign`, `magnitude`, `latency`, `affected_holdings[]`
via deterministic propagation.

**AI-team last-mile** — `technique_type`, `artifact_availability`, `licence` *(F5)*,
`reproducible`, `adopt_or_investigate`, `reason`.

**Changed by this pass:** `function` added to people (F7); `entity_class` added and `is_public`/`ticker` demoted from filters to attributes (F3, F10); `quoted_span` gains a body-boundary constraint (F1); `training_hardware` demoted to optional
(F9); **scoring becomes per-audience rather than single-score** (F6).

## Open items this pass created

1. **Body-boundary extraction and citation validation** — F1, blocking. The citation promise is
   unsound without it.
2. **Decide press as a primary source for `lab_compute_contract`** — F8. Affects cost and coverage.
3. **Route around the openai.com 403** — F9.
4. **Decide whether image-embedded numbers are in scope** — F9. Affects every model-release page.
5. **Per-audience scoring** — F6. Confirm before building the scorer.
6. **Widen `companies.yaml` to a three-class entity registry** — F10. Held / reference / private,
   with `peers_in_portfolio` promoted to a bidirectional relation. Blocking for propagation.
