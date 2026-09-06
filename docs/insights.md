# Insights

Real things the system surfaced, captured as they appeared. 3–5 go in the final doc.

---

**BIT did not hold Micron through the memory downturn — they rebuilt at the
trough.** The 13F shows 22 consecutive quarters of "holding", but through 2022 it
was 4,000 shares worth $0.2m (0.04% of the book). The real position was built in
Q1 2023 near the memory-cycle low and has been material since. In Q2 2026 they
cut shares 40% while the weight still rose — trimming into strength. That reads
as cycle discipline, not a structural AI holding, and it changes which
transmission mechanism they are actually trading (`ai_capex_investments`, not
`memory_intensity_up`).

**BIT's own audited report contains an entity-resolution failure.** Page 40 lists
ISIN `AU0000185993` twice — "Iris Energy Ltd." at 5.60% and "IREN Ltd." at 3.99%,
the same company either side of a rename. The ISIN is stable; the name is not.
A live argument for identity-by-identifier in our own register.

**Name turnover averages ~20% a quarter and hit 50.8% in Q2 2026.** Anything we
build against the disclosed book is describing a portfolio that has already
moved. Argues for mechanism-level output over name-level recommendations.

**BIT's biggest positions are pinned by regulation, not conviction.** Measured
against each fund's own NAV, all six funds sit just inside the UCITS 5/10/40
limits — largest position anywhere 10.43%, over-5% bucket peaking at 37.1%
against a 40% ceiling. The aggressive trimming of Micron and IREN into strength
reads as compliance rather than a view change. If so, the intelligence product's
job is not to rank conviction but to say which fund has headroom and what a
purchase displaces.

**The same analysis reverses sign depending on the window.** Over four quarters
BIT look like relentless sellers of their top names; over twelve they are net
accumulators of the same names — IREN shares +57%, Amazon +479%, TSMC +9,579%.
They build for years, then cut hard once a name goes vertical. Any scoring input
derived from position changes has to state its horizon or it is meaningless.

**Four frontier labs were invisible to the product until the ceiling replaced
the off-switch.** xAI, Google DeepMind, Mistral and Meta AI had their
announcements fetched months ago and sitting in the corpus — 61 articles — but
none were ever classified, because scoring was deactivated wholesale (D10) as a
guard against an uncontrolled LLM run. The register said seven labs; the
dashboard could only see three. Classifying them cost $1.08 and moved the
corpus from 175 scored articles to 236, mechanism tags from 101 to 141, and
holding connections from 612 to **809**. The lesson is not about the labs — it
is that a safety switch with no bounded alternative quietly becomes a coverage
gap that looks like a completed register.

**One xAI announcement fans out to seven holdings on a single sentence.**
"Grok 4.5 was trained across tens of thousands of NVIDIA GB300 GPUs" fires
`training_compute_up` at strength 0.67 against NVIDIA, TSMC, Micron, Marvell,
Amazon, IREN and TeraWulf simultaneously — the accelerator, the foundry, the
memory, the interconnect, the cloud and two power/datacenter names, each with
the same verbatim quote as evidence. That is the mapping layer doing exactly
what it was built for: a private lab's model release priced through to seven
public positions. It is also the clearest noise risk in the product — one
sentence producing seven digest rows — and the argument for grouping a digest by
*event* rather than by connection.

**The register found four possible researcher moves without being asked to.**
Building the people register surfaced four names appearing on papers at two
different labs inside the same three-month window — Jeffrey Wu (OpenAI and
Anthropic), Yonglong Tian (OpenAI and Google DeepMind), Jason Chen (OpenAI and
Meta AI), Yao Li (OpenAI and DeepSeek). Researcher departures are what the brief
calls top-tier signal, and nothing in the system produced them before. They fall
out of the register precisely *because* it refuses to merge people across labs:
had the design silently glued matching names together, each of these would have
become one tidy row and the signal would have been destroyed by the cleanup.
They are candidates, not confirmed moves — same name is not same person — but a
candidate list of four is a tractable thing to check by hand.

**The people register is 4,927 people and most of them do not work at the labs.**
Of 6,415 evidence rows, 1,996 are `unknown` — a GitHub account that touched a
lab's repo with no evidence of employment. `shoemoney` has one or two commits
across five different labs; `winklemad` the same. Drive-by open-source
contributors, not staff. Only 630 rows are `confirmed` (a commit from a
lab-owned email domain) and 583 `confirmed_org_wide` (a parent-company domain
like @google.com, which covers all of Alphabet). The lesson for the design doc:
a raw contributor count is nearly meaningless as a measure of a lab, and any
figure quoted has to carry its evidence tier or it is just repo popularity.


---

**Four model names have appeared in the labs' own public code before any
announcement, across three labs.** Running the first-mention pass over 630
documents in bronze -- 250 announcements and 380 release notes -- turns up 39
identifiers appearing for the first time in 120 days. Thirty-five were first
seen in the lab's own announcement, which is an ordinary launch. Four were
first seen in shipped code with nothing written about them anywhere:

- `gpt-6-astra` — *"Added support for configuring GPT-6-Astra through the API
  without changing the default model or showing it in the model picker"*,
  `openai/codex` rust-v0.153.1, 2026-09-03
- `gpt-5.6-luna` — became the **default model** of the OpenAI Agents SDK,
  2026-08-11
- `gemma-4` — *"Add Gemma 4."*, `google-deepmind/gemma` v4.0.0, **2026-05-13**
- `gemma-3` — in a DeepMind fine-tuning example, 2026-07-14

The `gemma-4` case is the strongest of the four on timing: a version bump in a
public repository nearly four months before anything in the announcements
corpus names it. And the distinction between the four and the other
thirty-five is not something the system was told to look for -- it falls out of
recording which corpus reached a name first, which is only possible because
every leg's documents land in one table.

---

## The efficiency claim that only exists in the paper

DeepSeek-V4's abstract, scored 100.0 on the investment axis and quoted verbatim
by the register:

> "In the one-million-token context setting, DeepSeek-V4-Pro requires only 27%
> of single-token inference FLOPs and **10% of KV cache** compared with
> DeepSeek-V3.2."

KV cache is HBM-resident, so a tenfold reduction per served token is a
first-order claim about memory demand at long context — the transmission the
brief names as its calibration case, stated in numbers rather than inferred.
The system routes it to NVIDIA, Micron, TSMC, Amazon, TeraWulf and IREN at
strength 1.00: memory and the energy complex, which is what that claim argues
about. DeepSeek-V3's technical report routes **negative** to the same names.

**The announcements leg cannot reach this.** The figures are in the technical
report and nowhere else, which is the whole case for scoring papers at all
(D57) — and the reverse of the assumption we started from, that research would
be interesting to engineers and not to investors.

The corpus splits by *document type*, not by lab and not by research-versus-
announcement:

| type | n | investment | AI team |
|---|---|---|---|
| Flagship technical reports (DeepSeek) | 8 | strongest evidence in the register | high |
| System / model cards (OpenAI) | 7 | high, but duplicative of the launch | low |
| Alignment & interpretability (Anthropic) | 12 | **0.0, all twelve** | up to 44.4 |
| Safety / social science (DeepMind) | 15 | 0.0 | 0.0 |
| Component research (Meta) | 5 | ~0 | low–medium |

DeepSeek-R1 (2025-01-22) scores **100.0 / 100.0** — the system recovering the
brief's own calibration case from a paper abstract, unprompted.

The counterweight matters as much as the finding. The largest single group is
DeepMind's safety and social-science work, and *"A moral Turing test"* and
*"Artificial Minds, Human Disagreement: The Politics of AI Consciousness"* both
score 0.0 on both axes. 33 of 47 papers score zero for investors. That is the
result, not a shortfall: if those papers scored, they would bury the eight that
matter.

---

**Meta's most investment-relevant AI news is not on its AI blog, and the four
highest-scoring items in the whole register came from a feed we were not
reading.** D66 added `about.fb.com/news/tag/ai/feed/`. Four items band `high` at
a saturated 100.0, all of them compute buildout, none present on
`ai.meta.com/blog` in any form:

| date | item | what the scorer quoted |
|---|---|---|
| 2026-07-28 | BlackRock joint venture, El Paso | 1 GW campus, "$14 billion in total development costs", funded partly by a "$12.5 billion debt financing" |
| 2026-07-13 | Louisiana expansion | "more than $50 billion in the Richland Parish region", to 5 GW, and an Entergy agreement funding "seven new natural gas-fueled generating plants, three grid-scale batteries, nuclear uprates" |
| 2026-07-08 | First data centre in Canada | new-region buildout |
| 2026-06-10 | Reliance joint venture, India | AI-enabled data centre |

The El Paso item is the one to look at twice. `lab_capital_access` fired on it
because Meta is funding the buildout through an external partner and debt rather
than its own balance sheet — a *financing-structure* change, not a spending
number, and the kind of thing a capex headline usually buries. That is the
transmission mechanism BIT is actually trading (`ai_capex_investments`, per the
Micron finding above), sourced to a primary document.

Worth stating honestly: four items at exactly 100.0 is the ceiling being hit,
not four things being equally important. The scale stops discriminating at the
top, and a fund reading this wants El Paso and Richland Parish ranked against
each other, not tied. That is a scoring-calibration gap, not a data one.

**The register said Google DeepMind shipped nothing but outreach for three
months.** It was reading a sitemap that does not enumerate the blog (D66), and
what that sitemap carried was the education, policy and programme posts while
omitting Gemini 3.6, 3.7 and 3.8 Flash, Gemma 4 12B and DiffusionGemma. The
single most forward-looking sentence recovered by the fix is buried in the
3.6 Flash post, in a paragraph about something else:

> We have started our most ambitious pre-training run yet, for Gemini 4, and are
> excited by the progress.

A lab disclosing that its next flagship pre-training run is underway is a
`training_compute_up` signal with a date on it. The system had no idea, and the
failure that hid it reported success on every run.

The counterweight, again: those launches mostly band `low` (Gemini 3.6 Flash at
26.7, 3.5 Flash Cyber at 13.3) while the data-centre posts band `high`. An
incremental Flash release genuinely is not a portfolio event and the compute
commitments genuinely are — the scorer preferring capex to model numbering is
the product working, not a mis-weighting.
