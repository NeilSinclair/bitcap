# Decisions

Decision · alternatives rejected · rationale · consequence. Newest last.

---

## D1 — Price data comes from a vendor only once the filing itself agrees

**Decision.** `config/tickers.yaml` hand-maps CUSIP→ticker. No mapping is used
until the vendor's daily close on each 13F quarter-end matches the price the
filing implies (`value_usd / shares`) to within 2%. A ticker that fails is
dropped and the position falls back to its implied-price series.

**Alternatives rejected.**
- *A licensed security master.* Correct answer, not available for a take-home.
- *Trust the hand map.* A wrong ticker draws a confident price line for the wrong
  company — indistinguishable from a right one on screen. Entity-resolution
  collision is a named failure mode; this is one.
- *Fuzzy-match issuer names.* Same failure mode, less transparent.

**Rationale.** The 13F is its own oracle. Value ÷ shares is the quarter-end price
by construction, so the filing can verify an external identifier without any
third-party reference data.

**Consequence.** 44 of 82 current positions carry a vendor price line; the rest
render quarter-end implied prices, labelled as such. A mapping error produces a
*missing* price line, never a wrong one. Splits are detected (vendor closes are
retroactively adjusted, filings are not) and excluded from the check, then
disclosed in the UI because the price line is rebased where the share counts
beside it are not.

## D2 — Config identifiers are validated on load, not trusted

**Decision.** `load_tickers()` rejects any key that is not a 9-character CUSIP.

**Rationale.** Unquoted, YAML reads an all-digit CUSIP as an integer and a
leading-zero one as **octal**: `023135106` silently became `5028422`. Sixteen
identifiers were corrupted, and the only symptom was a missing price line —
found by noticing Micron had fallen back to implied prices, not by any error.

**Consequence.** Quoting the keys fixed it; the loader check is what stops it
recurring. Generalises to the whole project: "config, not code" only holds if
config is validated as strictly as code. Every config file we add gets a loader
that fails loudly.

## D3 — Holding streaks are measured at a materiality floor

**Decision.** `survival()` reports `quarters_material` (≥0.5% of the book)
alongside raw `quarters_held`.

**Rationale.** Raw presence counted a 4,000-share Micron stub (0.04%) the same as
a $200m position, producing a "22-quarter holding" that read as conviction.
Robinhood was starker: 10 quarters present, 1 material.

**Consequence.** Caught by Neil questioning the number, not by a test — the
pipeline's main risk is exactly this kind of plausible-looking wrong answer.
Any scoring input derived from holding duration must use the material measure.

## D4 — Concentration is measured against fund NAV, never against the 13F

**Decision.** Any claim about position limits uses `pct_of_fund` from the
statutory portfolios. The 13F weight is share of a merged, all-vehicle book and
is not a NAV percentage.

**Rationale.** The "weight cap" found in the 13F (nothing sustained above ~15%)
was measured against the wrong denominator. Per fund, every one of the six sits
inside the UCITS 5/10/40 pattern: largest single position 10.43%, and the
over-5% bucket peaks at 37.1% against a 40% ceiling. Neil reports BIT's own
documents require justification above 5% of NAV.

**Consequence.** The cap is probably regulatory, not discretionary — which
changes the product. A signal that says "increase IREN" is unactionable when
three funds already hold it at 7.9–9.7% of NAV. Useful output is *which fund has
headroom* and *what funds the purchase*, not a bare conviction score. The exact
policy wording is **not sourced** — neither prospectus nor KID is in the
collected document set — so the measurement stands and the attribution is
flagged unverified.

## D5 — A missing price raises rather than defaults

**Decision.** `counterfactual()` raises when any quarter in the window lacks a
price, instead of substituting zero.

**Rationale.** `price(...) or 0` valued a sale at nothing. Across the book that
produced 40 positions reporting an "untraded" value of $0m and a confident,
wrong delta. Nothing errored.

**Consequence.** Generalises: in this pipeline a missing value must never be
coerced to a neutral-looking default. The same rule applies to LLM extractions —
a null field is undefined, not zero, and must fail loudly.

## D6 — Pin the source PDFs by hash rather than re-fetching them

**Decision.** `research/docs/manifest.json` pins every fund PDF by SHA-256,
retrieval date and as-of date. `verify_docs.py` checks it, and all three
parsers call `check()` before reading a single byte. The PDFs stay committed.

**Alternative rejected.** Gitignore the 29 MB and re-download at setup time,
which would have fitted "clone-to-running in a few commands" better.
`sources.json` records that *both* sources serve rolling URLs: the anevis
factsheet `_ultimo` links and the HANSAINVEST `.../document/{jb|hjb}/de/de`
endpoints each return whatever edition is current. A fetch script would pull
different documents into the same filenames, and `parse_portfolios.py` would
reconcile them to within €1 and emit a different portfolio without erroring.
Silent data substitution is worse than a heavy repo.

**Alternative rejected.** Git LFS. Solves repo weight, which is not the actual
risk, and adds a `git lfs install` prerequisite to the setup path. Revisit past
~100 MB.

**Consequence.** The PDFs cannot be reproduced, so the hash is the only proof
of which edition an insight was extracted from — it's what makes a citation
resolvable to a specific document rather than to a URL. A deliberate refresh is
`verify_docs.py --rebuild`, which forces the new edition into a reviewable diff
instead of letting it land silently. A full check also fails on any PDF in
`docs/funds` with no entry in `sources.json`, so undeclared files can't
accumulate.

The first `--rebuild` pinned 19 of the 21 PDFs then, which is how
`GTL_annual_report.pdf` and `GTL_semiannual_report.pdf` surfaced as
byte-identical duplicates of `jb_GTL.pdf` and `hjb_GTL.pdf` — two filenames for
one document, the same entity-resolution hazard the pipeline exists to catch.
Removed, with `parse_annual_report.py` repointed at the canonical name; all
generated JSON is byte-identical afterwards. Git had stored each pair as a
single blob, so this cost nothing in repo weight and was never about size.

## D7 — Register admission is decided by transmission path, not by lab prominence

**Amended 2026-09-02 — see D14.** Meta AI added as a seventh deep-coverage lab. The
"six labs" language below is the original decision as made; it is superseded, not
rewritten, so the reasoning that produced it stays legible.

**Decision.** A lab earns a register slot only if a path can be written from its
publications to a BIT position through a defined mechanism. Six labs get deep,
person-level coverage first: **Anthropic, OpenAI, Google DeepMind, DeepSeek,
Mistral, xAI/SpaceXAI**. The remaining 15 drop to a watchlist — org-channel
ingestion only, high alerting threshold, no person-level tracking. Nothing is
deleted.

**Alternatives rejected.**
- *A who's-who of frontier labs.* This is what the first register draft actually
  was, and it admitted Ai2 on the strength of "best test corpus" — an
  engineering justification smuggled in under a market-impact heading. Ai2 is a
  non-profit publishing research-tier models; no BIT holding moves on an OLMo
  release. Neil caught it.
- *Breadth over depth.* The brief says the opposite, and person-level coverage
  is the expensive axis. 21 labs deep is not affordable; 6 is.

**Rationale — the evidence changed the ordering.** The selection was made against
verified market events rather than reputation:

| Date | Event | Effect |
| --- | --- | --- |
| 2025-01-27 | DeepSeek-R1 | NVDA −17%, −$593bn, largest single-day loss in US history |
| 2025-09 | OpenAI–Oracle $300bn | ORCL +36% in a day, best since 1992 |
| 2025-11-25 | Report Meta may adopt Google TPUs | NVDA −4.3% close, ~$243bn erased |
| 2026-04-10 | Anthropic–CoreWeave | CRWV +11% |
| 2026-08-11 | Anthropic–Riot $9bn / 191MW | Riot is a BIT holding |
| 2026-08-26 | Anthropic–Nscale $45bn / 460MW | — |
| 2026-04-28 | OpenAI missed revenue targets | Oracle and chip stocks fell — signal is bidirectional |

**The finding that matters: the repeatable market-moving lab event is the compute
contract, not the model release.** DeepSeek is the largest such event and also
the only one of its kind in 18 months. Anthropic alone moved five named
counterparties in 2026. A compute contract names a public company, a dollar
figure, a megawatt figure and a date — it needs no interpretation to reach a
ticker. A model release does. Ranking labs by benchmark leadership would have
produced a materially different and worse list.

**Consequence.**
- `lab_compute_contract` moves from "proposed mechanism" to the highest-value
  extraction target in the system.
- Person-level depth follows the six, and must cover **dealmakers, not only
  research leads** — a publications-driven register would miss the people who
  sign compute agreements.
- Mistral is in on the sovereign-EU thesis. An earlier assessment called it
  marginal by anchoring on ASML at 0.08% of book; the correct figure is **7.77%
  European semiconductor exposure** (Infineon 4.05%, Nokia 1.97%, BE
  Semiconductor 1.63%, ASML 0.08%). The error was measuring one name instead of
  the exposure.
- **Known gap: the six were selected on investment impact alone.** See D8.
- The event table above is reusable as the stratum-A fixture set for the
  extraction backwards-test.

## D8 — Scope is BIT Global Technology Leaders only, for now

**Decision.** The system analyses lab-signal impact on **Technology Leaders alone** —
26 positions as of 30.06.2026 — not the six-fund combined book of 125 positions.
Broadening is a later step, not a cancelled one.

**Rationale.** Neil's call: Technology Leaders is roughly 50% of BIT's AUM, so it is
the single most representative fund, and a narrow scope keeps the build focused while
the design is still moving.

**Consequence — a data-shape trap this closes.** Each BIT fund reports on a *different*
`as_of` date:

| Fund | as_of | Value |
| --- | --- | --- |
| Crypto Leaders | 28.02.2026 | €158.2m |
| Multi Asset | 31.03.2026 | €19.9m |
| Aggressive Growth | 30.04.2026 | €63.3m |
| Global Leaders | 30.04.2026 | €103.6m |
| Defensive Growth | 31.05.2026 | €38.5m |
| **Technology Leaders** | **30.06.2026** | **€1,507.3m** |

There is therefore **no single combined snapshot of "the book"**. Technology Leaders is
80% of the summed value purely because it is the largest fund reporting on the latest
date. Any combined "% of book" figure blends six dates and is close to meaningless.
Scoping to one fund removes the trap rather than working around it.

**Consequence — figures are restated.** Everything computed across all funds is
superseded. `memory_storage` was 16.22% combined; it is **19.08% of Technology Leaders**.

**Class breakdown, Technology Leaders @ 30.06.2026** — 26 positions, €1,507m,
100% classified, no residual:

| Class | Weight | n |
| --- | --- | --- |
| memory_storage | 19.08% | 3 |
| hyperscale_platform | 11.03% | 1 |
| health_insurance | 10.37% | 3 |
| compute_hosts_miners | 10.15% | 2 |
| fintech_consumer_fin | 8.07% | 2 |
| accelerator_custom_si | 7.61% | 2 |
| consumer_marketplace | 7.52% | 2 |
| foundry_logic | 7.39% | 2 |
| materials_mining | 5.48% | 3 |
| analog_power_semi | 5.34% | 2 |
| networking_optical | 5.12% | 2 |
| semicap_equipment | 2.02% | 1 |
| digital_assets | 0.82% | 1 |

**Note on the trajectory claim.** `research/human_research_notes.md` records memory and
storage moving from ~10% of book at end-2025 to ~29% by mid-2026. The direction is
strongly confirmed — Technology Leaders went from **5.21%** (Micron alone) at
31.12.2025 to **19.08%** (three names) at 30.06.2026, a 3.7x increase, with SanDisk and
Silicon Motion added exactly as the note describes. The **specific percentages do not
reproduce**: narrow gives 5.21%->19.08%, wide (incl. Marvell/Astera/Credo) gives
5.21%->24.16%, widest (incl. foundry/analog/Nvidia) gives 16.47%->38.22%. The 10%/29%
figures sit between definitions at both ends and are **unsourced**. Treat as
directionally right, numerically unverified.

**Consequence for the class layer.** "Memory and storage" ranges from 5% to 16% at
end-2025 purely on where the boundary is drawn — a 3x swing from a definitional choice.
**Every class figure the system emits must carry its boundary definition**, or it will
disagree with BIT's own reporting and read as broken.

## D9 — AI compute demand and crypto demand are separate categories, and categories may overlap

**Decision.** Split the former `compute_hosts_miners` class into two:

- **`ai_compute_hosting`** — demand for datacentre compute driven by AI. 10.15% of
  Technology Leaders (IREN, TeraWulf).
- **`crypto`** — mining economics and crypto adoption generally. 10.97% (IREN,
  TeraWulf, Hyperliquid Strategies).

**IREN and TeraWulf sit in both.** They are single companies with two independent
demand drivers, so **10.15% of the fund is dual-driver**.

**Rationale.** IREN began as a bitcoin miner (formerly Iris Energy) and moved into AI
compute hosting; TeraWulf straddles the same line. One class covering both would fire
on two unrelated stimuli: "AI compute demand surges" and "bitcoin hashprice collapses"
are both real signals for IREN, but only the first has anything to do with a frontier
lab. Merging them would have made the category useless for attribution — a hit could
not be explained.

Neil's call to keep `crypto` despite expecting it to stay empty: if no lab signal ever
routes to it, that is itself a finding, and the category is cheap to delete. Keeping it
also means a crypto-driven move in IREN is *explicitly out of scope* rather than
silently misattributed to an AI signal.

**Consequence — categories are no longer a partition.** Class weights now sum to more
than 100%, and any output stating "X% of the fund" must say which category, not imply
exclusivity. This was previously an unstated assumption; it is now an explicit property
of the model.

**Consequence — a company may be hit for reasons unrelated to its main class.**
Robinhood (5.29%, `fintech_consumer_fin`) carries material crypto revenue. It is
**flagged, not reclassified** — a secondary-exposure annotation rather than dual
membership, because unlike IREN its crypto exposure is a revenue line rather than its
operating business.

**Consequence for the register.** Frontier-lab signals should route to
`ai_compute_hosting` and never to `crypto`. If the pipeline routes a lab signal to
`crypto`, that is a bug worth alerting on, not a finding.

---

## Person register: derive it from bylines, deterministically (2026-08-30)

**Decision.** Build the person half of the register by parsing author lists off the
labs' own publications, and parse them with code rather than an LLM. Proved at n=1 on
Anthropic over a rolling 3-month window: `research/harvest_contributors.py` +
`research/byline.py`, tested in `tests/test_byline.py`.

**Alternatives rejected.** (a) LinkedIn or a headcount vendor — ToS-hostile, gated, and
the field it returns (job title) is the weakest available predictor of who produces
signal. (b) An LLM extracting authors from page text — a byline is structured markup;
an LLM here buys nothing, costs per page, and fails silently in a way that returns a
plausible-looking list.

**Why deterministic won on the evidence.** The parser broke four times against the live
pages, and every break was a *silent empty return*, not an error: a `<br>` desynchronising
the tag stack, a superscript placed after the comma handing one author's affiliation to
the next, a line-wrapped name splitting into two people, and multi-paragraph bylines
truncating at the first line. An LLM would have papered over all four and produced a
confident wrong register. Each is now a test.

**The finding that justifies the module.** `*` is overloaded across Anthropic's *own*
two channels: "Core contributor", "Core Research Contributor", "Equal contribution,
author order alphabetical", and on the alignment blog the Anthropic Fellows Program.
Reading `*` as seniority would invent a signal that is not there, and on an alphabetical
byline author *position* carries no information at all. The parser resolves the symbol
against each page's own footnote and records `star_means` / `order_meaningful`; scoring
must not use position where the lab says order is alphabetical.

**Consequence — the register's people are not a roster.** It records who signs research
output, which is the population that matters for both watchlist and ingestion purposes.
It will never contain non-publishing staff, and that gap must be stated rather than
hidden.

**Consequence — affiliation is an interval, not an attribute.** 18 of 87 people in the
window carry a non-Anthropic byline (AE Studio, Redwood Research, MATS, EPFL, UK AISI,
Theorem) and 8 more are Anthropic Fellows. "Anthropic researcher says X" is wrong for a
quarter of this list. The schema stores affiliation per byline, dated.

**Open.** One in-window article ships no machine-readable byline at all
(`automated-alignment-researchers`, names in an unmarked italic paragraph). 1 of 17 is
tolerable; the harvest reports it rather than silently dropping it, and if that rate
rises an LLM fallback on the no-byline path becomes the right call.

---

## LLM vs deterministic byline extraction: measured, not argued (2026-08-30)

**What was run.** `claude-sonnet-5` with `prompts/byline_extraction/v1.md` over the same
17 cached Anthropic articles the deterministic parser handles, compared by
`research/eval_byline.py`. 17 calls, 370,210 in / 12,127 out, **$0.86**. The prompt was
written before seeing any result and deliberately not tuned to the parser's known
failure modes — tuning it would have measured the prompt, not the approach.

**Headline.** Names: precision 0.974, recall 0.982, F1 0.978; 14 of 17 bylines matched
exactly, in order. After adjudicating every disagreement by hand against the pages,
**the LLM's name extraction was correct on all 17 articles** — 114 of 114 authors.

**The eval found more bugs in the deterministic parser than in the LLM.** Three, all
silent:
1. A trailing superscript in a comma-split chunk was assigned to the previous author,
   giving Robert Kirk a fabricated second affiliation (`Anthropic` + `UK AISI`).
2. `star_means` was inferred from an affiliation legend on pages that never print a
   `*` at all — six articles carried an invented "fellows" convention.
3. One article's byline was missed entirely; the LLM read it correctly. The three names
   the eval scored as LLM hallucinations were real, and the reference was wrong.

All three are now regression-tested. Affiliation accuracy went 0.991 → 1.000 and
`star_means` accuracy 0.647 → 1.000 on the reference side as a result.

**Where the LLM is genuinely unsafe, and it is the reason not to adopt it wholesale.**
It invented `is_fellow: true` for four people on two pages that state no affiliation
whatsoever — Kevin Der, Harish Kamath and Ben Thompson on a Circuits Update, and Alex
Serrano, whose page explicitly attributes him to MATS. Nothing on those pages supports
the claim. It also read the NLA paper as having a meaningful author order when the page
states "author order alphabetical". **Every LLM error was an assertion about employment
or credit that the source does not make** — precisely the class of error this project
cannot ship, and precisely the class that would never surface without an eval.

**Not counted against the model.** Four date mismatches are pages that print no date;
the reference took those from the index, which the model never saw. Two "missed" names
are people credited as *editors* of a Circuits Update — the model excluded them as
non-authors, which the prompt's wording supports. That is a spec ambiguity to settle,
not a defect: the register must decide whether an editor is a contributor.

**Decision — unchanged, now on evidence.** Deterministic parsing stays primary on
channels with known markup: it is free, instant, exactly reproducible, and its errors
are visible. The LLM is adopted for the two roles argued for earlier: the no-byline
fallback (where it beat the parser outright) and onboarding channels whose markup is
unknown. Any LLM-extracted affiliation or employment claim must be marked as
model-asserted and excluded from attribution until a page states it.

**Consequence — the eval harness is the deliverable, not the verdict.** The 17 pages are
now a labelled gold set (`research/docs/anthropic_contributors.json`), and
`research/eval_byline.py` scores any extractor against it. The next lab's channel gets
measured before it is trusted, rather than argued about.

**Forward procedure.** The per-lab onboarding steps this implies are written up in
[planning.md](planning.md) §11, gated on validating the approach against a second lab
before it becomes standard.

**Consequence — the reference is not ground truth.** It was wrong three times here. The
eval prints every disagreement rather than averaging them, because the disagreements are
where both sides' defects live.

---

## Person deep-dives are restricted to direct lab employees (2026-08-30)

> **Scope note added 2026-08-31.** This decision governs people found *through
> publications*. It says nothing about non-publishing leadership, who are missing from
> the register entirely — see [planning.md](planning.md) §12 for the proposed leadership
> register.


**Decision — Neil's call, hard rule.** Only people **employed by a frontier lab** get
enriched: personal channels found, GitHub and X resolved, activity tracked, scored.
Co-authors from other organisations are **recorded but not researched**.

The two halves are deliberately different:

- **Extraction records everyone.** `anthropic_contributors.json` keeps every byline as
  published, including Redwood Research, AE Studio, MATS, UK AISI, EPFL and Anthropic
  Fellows, with per-article affiliations. Throwing them away at parse time would lose
  the co-authorship graph and the affiliation history that makes drift detectable later.
- **Enrichment covers staff only.** The expensive per-person work stops at the lab
  boundary.

**Rationale.** The assumption is **overlap**: a lab employee co-authoring with an
external will themselves publish and discuss that research, so the finding reaches us
through the staff member's channel anyway. Following both sides buys a second path to
the same signal at roughly double the per-paper cost. Extraction volume has to be
bounded somewhere, and this is the boundary with the clearest justification.

**Supporting evidence (n=17, Anthropic, 3 months).** Every article in the window has at
least one Anthropic staff author, so a staff-only enrichment scope loses **no article
coverage at all**. No non-staff person appears more than once — all 17 repeat
contributors are staff — while 28 externals and fellows appear exactly once each. AE
Studio contributed 8 people via a single paper.

**Alternatives rejected.**
- *Enrich everyone on the byline.* Roughly doubles the person register for people who,
  on this evidence, do not recur.
- *Promote recurring collaborator orgs (Redwood: 6 people, 3 articles) to first-class
  register entities now.* Defensible, and the cleaner long-term shape, but it widens
  scope before the core register exists. Deferred, not dismissed.

**Consequence — a known, stated gap.** Research that an external co-author publishes
**without** a lab employee is invisible to us. That is a real hole and goes in the
design doc as one, not as an oversight.

**Consequence — the assumption is untested.** "Staff will surface it anyway" is a
hypothesis, not a finding. It is cheaply testable once channels are ingested: take
papers with external co-authors and check whether the staff author's own channel
actually mentions them. If it usually does not, this decision needs revisiting.

**Caveat on the evidence.** Three months is too short to prove non-recurrence — anyone
publishing twice a year cannot recur in the window. Widening to nine months
(`--months 9`, same parser and cache) is the cheap check, and is parked for now.

**Later step, explicitly deferred.** Extending enrichment to frequent collaborators, and
promoting recurring collaborator organisations to their own register entries. Not part
of this build.

### Amendment, same day — lab profiles carry an external-organisation register

**Revision.** The lab profile gains one section: **the outside organisations the lab
co-publishes with**. Organisations only, never their people. It is a future watchlist,
not something acted on now.

The rule above is unchanged and still hard: no deep dives into external individuals, for
the overlap and volume reasons already recorded. This adds an org-level pointer, which
is a different and much cheaper object than a person register.

**Why it is worth having.** It answers "who is this lab's research actually entangled
with" — a question the person register cannot answer, because individual externals never
recur while their organisations do. It also gives the register a principled way to grow
later: an org that keeps reappearing is a candidate register entity on its own merits,
and this is the evidence trail for promoting it.

**Cost: nil.** It falls out of the byline affiliations already parsed.
`collaborator_orgs()` in `research/harvest_contributors.py` emits it into
`anthropic_contributors.json`.

**Anthropic, 3-month window:**

| Organisation | Articles | People |
| --- | --- | --- |
| Anthropic Fellows Program | 5 | 8 |
| Redwood Research | 3 | 6 |
| MATS | 2 | 3 |
| AE Studio | 1 | 8 |
| EPFL, Theorem, UK AISI, Independent | 1 each | 1–2 each |

**Reading it.** Article count matters more than people count. Redwood at 3 articles is a
standing research relationship; AE Studio's 8 people on one paper is a single
collaboration. A person register would have ranked AE Studio highest — the org view
inverts that, correctly. The Fellows Program is a pipeline rather than a peer
organisation and is flagged `is_fellowship` so it is never mistaken for one.


---

## §11 gate: DeepSeek validates the approach, and changes one conclusion (2026-08-30)

**The gate** set in [planning.md](planning.md) §11 was to take a second lab through steps
1–2 before adopting the per-lab onboarding procedure. Done: DeepSeek, 8 papers,
415 people, `research/deepseek_harvest.py` + `research/eval_deepseek.py`.

**Result: the approach holds, and the LLM did better here than on Anthropic.**
Precision **1.000**, recall **0.991**, F1 **0.9955** over 780 authors, and **zero
inventions**. Adjudicated, recall is effectively 1.000: all six "misses" on R1 were
names present in arXiv metadata but absent from the paper's own author-list appendix,
which is the only thing the model was shown. Departure flags matched exactly on all
three papers (5/5, 8/8, 10/10). One role disagreement in 780.

**So the ~0.98 on Anthropic was not flattery from clean markup.** That was the open
question; it is answered.

**But DeepSeek breaks the assumption underneath the procedure.** The bottleneck at
Anthropic was *parsing* bylines. At DeepSeek parsing is trivial — arXiv publishes
`citation_author` metadata — and the hard part moved to two places the Anthropic
experience did not predict:

1. **Corpus discovery.** DeepSeek files under a collective byline, `DeepSeek-AI`, so an
   author query finds 7 papers. DeepSeekMath-V2 is a real DeepSeek paper filed under
   individual names and is invisible to that query; it was found by title, by hand. There
   is no index page to enumerate. **Knowing which papers exist is now the unsolved step**,
   and it is not an extraction problem.
2. **Harness geometry.** The author list is an appendix, and V4 places another appendix
   after it. A fixed tail slice missed the section on 2 of 3 papers and the model
   correctly reported none — an error that scored as a catastrophic model failure
   (recall 0.32) until adjudicated. **Where the target sits in the document is a
   per-lab property, and getting it wrong looks exactly like a model failure.**

**Finding that outranks the eval: DeepSeek prints its own departures.** The author list
marks names with an asterisk denoting "individuals who have departed from our team" —
5 on R1, 8 on V3.2, 10 on V4, 20 distinct people. No other lab in the register publishes
this. Researcher departures are named in the brief as top-tier signal, and here they are
stated by the lab, dated by paper, and machine-readable. **This should be a first-class
extraction target, not a byline attribute.**

**Second structural finding: author order is meaningless.** The papers state authors are
listed alphabetically by first name. Any first/last-author scoring is invalid for
DeepSeek, and `order_meaningful` is hard-coded false. The same trap appeared on
Anthropic's NLA paper; it is now confirmed as a cross-lab hazard rather than a one-off.

**Consequence — §11 is adopted, with a step inserted.** The procedure gains **step 0:
establish how the lab's corpus is enumerated, and where in a document the byline sits.**
On Anthropic both were free. On DeepSeek both took longer than the extraction did.

**Consequence — the deterministic-primary rule stands but matters less here.** With 8
papers a year, LLM extraction of the whole corpus costs $0.16/paper. The argument for
deterministic parsing at DeepSeek is reproducibility and visible failure, not cost.

---

## DeepSeek register: follow 15 core contributors, count the rest (2026-08-31)

**Decision — Neil's call.** For DeepSeek, personal-channel research is limited to the
**core contributors still present and not marked departed**: 15 people. The remaining
~400 authors are a **roster** — counted, never individually researched.

**Why frequency could not be the cut.** DeepSeek-V4 has 328 authors. Appearing on it
means you were employed that release cycle, not that you did notable work, so appearance
count measures *tenure*, not importance — 48 people sit on six papers each. The lab's own
"Core Contributors" heading is the only importance ranking available, and it is theirs
rather than ours.

**The 15.** Shirong Ma, Zhihong Shao (8 papers); Dejian Yang, Junxiao Song, Peiyi Wang,
Qihao Zhu, Xiao Bi (7); Runxin Xu, Xingkai Yu, Zhibin Gou (6); Xiaokang Zhang, Zhuoshu Li
(5); Ruoyu Zhang, Yu Wu, Ziyi Gao (4). All appear on V4 (Apr 2026).

**Excluded from the 18:** Daya Guo and Haowei Zhang (marked departed), Z. F. Wu (absent
from V4). These move to the departure watchlist rather than the research list.

**Stated weaknesses — this is a triage rule, not a truth claim.**

1. **The ranking is 19 months old.** "Core Contributors" appears only on DeepSeek-R1
   (Jan 2025); V3.2 and V4 dropped the heading. It identifies people central in early
   2025 who are *still employed* — a proxy for continued seniority, not a measurement of
   it. It cannot see anyone who became central after R1. DeepSeek publishes roughly
   annually, so no fresher ranking exists.
2. **Preliminary finding: these people may publish nothing to follow.** A quick check by
   Neil found little contemporary personal output — GitHub accounts carrying DeepSeek
   repositories or pre-DeepSeek research, and no personal blogs located. Not an
   exhaustive search. If it holds, the honest conclusion is that DeepSeek individuals are
   not a viable ingestion source and the lab is trackable only through its papers,
   releases and roster — which is a finding about the lab, not a gap in the method.

**Rationale.** The purpose is efficiency: bounding whose channels are worth hunting for
while we establish whether any DeepSeek individual publishes anything worth ingesting.
Fifteen names is a cheap question to answer; four hundred is not.

**Consequence — the roster keeps its value without enrichment.** Author counts across
releases (197 → 264 → 328) give headcount growth, and V3.2 → V4 gives 215 retained,
113 new, 49 absent. For an investment audience that aggregate is more useful than any
individual profile, and it costs nothing.

**Consequence — implied departures stay labelled as inferred.** Only 5 of those 49
absences are marked by the lab. Absence from one paper is not a departure; the other 44
are a weak signal and must never be asserted as personnel events.

---

## Anthropic enrichment shortlist: >=6 papers OR core-marked — EXPERIMENT (2026-08-31)

**Status: trial, not settled.** Neil's call for a first pass. The point is to see how rich
the results are before committing to a rule; the threshold is expected to move.

**Rule.** From the 117 people carrying an Anthropic staff byline in the 12-month window,
enrich those with **>=6 papers OR any core-contributor marking**. Yields **17 people**.

**Why two criteria rather than one.** They are blind in opposite directions, because they
come from different channels.

- *Frequency alone* misses core contributors on few but central papers — Tom Conerly and
  R. Luger have one paper each.
- *Core marking alone* misses the top three by output entirely: Samuel Marks (13), Rowan
  Wang (12) and Fabien Roger (11) are not core-marked, because **only
  transformer-circuits uses that marker**. Selecting on it alone yields an
  interpretability-only register and drops the whole alignment side.

`last_author >= 2` was tested and dropped: all 9 such people already qualify, so it adds
a criterion that does no work.

**The 17.** Samuel Marks 13, Rowan Wang 12, Fabien Roger 11, Joshua Batson 9, Joe Benton 9,
Samuel R. Bowman 9, Henry Sleight 8, Jack Lindsey 6, Wes Gurnee 6, Isaac Kauvar 6,
Harish Kamath 4, Emmanuel Ameisen 3, Nicholas Sofroniew 2, Runjin Chen 2, R. Luger 1,
Tom Conerly 1, William Saunders 1.

**One entry needs a decision: Henry Sleight.** 8 papers, but **7 under Constellation and
only 1 under Anthropic**. He passes the staff-byline filter on a single byline. Either the
filter is too loose (one staff byline should not admit a mostly-external person), or he is
a genuine case of the affiliation drift the register exists to catch. Flagged rather than
silently kept or dropped.

**Known weakness.** Over a 12-month window the threshold conflates productivity with
tenure: someone who joined in March cannot reach six papers. It matters less here than at
DeepSeek, because Anthropic's author lists are small enough that authorship implies real
involvement, but it biases against recent hires — who are exactly the population a
fellow-to-staff conversion signal would surface.

**What would change the rule.** If most of the 17 turn out to have thin personal channels
(the DeepSeek outcome), the threshold is not the problem and per-person enrichment is the
wrong instrument for this lab too. If they are rich, widening to >=5 or core (22 people)
is the obvious next step.

---

## OpenAI: the third lab, where the specification is the bottleneck (2026-08-31)

**Run.** 7 papers, 1,079 people. `research/openai_harvest.py`, evaluated by
`research/eval_openai.py` on the 3 papers carrying a role-structured credit section.
$2.39 across three runs, two discarded.

**Headline numbers.** Precision 0.843, recall 0.918, F1 0.879 over 1,014 authors —
visibly worse than Anthropic (0.978) or DeepSeek (0.996). **Adjudicated, the LLM
hallucinated nothing.** All 174 "invented" names are real people printed in the papers
under a **Red Teamers** heading. The 83 "missed" are the same population on a different
paper.

**The finding: "contributor" has no consistent meaning at OpenAI.** Their cards run
core contributors, product and go-to-market staff, and hundreds of *external* red
teamers through adjacent sections. The boundary between them moves between papers — in
GPT-4's report the red-team list sits inside the credit section, in GPT-4o's it sits
after it. So the deterministic parser included red teamers on one paper and excluded
them on another, and the LLM did the opposite. **Both extractors are behaving sensibly
against an under-specified target.** No amount of extraction quality fixes this; the
register has to decide whether an external red teamer is a contributor, and that
decision belongs in config, not in a parser.

**"Core contributors" does not transfer between labs.** OpenAI applies it to ~219
people. Anthropic marks about a dozen of 117; DeepSeek eighteen of 415. The same string
denotes an elite at one lab and most of the staff at another. Any cross-lab scoring that
keys on it is measuring the label, not the person.

**OpenAI's newest cards publish no credit structure at all.** GPT-5 (486 authors) and
the Privacy Filter card carry arXiv metadata only. DeepSeek did the same after R1. Two
of three labs have now stopped publishing contribution roles, so role-based scoring
degrades over time by default and cannot be relied on for current signal.

**Collection limit: openai.com is unreachable.** Its CDN returns 403 to non-browser
clients although robots.txt permits crawling. The research blog cannot be ingested, so
arXiv is the only channel — recorded as a gap, not worked around.

**Corpus enumeration was manual, again.** OpenAI files under assorted author strings and
no single query returns the set. Third lab, third time; §11 step 0 is confirmed as the
real work.

**Two harness lessons, both of which first looked like model failures.**
1. Adaptive thinking shares the `max_tokens` budget with the answer. A 330-name section
   truncated mid-JSON at 32K. Now raised, and truncation is caught and named rather than
   crashing in `json.loads`.
2. Anchoring a context window on the last textual match put it on the wrong section
   entirely, scoring 0 matches on GPT-4o. Anchoring on the heading fixed it. **Every
   catastrophic score in this project so far has been the harness, never the model.**

**Consequence.** OpenAI is a weaker register entry than the other two: no blog access, no
current role structure, and an ambiguous contributor definition. Its value is the roster
and its growth (281 -> 420 -> 486 authors), not per-person signal.

---

## Publication-derived importance does not generalise across labs — negative result (2026-08-31)

**What was tested.** Whether a lab's staff, and their relative importance, can be derived
from the author lists on what the lab publishes. The specific hope was to surface people
*below* the media spotlight — the layer the brief argues carries the real signal — without
relying on press coverage or LinkedIn. Tested end-to-end on three labs: Anthropic
(117 people, 54 articles), DeepSeek (415 people, 8 papers), OpenAI (1,079 people,
7 papers), with deterministic and LLM extraction evaluated against each other at every
step.

**Result: the method works on one lab of three, and the reason is structural, not fixable.**

Deriving importance from bylines assumes two things, and only Anthropic has both.

| | articles/yr | authors/paper | does frequency discriminate? |
| --- | --- | --- | --- |
| Anthropic | ~54 | 3–20 | **yes** |
| DeepSeek | ~3 | 200–330 | no — it measures tenure |
| OpenAI | ~2 | 300–486 | no |

At OpenAI, appearing on the GPT-5 system card places a person among 486. "Core
contributors" is applied to 219 people. No threshold creates a signal that is not in the
data: **for a lab that publishes rarely and credits hundreds at a time, a byline says
"employed during this release cycle" and nothing more.**

**The one success case is itself narrower than it appears.** Anthropic's publication
stream is overwhelmingly safety, alignment and interpretability. The register that method
produces is therefore not "important people at Anthropic" but "people who publish safety
research at Anthropic". Pretraining, RL, inference and product engineering are largely
invisible to it. The success case measures a slice and should be described that way, not
as coverage.

**Compounding failures at the sparse-publishing labs**, each recorded in its own entry
above: role vocabularies that do not transfer between labs; two of three labs abandoning
contribution roles entirely (DeepSeek after R1, OpenAI after o1, 18+ months ago);
openai.com unreachable to the fetcher; and corpus enumeration requiring manual work at
every lab.

**What this does not invalidate.** Extraction quality was never the problem. The
deterministic parsers and the LLM both performed well (F1 0.978 / 0.996 / 0.879, with
zero adjudicated hallucinations in any of the three). **The pipeline extracts correctly
what the labs publish; the labs simply do not publish what would answer the question.**
That distinction matters: this is a finding about the source material, not a defect in
the system.

**What the bylines are still good for.** At the sparse labs the register is a *roster*,
and the aggregate is genuinely valuable: OpenAI 281 → 420 → 486 authors, DeepSeek
197 → 264 → 328, with 49 people absent between two DeepSeek releases and 20 marked as
departed by the lab itself. That is headcount, hiring and churn data on private companies
from primary sources. It is lab-level signal, not person-level.

**Consequence — coverage is deliberately uneven, and the design doc must say so.**
Person-level tracking is viable at Anthropic-shaped labs (frequent, small-author
publications). At OpenAI- and DeepSeek-shaped labs it is not, and those labs get
leadership-tier tracking plus roster aggregates instead. Claiming uniform person coverage
across the register would be false.

**Consequence — no cross-lab importance score.** Any score spanning these labs would be
dominated by publication cadence and would rank a mid-tier Anthropic safety researcher
above OpenAI's chief scientist. The labs are not comparable on this axis; forcing
comparability would produce a number that looks rigorous and means nothing.

**Where importance comes from instead.** Sourced rather than computed: the leadership
register ([planning.md](planning.md) §12) and press-reported moves, which invert the
problem — the press does the filtering, so a reported departure is itself the evidence of
importance. Candidate additional sources for reaching non-publishing staff (GitHub commit
authorship, patent inventorship, conference speakers) are under discussion and not yet
tested.

---

## GitHub commit authorship as a person-discovery source (Anthropic, n=1)

**Decision.** Test whether GitHub commit history surfaces trackable people at a lab, run
against the `anthropics` org over a 12-month window. Treated as a *complementary*
population to the papers register, not a replacement for it.

**Why a separate population, tested up front.** The papers register is 175 people who
publish safety research. GitHub is expected to surface whoever ships public product code.
Measured overlap: **3 of 175 (1.7%)**, and all three are marginal committers (1, 1 and 9
commits). The two sources address disjoint groups of staff, so GitHub does not repair the
coverage gap the papers work left — it opens a different one.

**Employment is evidenced, not inferred — this is the main find.** 61% of human commits
in the org carry an `@anthropic.com` address. A commit made from a corporate address is
direct evidence of employment, which nothing in the papers work ever gave us; bylines only
ever asserted affiliation. Two weaker signals back it up: the GitHub profile `company`
field, and a `-ant` / `-anthropic` work-handle convention. The classification records
which signal fired rather than collapsing them, because only the first is evidence and the
last is a guess.

**Alias resolution is easier here than in papers.** Commit email is a hard key: two
accounts sharing a real address are the same human, no corroboration needed. Contrast the
papers work, which required a co-authorship graph to guess. The one trap is GitHub's
`users.noreply.github.com`, which is issued *per account* and would merge strangers if
treated as an identifier — excluded explicitly and covered by a test.

**Alternatives rejected.**
- *Rank by raw commit count.* Rejected: 13,439 of 16,896 commits are release automation
  and code generators. Bots outrank every human in the org.
- *Exclude repos by low corporate-email share.* Rejected after it would have wrongly
  dropped `buffa`, a genuine first-party project (2% corp email) whose top committer's
  profile names Anthropic as employer. Staff commit under personal addresses often enough
  that a repo-level threshold is unsafe.
- *Trust GitHub's `fork` flag to find non-first-party repos.* Rejected: it does not work.
  `OpenROAD-flow-scripts` reports `fork=false` and `mirror_url=null` yet is a declared
  read-only mirror of an upstream project, and alone contributed 2,441 commits by 43
  external maintainers — it topped the raw ranking. Mirrors are detected from their
  description instead, and the exclusion is reported on the output rather than applied
  silently.
- *A single computed importance score.* Rejected, consistent with the papers finding.
  Commit count measures who maintains public code, not seniority. Commits, repo breadth
  and recency are recorded side by side and left uncollapsed.

**Consequence.** GitHub yields an employment-evidenced roster of public-code contributors
with their outside channels attached. Whether that population is worth *tracking* — as
opposed to merely being identifiable — is the open question, and is judged on the register
itself rather than assumed.

---

## GitHub validated on a second lab (OpenAI): the method holds, the yield does not

**Decision.** Run the identical GitHub pipeline against the `openai` org. Per-lab config
(email domain, work-handle suffix) moved into a `LABS` table so adding a lab stays a
config change, per the non-negotiables.

**The method generalises.** No new extraction code was needed. 456 staff evidenced at
OpenAI against 176 at Anthropic, 424 of them by corporate commit email. The employment-
evidence approach is not an Anthropic artefact.

**The labs are not alike, and the difference is structural.** Bot share is 80% at
Anthropic against 12% at OpenAI: Anthropic's public org is mostly Stainless-generated SDK
code and release automation, OpenAI's is hand-written. OpenAI therefore exposes 2.6× the
staff and 4× the substantial contributors (52 people with ≥50 commits, against 13).

**This inverts the papers ranking, which is the load-bearing point.** In the papers work
OpenAI was the sparse lab — rare publications, author lists in the hundreds, useless for
person discovery. On GitHub it is the densest people source found at any lab. Two sources
rank the same three labs in opposite orders. That is independent confirmation of the
earlier decision to build **no cross-lab importance score**: the ranking is an artefact of
which source you happen to look through.

**The negative result: finding more staff does not find more channels.** Staff with a blog
or X handle: 48 of 176 at Anthropic (27%), 51 of 456 at OpenAI (11%). The absolute number
is ~50 at both labs while the staff count grows 2.6×. GitHub is a strong *identity and
employment* source and a weak *channel discovery* source, and scaling it does not fix the
second.

**Consequence — GitHub reaches the application layer, which decides its audience.** Both
orgs are product surface (Claude Code, plugins, SDKs; `codex`, agents SDKs, chatkit).
Neither exposes the people building models. So this feeds the **AI-team renderer** — is
this SDK maturing, is this pattern worth adopting — and is weak for the **investment
renderer**, since public repo activity does not move a thesis on compute or the energy
complex. That is the two-audience split already in the brief, not a new system.

**Consequence — a plausible division of labour, not yet committed.** Papers answer what
the labs are *researching* (investment-relevant, viable only at Anthropic-shaped labs);
GitHub answers what they are *shipping* (AI-team-relevant, viable at OpenAI- and
Anthropic-shaped labs). The two populations barely intersect: 1.7% overlap at Anthropic,
4.0% at OpenAI. Whether to fund the ingestion work for ~50 channels per lab is open.

**Alternatives rejected.**
- *Reuse the Anthropic work-handle pattern across labs.* Rejected after it silently
  produced zero merges on OpenAI, which has its own `-oai` / `-openai` convention. A
  pattern that matches nothing looks like clean data rather than a misconfiguration, so
  the suffix is now per-lab and a test pins that a wrong-lab pattern merges nothing.
- *Enrich every contributor.* Rejected on cost: 1,146 people at two calls each. Profiles
  are fetched for staff plus anyone with ≥2 commits (675 of 1,146); the single-commit tail
  is retained in the register but flagged `profile_fetched: false` rather than dropped.
- *Count `c-openai.com` contractors as staff.* Rejected: "works at the lab" and "is
  employed by the lab" are different claims. Recorded as a separate tier.

---

## Announcement scoring: classify with the LLM, score with a rule

**Decision.** The LLM never emits a score. It classifies an announcement and tags the
mechanisms and categories it touches, with a verbatim quote required for every tag. The
score is computed deterministically from `config/scoring.yaml` as
`event_weight × strongest_mechanism(magnitude × confidence)`.

**Why.** An opaque 1–10 from a model cannot be defended in a room. Every number this rule
produces can be argued line by line and changed without touching code, which is also what
the "config, not code" requirement demands. The LLM does what it is good at — reading a
document and saying what is in it — and the judgment about what matters stays inspectable.

**Alternatives rejected.**
- *Let the model score directly.* Rejected as indefensible under questioning.
- *Sum the mechanism tags.* Rejected: six weak tags would out-score one strong one. The
  rule takes the maximum, pinned by a test.
- *Hand-score a gold set as the anchor.* Rejected as ground truth (it is opinion), though
  retained as a tiebreaker. See the evaluation discussion — market-outcome and press-lead
  anchors are the defensible ones and are not yet built.

**Result on the first run.** 375 announcements over six months; 289 (77%) scored zero.
Zero is the expected answer for regional expansion, grants, hiring and policy comment, and
the filter working is the point. The top of the list is what an investor would want:
Anthropic–Google–Broadcom and Anthropic–Amazon compute commitments, the $65B Series H,
DeepSeek V4 open weights, and the frontier model releases. $5.25, no failures.

**Four rule failures found by examining disagreements, not by averaging them.** The model
also emits its own `notable` flag; where that disagrees with the computed score, the score
is usually wrong:

1. *`corporate_other: 0` is a trap.* "Anthropic confidentially submits draft S-1 to the
   SEC" scored **0**. An IPO filing by a frontier lab is plainly investment signal. The
   catch-all bucket swallows corporate finance events.
2. *Event weight acts as a veto.* Because the rule multiplies, a low-weight event type
   crushes a high-magnitude mechanism. The first US export control applied to a frontier
   model's access scored 20, because `safety_policy` is weighted 1 — even though the
   `export_controls` mechanism fired at high magnitude.
3. *Routine point releases are over-scored.* Every incremental model release scores 46–67
   alongside genuine frontier launches. Magnitude should separate them; event weight
   overrides it.
4. *The mechanism vocabulary has a hole.* `categories.yaml` explicitly names custom
   silicon as the thing `accelerator_custom_si` is most exposed to, but no *mechanism*
   exists for a lab moving to in-house silicon. On "OpenAI and Broadcom unveil LLM-optimized
   inference chip" the model reached for the category id in the mechanism field; the
   validity guard dropped it as a hallucination. **The guard was right and the vocabulary
   was wrong** — the model was expressing something real that the config could not say.

**Consequence — the multiplicative form is the structural bug.** Event type and mechanism
strength are independent axes and should contribute additively; multiplying lets either
one veto the other. Not yet changed: it alters every score, and the vocabulary gaps
(`corporate_finance` event type, a custom-silicon mechanism) should be fixed in the same
pass.

**Consequence — lab announcements barely reach the fund's largest category.** Category
reach is concentrated in `hyperscale_platform` (18) and `ai_compute_hosting` (12), while
`memory_storage` — the fund's *largest* category at 19% — is touched 3 times, and
`foundry_logic` and `semicap_equipment` once each. Labs announce compute deals and models;
they do not announce DRAM. Reaching memory and semicap needs the mechanism layer to carry
the inference (more accelerators ⇒ more HBM), which is exactly what `mechanisms.yaml`
exists for and what the propagation step must be judged on.

**Source asymmetry, recorded.** openai.com is behind Cloudflare: the news index, article
pages and WebFetch all return 403. OpenAI items are scored from the official RSS title and
summary (median ~165 characters) with tag confidence capped at medium; Anthropic and
DeepSeek are scored on full text. Every item on the page states which it was, so an
OpenAI score is never silently compared against an Anthropic one.

---

## Model selection for announcement classification, and the variance problem behind it

**The problem, discovered not assumed.** Comparing two full runs over the same 375
announcements showed 9% of re-classified items flipping their `is_signal` verdict. A
dedicated probe — same article, same model, same prompt, three times — found only **6 of 12
borderline items scoring identically**, with one article ranging from **17.8 to 80 across six
runs**. A score that moves that much cannot be shown to an investment team.

**There is no temperature to turn down.** `temperature` is deprecated on the Claude 5
family: absent from the SDK signature, and rejected by the API with "`temperature` is
deprecated for this model". Verified directly rather than assumed. The variance is
inherent to the model as exposed and has to be handled architecturally.

### What was tested, in order

**1. Remove the `is_signal` field (prompt v3 → v4).** Hypothesis: the flag was an escape
hatch — when the model set it false it emitted no mechanism tags in 86% of runs, versus 0%
when true, so a single unstable boolean was discarding the evidence.

| Same 12 items, 3 runs | v3 (with `is_signal`) | v4 (removed) |
|---|---|---|
| Score identical across runs | 3/12 | **6/12** |
| Median score range | 6.7 | **2.2** |
| Worst range | 35.6 | **17.8** |
| `event_type` stable | 8/12 | **10/12** |

Adopted, but **the hypothesis was wrong**: empty-tag runs went 39% → 47%, slightly worse.
The model abandons tagging just as often without the field to justify it. What improved was
score reproducibility via steadier `event_type`. Recorded because the fix worked for a
different reason than predicted, and the residual instability is in the tagging step.

**2. Haiku 4.5 instead of Sonnet 5.** More consistent on most items (9/12 identical, median
range 0.0) and 4.3x cheaper — but a worse tail (worst range 66.7) and only **9/12 modal
agreement with Sonnet on event type**. Rejected: it scored *both* S-1 filings at 0.0,
stably. Its apparent stability is partly confident wrongness, and it silently reintroduces
the exact failure this work existed to fix. Noted as viable in a two-tier design: Haiku for
bulk classification, Sonnet only near band boundaries or where repeated Haiku runs disagree.

**3. Majority voting, Sonnet 5.** Classify each item 3 times; majority on `event_type`,
keep a mechanism present in >=2 of 3 runs, median magnitude and confidence. Then run the
whole voting process **twice** and compare — because voting is worthless unless the reduced
answer is itself stable.

**Result: 6/6 voted scores identical across two independent passes**, and the vote absorbed
genuine disagreement on **5 of 6 items** (an event-type split 2:1, tags appearing 3/3 in one
pass and 0/3 in the next). It converges because the score depends only on `event_type` and
the *strongest* mechanism, both majority-stable even when the full tag set is not. Taking
the max rather than the sum is what makes voting cheap here.

**4. GPT-5-mini instead of Sonnet, same prompt, schema and voting rule.** 4.7x cheaper.

| | Sonnet 5 | GPT-5-mini |
|---|---|---|
| Voted score reproducible across passes | **6/6** | 4/6 |
| Mechanism tags dropped by the vote | 1 | 7 |
| Cost (6 items, 3 votes, 2 passes) | $1.15 | **$0.25** |

Rejected for now. The vote did not converge: on the OpenAI-Broadcom chip the event-type
majority flipped between passes (`compute_commitment` 2:1, then `product_launch` 2:1) with
*identical* mechanism tags, so a 40-point swing came from the event vote alone. It also
disagrees systematically, not just noisily, scoring Claude Opus 4.8 at 40 against Sonnet's
17.8 in both passes. Seven dropped tags against one means its runs agree with each other far
less, so majority-of-3 cannot settle them.

**Untested and worth testing:** 5 or 7 votes with GPT-5-mini would still undercut Sonnet at
3 votes on price. The saving is real; it just needs more votes to reach the same place.

### Decision

**Sonnet 5 with 3-vote majority consensus**, prompt v4. Roughly $0.19 per voted item,
about $21 for a 375-item backfill uncached and ~$10 batched.

### Consequences

- Consensus is part of the pipeline, not an optimisation. A single classification is not a
  defensible input to a score that a person will act on.
- The vote's own disagreement is recorded per item (`vote_notes`: event counts, tags seen,
  tags dropped), so a reader can see how contested a score was. This feeds the variance
  reporting required in [planning.md](planning.md) 6a.
- Provider choice is now a shim (`research/announcements/providers.py`), so re-testing a
  cheaper model later is a flag, not a rewrite.
- **Caveat on the evidence:** six items, two passes. Strong evidence that 3-vote consensus
  stabilises the score, not proof it is stable everywhere. Items sitting exactly on a band
  boundary are the likely remaining failure and have not been probed.
- **Caveat on the cost figures:** GPT-5-mini pricing is marked UNVERIFIED in `providers.py`.
  Token counts are exact from the API; the dollar conversion was not checked against
  OpenAI's published prices.

## Confidence becomes a gate, not a discount (2026-09-01)

**Decision.** In `config/scoring.yaml` v4, the confidence scale changes from
`{high: 3, medium: 2, low: 1}` to `{high: 1.0, medium: 0.5, low: 0.0}`, on both
the mechanism and practice axes. `max_mechanism` and `max_practice` fall from 9
to 3 accordingly. A low-confidence tag now contributes nothing to any score.

**Why, and this one is evidence-led rather than argued.** A hand review of a
20-article gold run (`docs/gold_review.md`) went through every tag against the
source text. Every tag rejected as unsupported was **already marked low
confidence by the model**. Two representative cases, both quoting marketing copy
rather than a claim:

- `inference_volume_up` on OpenAI's $122bn raise, cited to *"meet growing demand
  for ChatGPT, Codex, and enterprise AI"*
- `inference_volume_up` on a GPT-5.6 pricing item, cited to *"deploy AI workflows
  at scale"*

Neither states anything about volume. The model knew — it marked both low — and
the scale paid for them anyway at 1/3 rather than 0. The filter already existed
in the output and was being discarded by the arithmetic.

**Consequence, measured before committing.** Across the 192-article register the
change moves 34 items and drops 5 to zero. All five are `enterprise_partnership`
items previously scoring 4.4 whose only tags were low magnitude and low
confidence — *"GPT-5.6 is now the preferred model in Microsoft…"*, *"TCS and
Anthropic bring Claude to regulated industries"*. Nothing above a score of 5 is
destroyed. The largest single fall is 66.7 to 50.0, on compute-commitment items
whose strongest tag is medium confidence.

**Alternatives rejected.**

- *Keep 1-3 and filter low-confidence tags in the renderer.* Rejected: it hides
  the rule in presentation code, where it cannot be argued or changed in config.
  The point of a config-driven rule is that the whole thing is on one page.
- *Drop low-confidence tags at extraction.* Rejected: the tag and its quote are
  still evidence worth keeping in the register, and a future rule may want them.
  Scoring them zero keeps the record and removes the influence.
- *Apply the gate to magnitude too.* Rejected: a small effect is still an effect.
  Magnitude keeps 1-3. Confidence is the only axis where the low value means
  "we may be wrong about this", which is the thing that should not be paid for.

**What this gives up.** D-era reasoning argued for equal 1-3 scales on both axes
so neither was quietly favoured, and for thirds specifically so the split could
be defended as "it is just thirds". That symmetry is now gone: magnitude runs
1-3 and confidence runs 0-1. The replacement statement is stronger and just as
short — *confidence is a probability weight, and we do not score what we are not
confident the document says*. Under the old scale a high-magnitude,
low-confidence tag was worth more than a low-magnitude, high-confidence one,
which is backwards for a system whose main risk is fabricated evidence.

**Applied to both axes deliberately.** The AI-team rule mirrors the investment
rule so the two numbers are readable side by side; changing one alone would make
them incomparable. Reversible if the practice axis turns out to need it.

---

## Recover OpenAI article text from the Internet Archive (2026-09-01)

**Decision.** Keep RSS for *discovery* of OpenAI articles, and backfill the
*text* from the Internet Archive. Recovered articles are marked
`text_source: full_text_archived` and carry an `archive_snapshot` URL.

**The problem, measured.** openai.com returns 403 to every automated request for
article HTML. Re-probed on 2026-09-01: plain curl, a browser user agent, a full
browser header set and Googlebot all 403; `sitemap.xml` and `news/rss.xml`
return 200. Cloudflare is blocking HTML, not XML. The RSS feed carries a title
and one sentence — median **205 characters** against ~9,200 for an Anthropic
page — and OpenAI is 79% of the corpus. Nearly four fifths of what we score was
being judged on a headline, which is also why so many OpenAI tags come back
low-confidence and, under the new gate, now score zero.

**Result.** 138 of 149 recovered in one run (**93%**, 0 failures), **1,423,288
characters** gained. Median recovered length 9,270 characters — comparable to a
directly fetched Anthropic page. The corpus is now 141 archived, 40 direct,
**11 RSS summaries**, down from 152.

**Alternatives rejected.**

- *Browser automation (Playwright) against openai.com.* Would probably work, but
  it is deliberately defeating a block the publisher has put up, it adds a heavy
  dependency, and it breaks whenever the challenge changes. The archive is a
  public, citable, stable source that is meant to be read.
- *Accept the RSS summaries.* Rejected on evidence: the gold review traced
  several weak and spurious tags directly to the 189-205 character ceiling
  (`04` GPT-5.6 pricing, `15` Jalapeño). The ceiling was causing the errors.
- *Third-party news coverage of OpenAI.* Rejected: it breaks the primary-source
  guarantee. An archived openai.com page is still the primary source.

**Engineering notes, both found the hard way.** Snapshot discovery is *one* bulk
CDX wildcard query, not 152 lookups. Fetches must be serial with backoff — at
six concurrent the archive failed 20 of 24 probes; that is the source's rate
limit and not ours to tune away. Snapshots are fetched with the `id_` modifier
so we store the originally archived bytes rather than the archive's rewritten
page with its injected banner; those bytes carry the original Content-Encoding,
so gzip is handled explicitly.

**What this gives up.** An archived snapshot is a point-in-time capture, not the
live page, and 11 articles have no snapshot at all. Both are visible rather than
hidden: the `text_source` distinguishes archived from live text, the snapshot
URL resolves to the exact capture, and the prompt has a confidence rule for the
archived source. A recovery is rejected outright if it is shorter than the RSS
summary we already hold, so a redirect stub or error page can never overwrite
good text.

---

## Fix the date parser: full month names (2026-09-01)

**Decision.** `date_from_page` accepts full month names as well as
abbreviations, with the abbreviation prevented from partially matching the full
name.

**Why, and why it is recorded.** This was found by hand-reading the gold set, not
by a test. `MONTHS` held only `Jan Feb Mar ...`, so `\bOct` matched the first
three letters of `October` and `\s+` then failed on `ober` — full-name dates
could not match at all. Anthropic prints the article's own date in full and its
"Related posts" footer in abbreviated form, so the parser skipped the real date
and silently took the date of an unrelated recent article from the footer.
"Introducing Agent Skills" was dated **316 days late**, putting a ten-month-old
article inside a three-month window, and it was in the gold set.

**Damage measured before fixing:** 2 of 40 sitemap-dated articles wrong. Both
corrected; the out-of-window one was dropped and the register is now 191.

**The lesson worth keeping.** `tests/test_announcements.py` states that it exists
to catch *"a date parser that places an old release inside the window"*, and it
did not, because every test case used the abbreviated form the code already
handled. A test written from the implementation cannot catch what the
implementation never considered. The three regression tests use the real page
shape — header date in full, footer dates abbreviated.

---

## Rebuild the gold set so it samples the corpus (2026-09-01)

**Decision.** Keep the ten gold articles still in the corpus, replace the other
ten with a draw that is **random within lab strata**, and move the displaced ten
to `gold/hard_cases/` — retained for error analysis, excluded from any accuracy
figure. Re-prefill all twenty at v5 with both axes.

**Why now.** The set had drifted three ways at once, none visible from inside it:

1. The window was cut from six months to three, so **ten of twenty articles were
   no longer in the corpus at all**.
2. It was drawn using scores computed from **205-character RSS summaries**.
   OpenAI is 79.6% of the corpus, so for four fifths of it the selection was
   effectively blind: any article whose signal was not visible in two sentences
   could never have been picked. This is the one that matters, and it only
   became visible after the archive backfill.
3. Its labels predated the practice axis and were filled at prompt v4, so the
   set could not measure half the system.

Nothing human was lost: 0 of 20 gold blocks carried a reviewer note.

**Why the replacements are drawn at random.** The folder README already said the
set "was drawn using the pipeline's own scores, so it cannot reveal a kind of
signal the system misses entirely". A blind draw is the only part of a gold set
that can. The cost is that most blind draws are noise — five of the ten score
zero on both axes — but an empty tag list is the most common correct answer in
this pipeline, and it was previously untested at all: the old set had **zero**
articles scoring zero on both axes.

**Why stratified by lab.** An unstratified draw does not fix the mix. The set
was 45% OpenAI against a 79.6% OpenAI corpus, and the first unstratified draw
came back five-five and left it at 45%. Lab is a fact about the article, not a
judgement the pipeline makes, so stratifying on it costs none of the blindness
that matters. Result: 70/25/5 against a corpus of 79.6/19.4/1.0. Anthropic stays
over-weighted because five of the ten retained are Anthropic; recorded as a known
bias rather than smoothed away.

**Alternatives rejected.**

- *Redraw all twenty.* Cleanest sampling story, but it discards the hard cases,
  and a hand review had just found that all three serious classification errors
  were on articles of exactly that kind. `hard_cases/` keeps them usable without
  letting them into an accuracy figure.
- *Keep the twenty and relabel only.* Cheapest, but leaves a gold set that does
  not sample the corpus and whose OpenAI half was selected from two-sentence
  summaries. That is a set that cannot answer the question it exists for.
- *Stratify the new draw on predicted signal.* Would have reproduced the exact
  flaw being fixed.

**Cost.** $0.8768 for 20 calls at v5.

---

## Cap `model_capability` dimensions at three (2026-09-01)

**Decision.** At most three dimensions per `model_capability` tag, asked for in
the prompt and **enforced in code** after extraction. Dimensions past the cap
are recorded in `dropped_tags`, not silently discarded.

**Evidence.** On the first v5 run the three frontier releases named 8, 7 and 7 of
9 dimensions. A tag naming almost the whole vocabulary says only "this is a big
launch", which the headline already said, and it destroys the filtering the
field exists to provide — the same fan-out problem as a mechanism that reaches
16 of 26 holdings. Small releases were tagged tightly at 1–2, so the cap costs
nothing there.

**Measured after the change, on the same ten articles:** dimension counts went
from `[7, 2, 2, 2, 2, 1]` to `[3, 3, 3, 2]`. The cap holds.

**Why it is enforced in code and not only asked for.** The prompt already told
the model not to over-tag, and it over-tagged anyway. The same run showed the
same thing on `action`: the prompt says *"`watch` is the default and the most
common correct answer"* and the model produced the exact inverse ordering. An
instruction that has already failed once is not evidence for itself. **`action`
is still not fixed** — on the same ten articles it went from adopt 8 /
investigate 8 / watch 7 to adopt 12 / investigate 9 / watch 7, i.e. worse. Only
the blind-draw half shows the intended shape (adopt 1, investigate 3, watch 8),
which is the noise articles behaving correctly rather than the calibration
improving. Recorded as open.

---

## The verbatim quote gate (2026-09-01)

**Decision.** Before anything is scored, code checks that every tag's quote
appears in the document it cites. A tag whose quote is not there is **dropped**.
A quote that matches only after normalisation is **snapped** to the exact
substring of the source and kept.

Wired into all three classification paths (`score_announcements.classify_one`,
`vote.run_pass`, `run_gold`), so no route into the register bypasses it.

**Why the snap, and why it is safe.** The rewrite happens only when the source
proves it correct — the value written is a substring of the document, never
anything the model produced. The result is stronger than rejecting
hallucinations: **every stored quote is a literal substring of the article**, so
a reader who searches for it finds it. That is the difference between claiming a
citation guarantee and having one.

**Why now rather than earlier.** Two things had to be fixed first, both today.
The stored text held raw `&amp;` entities, so correct quotes looked fabricated —
an earlier check reported ten failures of which nine were the checker's fault.
And OpenAI articles were 205-character RSS summaries, so for 79% of the corpus
there was almost nothing to check against.

**What it accepts, and why each relaxation is not a loophole.**

- *Ellipsis.* `A ... B` passes when both halves are found **and A precedes B**.
  Ordering is enforced: without it the notation licenses exactly the splice this
  exists to catch. Segments under 12 characters are refused, so `the ... and`
  cannot pass.
- *Literal `\uXXXX` escapes.* Observed once: the model emitted the six
  characters `’` instead of an apostrophe, so ctrl-F found nothing.
- *A capitalised first letter.* The source reads "it delivered", the model
  quoted "It delivered". Beginning a quote mid-sentence and capitalising it is
  ordinary practice.
- *Terminal punctuation.* The source reads "universal jailbreaks:", the model
  ended its quote with a full stop.
- *Curly quotes, dashes, whitespace, and a space left before punctuation by HTML
  extraction* ("Terminal-Bench 2.1 ,").

**What it refuses**: any elision from the middle of a quote that is not marked
with an ellipsis. That is the splice shape — an earlier run produced a quote
built from the first word of one sentence and the body of another, stating a
true fact in words the document never used.

**A refusal that was right, and fixed upstream.** The gate initially dropped a
correct tag on article `20`, because the model had read across
`(opens in a new window)` — which the prompt tells it to ignore. The gate was
not wrong; the stored text was. `strip_chrome` now removes link and navigation
furniture at extraction, and the corpus was repaired: **92,711 characters across
180 articles**, including 3,603 occurrences of that one phrase. That chrome was
being paid for on every API call and was corrupting quotes.

**Measured on the gold set.** 69 tags: 58 exact, 8 repaired by snapping, 3
initially dropped. All three drops were false positives — two capitalised first
letters and one terminal colon — which is what drove the case and punctuation
relaxations. After those: **69 of 69 pass, 0 dropped, every quote a literal
substring.**

**What this gives up.** A relaxed match could in principle let through a quote
whose meaning was changed by a case difference; no such case exists in English
worth the strictness. More real: the gate proves a quote is *in* the document,
not that it *supports the tag*. Article `04`'s `inference_volume_up` cites a
customer testimonial that is genuinely present and still weak evidence. The gate
is a floor, not a judgement, and the hand review remains the thing that catches
a well-quoted wrong tag.

---

## Evaluation has no human ground truth, and says so (2026-09-01)

**Decision.** Drop independent human labelling. Replace the unreviewed pre-fill
in the gold set with **adjudicated** labels: every tag judged against the full
article by a different model from the classifier, with a recorded reason, in
`gold/adjudication.yaml`. Delete `gold_human/`.

**Why.** Human labelling was not going to happen — twenty articles at ~14,000
characters each is most of a working day, and the person who would do it said
plainly that they would not. An empty `gold_human/` folder implying otherwise is
worse than not having one: it lets the design document imply a validation step
that does not exist.

**Why the previous state was untenable.** The `gold` blocks held pre-fill from
the pipeline itself, so the reference and the thing being measured were the same
object. Agreement with it proved only that the classifier repeats itself, and
the metrics script said so in its own header. That is a reproducibility measure
wearing an accuracy label.

**What the adjudication is.** Classifier `claude-sonnet-5`; adjudicator
`claude-opus-5` reading the whole article, the classifier's output, and a second
independent run of it. Four properties make it worth more than self-grading:

1. Two independent runs are visible, so run-to-run disagreement routes attention
   to where the classifier was least stable.
2. Quotes are mechanically verified first, so the judgement is only ever whether
   a quote *supports* a tag, never whether it exists.
3. Every verdict carries a reason and is spot-checkable in a minute.
4. The adjudicator is held to the model's own rule — a quote it supplies goes
   through the same gate, and the build fails if it is not in the document.

**What it cannot do, stated rather than hidden.** The adjudicator wrote the
vocabulary it is judging against, so it cannot find a kind of signal both models
miss. `vocabulary_gaps` records four found this way; a gap nobody thought of
stays invisible. And the adjudication was built from a run, so *that* run scores
against it optimistically — the figures are a baseline for the next run, not a
report card on the one that produced them. Both caveats are printed by
`gold_metrics.py` on every report so they cannot be quoted without them.

**Alternatives rejected.**

- *Keep `gold_human/` empty in case someone fills it later.* Rejected: an
  unfilled slot in a repo reads as a plan. It was deleted, and git history
  keeps it recoverable.
- *Use a third model as an independent judge.* Attractive, but it substitutes a
  second opinion for ground truth while sounding more objective than it is. The
  same blind spot applies — a shared training distribution — and it adds cost
  and a provider dependency for no gain in what is actually verifiable.
- *Report agreement with the pre-fill as an accuracy figure.* Rejected as
  misleading. It was 0.78 mechanism F1 and would have been read as accuracy.

**What it found, which is the argument for doing it at all.** 7 of 20 articles
confirmed entirely; 13 with departures, 21 tag-level verdicts. The two largest
corrections were both **under-scoring, on the articles a fund would most want to
get right**: the US directive forcing a lab to disable two frontier models
(13.3 → 80.0, a magnitude the model called "low" while quoting a document that
says the standard would "essentially halt all new model deployments"), and
OpenAI reaching general availability on AWS with Amazon at 11.03% of the fund
(0.0 → 26.7, a mechanism one run scored at zero confidence and the other did not
tag at all). Accuracy is not uniform across the score range, and the errors
concentrate at the top of it.

---

## Blind test of the adjudicator, and what it found (2026-09-01)

**Why.** The adjudicated gold set is **85% classifier output the adjudicator
read and let stand** — 58 of 68 tags untouched, 10 changed or added, 9 removed.
Reviewing is a weaker act than authoring: it is easier to accept a plausible
wrong tag than to notice a missing one. Agreement measured that way is partly
just anchoring.

**Protocol.** Five articles drawn at random (seed 5150) from the 171 corpus
articles in neither the gold set nor `hard_cases`, so the adjudicator had seen
no tags for any of them. Labels written from the articles alone and **committed
to git before the classifier was run** (`81bad0e`), so the ordering is checkable
rather than asserted. Then classified, then compared. Cost $0.1881.

**Result 1 — tag selection agrees almost completely.** All 13 tags the
adjudicator wrote were also produced by the classifier. Event type agreed on
3 of 5. This is the reassuring half: the two are not looking in different places.

**Result 2 — the adjudicator is systematically the more aggressive scorer.** Of
28 ordered-field comparisons on shared tags, 13 matched and **11 of the
remaining 15 had the adjudicator higher** — higher magnitude, higher confidence,
more actionable. Attribute agreement was only 59%. The blind scores were 26.7 vs
6.7 and 26.7 vs 13.3 on two articles where every tag was identical.

This directly qualifies the adjudication. Of its ordered-field changes, **7
raised a value toward high or added a tag and 1 lowered one** — the same
direction as the measured bias. The corrections may still be right; article 10's
document does say the standard would "essentially halt all new model
deployments". But "the adjudicator raised the score" is now a known tendency
rather than neutral evidence, and it must be read that way.

**Result 3 — the classifier found two supported tags the adjudicator missed, and
one miss came with a written rationale that was false about the document.** On
`B5` (PORTS-Pike, 8GW) the adjudicator recorded: *"training_compute_up was
considered and rejected. The document says 'data center' and 'gigawatts-IT' and
never says training."* The document says: *"We are contracting for this capacity
based on our projected long-term needs for **frontier training** and growing
demand for our products."* The classifier quoted that sentence. It also found
`lab_capital_access` from *"fund those commitments through revenue and cash
flow"*, which the adjudicator did not consider at all.

A confident, reasoned, written-down rejection was wrong on a fact about an
8,000-word document the adjudicator had just read. On a 191-article corpus that
failure mode does not stay rare.

**What this changes.**

- The adjudication stays, because 19 tag-level corrections on evidence is worth
  more than unreviewed pre-fill. But it is now labelled with a measured bias
  rather than presented as a correction of the model.
- Where the adjudicator and the classifier disagree only on magnitude or
  confidence, **the classifier's value is the better prior**, because the
  adjudicator's bias has a measured direction and the classifier's does not.
- Tag *selection* disagreements remain worth adjudicating: the blind test found
  no case of the adjudicator inventing a tag the classifier had no basis for.

**What it cost to learn.** $0.19 and one drawn sample. It is the cheapest
experiment run on this project and the only one that measured the evaluator
rather than the thing evaluated. n=5, one run, one adjudicator — enough to
establish a direction, not to calibrate a correction.

## A second join key: lab exposure, keyed on the lab not the mechanism (2026-09-02)

**The gap.** Three holdings have a direct, disclosed relationship with a named
frontier lab. All three were recorded only as prose:

| Holding | Relationship | Where it lived |
|---|---|---|
| AMZN | Equity stake in Anthropic — $53.4bn of pre-tax other income in Q2 2026 | `what_it_does`, `financials.derived.note`, `headwinds[].claim` |
| WULF | 20-year, ~401 MW Anthropic lease, ~$19bn contracted | `ai_role_reason`, `tailwinds[0].claim` |
| IREN | Multi-year AI Cloud contracts; Microsoft named | `mechanisms[1].why` |

Cited, accurate, and unreachable by code.

**Why it matters.** Routing was mechanism-only. An announcement reaches a
holding when the LLM tags a mechanism the holding also carries. A lab's
corporate-finance news — a funding round, a burn disclosure, a counterparty
credit event — tags no mechanism, so under mechanism-only routing it reaches
nobody. Yet TeraWulf is an unsecured 20-year bet on one private lab's solvency,
and Amazon books that lab's valuation in its income statement. The highest-value
lab-specific items were precisely the ones that could not route.

**Decision.** Add `lab_exposure` to companies.yaml: a list of
`{lab, kind, sign, magnitude, confidence, why, source}`. `kind` is
`equity | revenue_contract | cloud_partnership | supply | credit_support`.
Uniqueness is on `(lab, kind)` — a stake and a supply contract with the same lab
are different exposures with different failure modes, and both should route.

**Alternative rejected: `lab_equity` only.** The literal reading of "does this
holding own a piece of a lab". It captures AMZN and nothing else, and misses
TeraWulf, whose contractual exposure is larger than most equity stakes in the
book. The routing behaviour is identical for both; `kind` keeps the economics
distinct without splitting the join.

**Alternative rejected: make it a scoring input now.** Deferred. This is a
routing edge, not a score term. Wiring exposure magnitude into the score formula
touches scoring.yaml and needs its own calibration.

**`lab` resolves against sources.yaml, dynamically.** No lab list is held in
code. An id the register does not carry is a **dormant edge**: a warning naming
the lab, never an error. This keeps two things true at once — the exposure is
recorded before the lab is ingestible, and adding the lab to sources.yaml
activates its edges with no edit to companies.yaml or validate.py. The register
carries three labs today; IREN/Microsoft and WULF/Google are dormant. Dormant
edges print in their own section because the ticker warnings truncate at five,
and a buried warning is not a warning.

**Consequence — a typo is a dormant edge, not an error.** `anthorpic` would warn
rather than fail. Accepted: the alternative is a closed vocabulary that cannot
express IREN's largest disclosed counterparty. The warning names the id, so a
lab nobody recognises is visible in the output.

**Sourcing.** Same rule as tailwinds: a `source` or an explicit `unverified`.
Four of the five committed edges cite an SEC filing. WULF/Google carries
`unverified` — the ~$600m Fluidstack credit support came from mechanism prose
with no filing behind it, and it is recorded as needing one rather than dropped.

**Still missing.** NVDA has no lab exposure recorded despite obvious candidates,
because the repo holds no citation for one. IREN's disclosure of "a new
multi-year AI Cloud contract with a leading frontier AI lab" does not name the
lab, so it has no joinable id and is deliberately absent — an unjoinable
placeholder in a join table looks like data and is not.

## Classify raw articles, not summaries (2026-09-02)

**Decision.** The extraction/scoring model reads raw article text. A cheap-model
summarisation stage ahead of it is rejected. Cost pressure on classification is
answered with prompt caching instead.

**The experiment** (`research/summarisation_research.md` →
`research/summarisation_results.md`). The 20 gold articles were summarised by
both Haiku 4.5 and GPT-5-mini under a prompt (`prompts/summarisation/v1.md`)
that injects the scoring vocabulary and demands figures verbatim. The v6
classifier then ran on the summaries, with everything else held constant, and
the runs were scored against gold. Fable 5 separately read every original
against both summaries and traced whether the evidence behind each of the 68
gold tags survived.

**Why rejected.** The saving is small — 12–17% per article — because the
classifier's fixed prompt is ~7.3k of ~10.8k input tokens and the tagged JSON
output doesn't shrink. The damage is large and lands on the load-bearing axis:
mechanism recall 0.81 → 0.63 (Haiku) / 0.56 (GPT). The fidelity read shows why,
and that it is systematic: both summarisers independently deleted the *same*
low-salience mechanism-bearing sentences ("high-volume work economical at much
greater scale", "more token-efficient than past models", "smarter context
management") while preserving every headline figure. Summaries also flatten
framing (an opinion piece reads as a model release), and Haiku editorialised —
twice leaking tag vocabulary into its summary — which the citation gate cannot
catch, since quotes verify against what the classifier read.

**Alternatives rejected.** Summarise-then-classify (above). Summarise only long
documents: parked — the corpus median is ~8.5k chars, where the fixed prompt
dominates anyway; revisit only if 50k+ char papers enter the pipeline.

**Consequence.** Per-article classification stays at the measured raw rate
(~$0.047 uncached under v6). Prompt caching on the fixed prompt is the sanctioned
cost lever (~$0.25 saved per 20-article run, no quality cost).

## Prompt caching on the classifier (2026-09-02)

**Decision.** The classifier/scorer caches its system prompt (Anthropic
ephemeral cache, 5-minute TTL) on every call. Implemented in
`score_announcements.classify()`; both runners warm the cache with one serial
call before fanning out; the cost log bills cache traffic at its real rates.

**Why — the key insight from the summarisation experiment.** The experiment
set out to cut classification cost by compressing the articles, and found the
cost was never in the articles. The fixed prompt — vocabulary, instructions,
schema — is ~7.1k of the ~10.8k input tokens per call (measured: 7,059 cached
tokens), and the tagged JSON output doesn't shrink whatever the input. So
summarise-first bought only 12–17% while mechanism recall fell 0.81 → 0.56–0.63;
caching attacks the part of the bill that is actually large, and cannot change
model output at all — the model reads byte-identical prompts either way.
Verified live: a cold call wrote 7,059 tokens ($0.0207), the identical warm call
read them back at 0.1x ($0.0043). Roughly $0.24 of every $0.95 gold run, ~$2.40
of a 191-article corpus run.

**Two consequences handled, not hoped away.**

1. *The cold-start stampede.* Both runners are parallel (10–12 workers). On a
   cold cache every first-wave call becomes a 1.25x cache *write* — with 20
   articles and 10 workers, caching would cost more than not caching. Both
   runners therefore classify one article serially until a call actually pays
   (disk-cached articles are free and warm nothing), then fan out into 0.1x
   reads.
2. *Honest accounting.* `usage.input_tokens` excludes cache traffic, so the old
   cost record would have silently under-reported writes (1.25x) and
   over-reported reads (0.1x). `call_cost()` now bills all three components at
   their real rates and records write/read tokens per call; unit-tested against
   the published multipliers.

**Alternatives rejected.** Summarise-first (see previous entry — quality cost on
the load-bearing axis for a smaller saving). A 1-hour cache TTL (2x write cost;
pointless when a corpus run refreshes the 5-minute window on every call).
Caching in the provider A/B shim (`providers.py`) — left uncached so
cross-vendor cost comparisons stay like-for-like.

**Consequence.** Prompt edits invalidate the cache by construction (the prompt
is the key), so version bumps cost one extra write per run — nothing to manage.
Cost records now carry `cache_write_tokens` / `cache_read_tokens`; older records
simply lack the fields.

## Vocabulary v2 and prompt v7: boundaries from measured confusions (2026-09-02)

**Decision.** `mechanisms.yaml`, `categories.yaml` and `practices.yaml` move to
v2 and the scoring prompt to v7. No id changed, nothing added or removed — every
edit is a sharpened boundary, and every boundary corresponds to a specific
spurious or missed tag in the v6 gold run. The pipeline default is now v7.

**The bug found along the way.** The renderer injected only `description` /
`definition` into the prompt: the categories' boundaries and the practices'
per-tag action guidance were written for the classifier but never delivered to
it. Category precision ran at 0.50 and action agreement at 68% with the model
never having seen the text meant to fix both. Action guides are now rendered;
category boundaries were folded into definitions (the `boundary` field carries
fund accounting that doesn't belong in a prompt).

**The main boundaries added.** Mechanisms: `capability_jump` reserved for the
step itself, not restatements (4 of 4 false positives were partnership/usage/
opinion pieces restating a released model); `export_controls` widened to model
access withdrawn under national-security authority (the one gold miss);
`inference_volume_up` cuts both ways (a suspension is a negative tag, not no
tag); marketing copy saying "efficient" is not `inference_cost_down`; a
performance result discloses no capex. Categories: a mention is not a signal —
a partner in a case study or a cloud named as rollout venue routes nothing.
Practices: `evaluation` counts even when secondary to the story (4 of 20 gold
misses, all this shape); courses, usage stories and marketing are not
`orchestration`; behaviour observed in incidents is `evaluation`, not
`model_capability`.

**Measured, same gold set, same model** (single runs; sonnet-5 run variance
applies, but the tag-level gains held across two v7 runs):
mechanisms F1 0.77 → 0.88, categories F1 0.67 → 1.00 (20/20 identical),
practices F1 0.84 → 0.87, investment-score MAE 11.2 → 5.7, rho 0.75 → 0.94.

**What the iteration taught.** The first injection of action guides collapsed
`watch` (21/11/1 against gold's 10/15/12) and worsened AI-team MAE — guidance
that enumerates when to act reads as license to act. Rewritten watch-first
("start every tag at watch; the default wins over the guides"), the mix came
back to 11/11/9. Per-tag action agreement remains the weakest attribute (~55%,
disagreements now scattered in both directions); left as an open item rather
than tuned further, because chasing it on single n=20 runs fits variance, not
signal.

**Also:** `max_tokens` 8000 → 12000 (two figure-dense gold articles truncated
under v7; an output cap only bills what is generated). `prefill_gold.py` and
`run_blind.py` stay pinned to v6 — they are records of how past artefacts were
produced.

## The database layer, and the join finally implemented (2026-09-02)

**Decision.** A new installable `app/` package persists the pipeline into
Postgres (SQLAlchemy 2.0, engine-agnostic; sqlite fallback so a clone runs
without Docker) and implements the article→holding join that the config had
documented since the vocabularies were written but nothing consumed. Raw-first,
per Neil's shape: verbatim JSON payloads land in `raw_*` tables, YAML mirrors
into `ref_*`/`holding*` tables, clean tables derive from raw, and a
`connections` table materialises the join.

**ELT reads the committed artifacts.** The research scripts keep writing their
JSON files; the DB load only reads. Zero LLM cost, real data on a fresh clone,
and the caches stay the idempotency layer for the expensive calls. Rebuild =
drop + create_all + load, so Alembic is deferred until deployment — the DB is
fully derived from committed files.

**Four routes, provenance on every row.** mechanism (article tag × company
edge), category (tag × membership), lab_exposure (article lab × company lab
edge), named (company literally named in the text — string match on suffix-
stripped names and ≥3-char tickers). Sign composition multiplies and `mixed`
dominates; strength reuses scoring.yaml's own magnitude×confidence maps with
the low-confidence gate, and zero-strength connections are not written. Two
honesty rules over cleverness: lab_exposure and named connections are always
`mixed` — the classifier has no lab-sentiment axis and a mention carries no
polarity, so direction there would be a guess dressed as data.

**What the corpus produces.** 191 articles → 706 connections (529 mechanism,
84 lab_exposure after the gate below, 53 named, 40 category). `ai_score` is
persisted for the first time: recomputed deterministically from the practice
tags, 81 of 191 articles carry one. The recomputed investment score reconciles
with the file register 191/191 — the free end-to-end proof the transform is
faithful.

**What Postgres caught that sqlite forgave.** Two real bugs surfaced only on
the real engine: FK-ordering in the ref reload, and a failure path that could
not record its own failure. sqlite ignores foreign keys by default, so the
test suite now switches them on (`PRAGMA foreign_keys=ON`) — the dialect gap
that hid the bugs is closed, not worked around. Consequence of the fix: a ref
reload wipes the whole derived clean layer and the load rebuilds it; raw
tables are never touched — the DB's history lives there, which also ends the
silent disappearance of articles that fall out of fetch's 3-month window.

**The lab_exposure gate (decided the same day).** Ungated, the route connected
*every* article from an exposed lab — 263 rows, because the edge fires on the
publisher rather than on what was published. 179 of them came from articles
scoring zero: "Expanding OpenAI's presence in Brazil" reaching Amazon is not a
finding. Neil chose a score gate: the route fires only when the article scores
above zero on the investment axis. One rule, no hand-maintained event-type
list, and it reuses scoring already trusted elsewhere.

The risk in a score gate is that it kills the case the route exists for — a
funding round moves TeraWulf while tagging no semiconductor mechanism — so that
was checked before choosing rather than assumed: Anthropic's S-1 scores 40
through its `corporate_finance` event weight and survives. A test pins this
(`test_gate_does_not_block_the_route_it_exists_for`), and another pins that the
gate touches no other route. Result: 263 lab_exposure rows -> 84, total
connections 839 -> 660, and what remains at the top is model launches, compute
commitments and infrastructure builds — the items where a counterparty tie
genuinely matters.

**Alternatives rejected.** Pipeline writes DB directly (later refactor; loses
raw-first and free clone data). Alembic now (nothing to migrate). DB enums for
vocab (YAML is the vocabulary's home; validate.py the gate). ORM relationships
(FKs without relationship() keep models flat; explicit flush ordering instead).

**Deferred, one line each:** FastAPI read layer; alert notifier behind
`pipeline_runs.alerted_at`; fetch consuming the recorded watermarks; a
lab-sentiment axis in the classifier (would give lab_exposure/named a real
direction); dialect-specific bulk upserts; Railway/Render deploy config;
gold_snapshots auto-loader beyond the table.

## The first code review, and what it changed (2026-09-02)

Wrote a review subagent (`.claude/agents/bitcap-reviewer.md`) and pointed it at
the ETL commit. It wraps the existing `code-reviewer` skill — same severity
ladder and tone — and adds two sections a general reviewer cannot supply: the
CLAUDE.md contract as checkable items, and the hazards that have already bitten
this repo (sqlite hiding Postgres FK behaviour, missing version bumps, derived-
table drift, `max_tokens` truncation as silent data loss). It reports and never
edits: a reviewer that patches its own findings stops being an independent
check. Eight findings, no criticals, all verified against the committed data
before being accepted. Three were worth acting on immediately.

**A load committed the destruction of the derived layer before rebuilding it.**
`load_refs` opens by deleting `connections`, the tag tables, `classifications`
and `articles` — correct, since all four are derived — and then committed. Every
later stage committed separately, so nothing spanned the wipe and the rebuild:
one bad payload in `transform` and the database was left *empty*, not stale.
Fixed by making every stage flush and giving `tracked` the single commit, so a
failure rolls the wipe back with it. `stats` is now filled stage by stage and
recorded on the failure path too — a run that dies in `transform` says so
instead of reporting `{}`. The regression test was checked against the old code
first: it fails there and passes here, which is the only way to know a test for
a fixed bug is worth keeping.

**The `named` route was matching custodian strings.** `Holding.name` was loaded
from `holdings.yaml`, whose names come from the fund's Vermoegensaufstellung and
are mangled — "FT Inter Inc. Reg. Shares Cl. Ao. N.", "Taiwan Semiconduct.
Manufact.". Eight of 26 could never match prose. `companies.yaml` already
carried the legal name *and* seven tickers `holdings.yaml` was missing, so the
fix was to load the naming authority from the right file rather than to invent
an alias for every position; the custodian string is kept as `custodian_name`
for the tie back to the fund document. Four companies still need an `aliases:`
list (Amazon → AWS, TSMC, Besi, SQM), which is config, not code. Deliberately
*not* aliased: "Figure" (a common word) and "Hyperliquid" (the protocol, not the
treasury vehicle holding it) — both would be entity-resolution collisions, a
named failure mode, and the restraint matters more than the coverage.

Result: the named route goes 7 → 53, and Amazon — a holding with an Anthropic
lab edge — becomes reachable at all (43 via `AWS`, 3 via `Amazon`, 0 via the
registered "Amazon.com"). The old figure had already been written up here as
"all seven named are NVIDIA, which is the only holding labs actually name."
That was a fact about the matcher, not about the corpus, and it has been
corrected above. A silent extractor bug that reaches the design document as a
finding about the world is the exact failure this project is meant to catch.

**`strength` was four incomparable scales in one column.** The mechanism route
multiplies two quoted, signed sides; `named` was a hardcoded 1.0 with no quote,
no reason and no `why`. 123 of 660 rows sat at exactly 1.0, and the README's own
example sorts on the column. Added `join.route_ceiling` to `scoring.yaml` —
mechanism 1.0, category and lab_exposure 0.6, named 0.3 — so each route is
capped at what it can actually prove. Judgement, not measurement, which is
precisely why it belongs in config where it can be argued.

**Also fixed:** an in-run duplicate-URL guard in the raw loaders (`load_costs`
already had the pattern; the other two would have raised `IntegrityError` and,
before the transaction fix, emptied the database); `pipeline_runs.kind` now
records `rebuild` distinctly from `load` instead of writing `"load"` for both;
`_load_env` no longer imports the research LLM providers just to run
`bitcap-db status`; and the wheel force-includes `config/` and `research/docs/`,
which it reads at runtime but did not ship.

**Not fixed, deliberately.** The reviewer proposed enforcing its own "never
mutate the database" rule with a `permissions.deny` entry. A project-level deny
would also block Neil and the README's documented workflow, so the honest
position is that the tools list enforces "never edits code" and the database
rule is prose — stated as a limitation in the definition rather than papered
over with a rule that blocks the wrong people. `aliases:` for IREN's former
name ("Iris Energy") is left out pending a source, per the no-unsourced-claims
rule.

## D10 — Scoring runs in batch mode and is off by default

**Decision.** `score_announcements.py` gained a `--batch` path using the
Anthropic Message Batches API alongside the existing interactive
(`ThreadPoolExecutor`) path, and a `SCORING_ENABLED` env-var gate that makes
`main()` a no-op — printing why, not silently exiting — unless
`SCORING_ENABLED` is truthy or `--force` is passed.

**Rationale.** Two separate asks, addressed together because they touch the
same call site. Batch: planning.md §4a already named the Batches API as the
right mode for non-interactive work — half price, latency irrelevant when
nobody is waiting synchronously on the result. Gate: the register expansion
in progress (Google DeepMind, Mistral, xAI — see D7) is scoped to
**extraction only** for now — raw announcements, papers/bylines, GitHub
contributors landing in the database — not classification. Nothing schedules
`score_announcements.py` yet, so the gate is currently redundant with "nobody
calls it" — but it makes the deactivated state explicit, visible, and
documented rather than an implicit property of what hasn't been wired up.
When a scheduler is built, it inherits an already-off switch by construction
instead of needing scoring left out of its own logic.

**Consequence.** `call_cost()` takes a `mode` parameter (`"interactive"` |
`"batch"`) and halves the computed price for `"batch"` — applied after the
existing cache-write/cache-read multipliers, not instead of them. Every cost
row now carries `mode`, so a future cost dashboard can't average the two
rates together without the split being visible in the data itself. Batch
`custom_id`s are a SHA-1 of the article URL, not the truncated cache-key
slug already used for on-disk caching — two distinct URLs can share a
truncated slug, and `custom_id` is the only thing `batches.results()` uses
to route an answer back to its article; a collision there would silently
misattribute a classification, which the existing on-disk cache key was
never exposed to since it's a full filename, not a wire identifier. Tests:
`tests/test_announcements.py::TestCallCostBatchMode`,
`::TestScoringEnabledGate`.

## D11 — Register expanded to Google DeepMind and Mistral; a pre-existing register/DB reconciliation gap surfaced along the way

**Decision.** Added `google-deepmind` (sitemap method, verified live) and
`mistral` (RSS method, verified live) to `config/sources.yaml` — both
config-only, no new discovery method needed. `fetch_announcements.py` was
run live end-to-end and confirmed idempotent (second run in ~2s, all-cache
hit, identical counts). One real code fix went in alongside: `from_rss` was
labelling every RSS-discovered item `text_source` however the config said,
regardless of what was actually fetched — the function never fetched the
linked page at all, so a lab configured `full_text` over RSS would have
silently shipped title+summary text mislabelled as a full article. `from_rss`
now fetches and strips the linked page when `text_source: full_text` is
configured, and downgrades a single item to `rss_summary` on a per-item fetch
failure rather than crashing the run or mislabelling the fallback.

**xAI is not in this batch.** Live `x.ai` is Cloudflare-blocked on every
path including `sitemap.xml` itself (see D7 register, still pending its own
`wayback_sitemap` discovery method) — deliberately sequenced last, per the
approved plan's "prove n=1 on the safe cases first."

**Surfaced, not fixed: `app/`'s DB reconciliation tests assumed a closed
world that the register additions broke open.** `tests/test_pipeline_db.py`
compares the live DB (loaded from `research/docs/announcements.json`, a
rolling `window_months: 3` window) against
`research/docs/scored_announcements_v7.json` (a point-in-time scoring
snapshot). Before this session, register size and window contents happened
to make `len(DB rows) == len(register)` true; after loading the two new
labs' raw articles (unscored, per D10's extraction-only scope) that ceased
to hold, and a check of the raw numbers by hand showed 16 URLs are missing
**from the DB entirely** relative to the register — pre-existing time-window
rollover between when the register snapshot was generated and now, present
regardless of anything this session touched. Changed the two affected tests
(`test_scores_reconcile_with_register`, `test_tag_rows_match_register_totals`)
from asserting equal set *size* to reconciling only the shared URLs, which is
what their own docstrings already claimed to check ("if the DB and the
register ever say different numbers about the same article"). Mechanical
count updates elsewhere (`ref_labs`, article/classification counts,
watermark set) are a direct, expected consequence of the register growing
and are not signs of anything wrong.

**Consequence.** The underlying staleness — a scoring snapshot that silently
drifts out of sync with the rolling raw-article window — is not resolved,
only stopped from masquerading as a false test failure. It will recur and
grow as more time passes with `SCORING_ENABLED` off. Whoever re-enables
scoring should regenerate `scored_announcements_v7.json` against the current
window rather than trusting the old snapshot.

## D12 — GitHub register extended to Google DeepMind, Mistral, xAI; a new `confirmed_org_wide` evidence tier

**Decision.** `aggregate_github.py`'s hardcoded `LABS` dict moved to
`config/github_sources.yaml` (fixing a pre-existing "adding a lab is a
config change" violation for the two labs it already covered), and gained a
`domain_shared` field: true when a commit-email domain belongs to the
parent company rather than the lab itself. A `domain_shared` match now tags
`confirmed_org_wide`, not `confirmed` — a fourth employment tier alongside
the existing three, never conflated with them in the totals breakdown or
the console legend (the two share a first letter, so the legend needed an
explicit code table rather than `employment[0]`).

**Why it's needed, with real numbers.** Ran live against all three new
orgs. `google-deepmind`: 168 repos, 9,870 commits, 632 people, **221 tagged
`confirmed_org_wide`** via `@google.com` — Alphabet-wide, not
DeepMind-specific, so calling this `confirmed` the way `@anthropic.com` or
`@openai.com` are would silently overstate the evidence. `mistralai`: 0
people evidenced by email at all — recent commits are almost universally
GitHub's privacy-relay noreply addresses; a real finding about the org, not
a harvesting gap. `xai-org`: 12 of 33 people confirmed via a genuinely
lab-specific `@x.ai` domain, still in use as of Sept 2026 despite the Feb
2026 SpaceX acquisition — the cleanest signal of the three.

**Bot list gained one entry from real data.** `copybara-github` (Google's
internal source-sync tool) ranked top-10 by commit volume on the DeepMind
harvest (199 commits, 11 repos, display name "Copybara-Service") and wasn't
caught by the existing `[bot]`/`-bot`/`stainless` patterns. Added to
`BOT_LOGINS` by name, not generalized into a new pattern — one
Google-specific tool doesn't imply a rule that should catch others by
resemblance.

**Not done: DeepSeek's GitHub org.** Still "not yet attempted" per the
README, unchanged by this pass — out of scope for the current register
work, which targeted the labs D7 added (DeepMind, Mistral, xAI), not the
existing gap on DeepSeek. Left for a future explicit ask.

## D13 — Papers leg extended to Google DeepMind, on a generalized v2 extractor

**Decision.** Built `research/papers/deepmind_harvest.py` against the
generalized `prompts/byline_extraction/v2.md` (see D-adjacent prompt diff
note in that file) rather than a third bespoke deterministic parser. No
deterministic path is feasible for DeepMind — confirmed live, its arXiv
HTML carries no reliable affiliation markup. Corpus enumeration reuses
`deepmind.google/sitemap.xml`, already fetched for the announcements leg:
it lists all 262 current publication pages with a `<lastmod>`, which
sidesteps the publications *listing* page being JS-rendered — no headless
browser or rendering step needed.

**Real run: 17 candidate papers (3-month window), 15 processed, 65 distinct
authors, $0.86.** Two skipped as genuine, honestly-recorded source-fetch
failures (one links to PLOS ONE rather than arXiv/OpenReview; one arXiv id
has no `/html/` rendering). Hand-checked the largest result: a 21-author
white paper where only one author was tagged DeepMind staff, correctly —
the other 20 are named collaborators from AIST, Oxford's VGG, OpenAI,
Cambridge and eight further institutions, and the model attributed every
one of those affiliations correctly rather than assuming DeepMind
authorship from appearing on DeepMind's own publications page.

**Two real bugs, both found by hand-checking output, neither caught by a
test written in advance.**
1. DeepMind renders HTML attributes unquoted wherever the value has no
   whitespace (`href=https://...`, valid HTML5, not what a
   `href="..."`-only regex expects) — the first proving run (`--limit 3`)
   returned 0 authors on 3 of 3 papers before this was caught by reading
   the raw cached page. Fixed by also trying the page's own JSON-LD
   `sameAs` field first (normal JSON quoting, more robust regardless), with
   the quote-flexible href regex as a second path.
2. `fetch()` had no retry/backoff. A transient 403 from arXiv mid-batch
   crashed the entire run rather than being one paper's problem. Fixed to
   match the existing pattern in `fetch_announcements.py` (exponential
   backoff, 3 attempts), and source-fetch failures are now recorded and
   skipped exactly like LLM extraction failures already were — matching
   CLAUDE.md's named failure mode "source fetch failure and partial runs."

**Idempotent by construction.** Extraction results are cached per-paper URL
(`deepmind_extraction_cache.json`) separately from the raw-page disk cache,
so re-running the script — including after fixing bug (1) mid-session —
never re-bills a paper whose byline was already extracted successfully.

**Not done: Mistral's papers leg, xAI's `wayback_sitemap` announcements
method.** Both remain from the approved plan's sequencing; DeepMind was the
only one of the three new labs with enough real paper volume to build and
prove the extractor against this pass (Mistral: ~5-7 total papers
identified via title search, none in-window; xAI: zero papers found under
any method tried).

## D14 — D7 amended: Meta AI added as a seventh deep-coverage lab

**Decision.** Meta AI joins the deep-coverage register alongside D7's
original six (Anthropic, OpenAI, Google DeepMind, DeepSeek, Mistral,
xAI/SpaceXAI). Neil's call, made directly, overriding D7 rather than
re-deriving it from the transmission-path test D7 used to pick the
original six.

**Rationale, as given.**
- Meta AI is simply a large, consequential lab — size alone is a reason to
  track it that D7's transmission-path test doesn't fully capture.
- It has published a genuine mix of open-weight and closed models, and is
  now visibly moving from open toward closed — already sourced in
  `research/labs/frontier_labs.md`'s Meta AI entry: "Reporting describes a
  shift away from open weights toward proprietary models," and flagged
  there as "directly relevant to the `open_weights_release` mechanism: the
  largest Western open-weights publisher stepping back." That shift is
  itself the kind of register-worthy event D7 was built to catch — not a
  single model release, but a change in *posture* with mechanism-level
  consequences (Llama's open weights are a standing input to several
  mechanisms; a step back from that changes the mechanism, not just the
  model).
- D7 already surfaces evidence in Meta's favor and didn't act on it: its
  own event table cites a Meta-driven market move ("Report Meta may adopt
  Google TPUs" → NVDA −4.3%, ~$243bn erased) that stopped short of
  admission because the *repeatable* mechanism D7 selected on is the
  compute contract, and Meta hadn't been the named party in one. This
  amendment doesn't relitigate that test — it adds a lab on a basis D7
  deliberately didn't use (size, and a structural posture shift), which is
  exactly what an override is for.

**Standing policy, stated explicitly so it doesn't need re-deriving next
time:** the register is expected to keep growing as the pipeline matures.
D7's "six is affordable, 21 is not" reasoning was about person-level
coverage cost at the time, not a ceiling. Adding a lab remains what D7
already established it should be — a deliberate, recorded call — not
something that requires re-running the whole transmission-path test from
scratch each time.

**Consequence — what this does and does not change.** Meta AI's factual
entry in `research/labs/frontier_labs.md` already exists and is sourced;
no change needed there beyond this register-status note. It is **not**
yet in `config/sources.yaml`: unlike DeepMind and Mistral (config-only
additions, both live and verified this session), Meta AI's announcements
channel needs a discovery method that doesn't exist yet in
`fetch_announcements.py` — its site blocks the sitemap to all but named
crawler user-agents, has no RSS feed, and needs a paginated-listing scrape
plus a UA-override (confirmed live earlier this session: a plain fetch
with no custom User-Agent succeeds; any browser-style UA gets a 400 —
the opposite of every other lab configured so far). That is a genuinely
new piece of code, not a config edit, and building it is a separate task
from this decision.

**Update — the separate task above is now done. See D15.**

## D15 — Meta AI's GitHub and announcements legs built and run live

**Decision.** Built the two legs D14 deferred: GitHub (config-only
extension, plus one real code change to support a lab with more than one
corporate domain) and announcements (a genuinely new discovery method,
`listing_pagination`, plus the UA-override already flagged in D14).
Papers is not attempted — Meta's own publications page returns a 500, and
the working alternative found in earlier discovery populates its arXiv
link via client-side JS with no href in server HTML, making enumeration
materially harder than DeepMind's sitemap-based path; not pursued this
pass.

**GitHub: two orgs, one `lab` id, a schema change.** `facebookresearch`
(general FAIR/research, 395 in-window repos, 10,943 commits, 736 people)
and `meta-llama` (Llama-specific, 8 repos, 351 commits, 28 people) both
map to `lab: meta-ai` in `config/github_sources.yaml`. A third candidate,
`meta-ai` itself, was checked and confirmed dead/squatted (0 repos) —
excluded. Both `@meta.com` and `@fb.com` are active corporate domains in
real recent commits (fb.com is legacy, not retired), so `aggregate()`'s
`org_domain` parameter now accepts a list as well as a single string,
matched by set membership rather than equality — a genuine, evidence-based
extension, not speculative generality. Both domains are Meta-wide, not
AI-org-specific, so both tag `confirmed_org_wide`: 347 of 736 at
`facebookresearch`, 16 of 28 at `meta-llama`.

**A second bot slipped through, same class of bug as D12's
`copybara-github`.** GitHub's own Copilot coding agent commits under the
resolved login `Copilot` (43 commits, 3 repos, `users.noreply.github.com`)
even though one of its own commit `name` fields is literally
`copilot-swe-agent[bot]` — uncaught by `BOT_PATTERNS` because the *login*
has no hyphen before "bot" and no bracket suffix. Added to `BOT_LOGINS` by
name, same reasoning as before: one account's resemblance to a pattern
doesn't justify generalizing the pattern itself.

**Announcements: 5 real articles, and three bugs caught by hand-checking
output before trusting it — none caught by a test written in advance.**
`from_listing_pagination` paginates `ai.meta.com/blog/?page=N` (no RSS, no
reachable sitemap) and sends no `User-Agent` override, per D14's finding
that a browser-style UA gets a 400 there.
1. The listing page links to itself (nav/logo/home link), which trivially
   contains the `/blog/` filter — the bare index URL was scraped as an
   "article," with a date lifted from its own featured-item text. First
   live run returned 6 "articles," one of them the index page itself, a
   1KB stub of nav chrome. Fixed by excluding `lab["index_url"]` from
   candidates explicitly.
2. Title extraction split the stripped body text on a separator (`" \ "`,
   `" | "`) that doesn't exist in this site's rendered text — titles came
   back with the full nav chrome appended (e.g. "Introducing Muse Spark
   1.1 Products AI Research Resources About AI Developers Try Meta AI
   Open Source..."). Fixed by reading the page's own `<title>` tag
   instead, same fix DeepMind's harvester already used.
3. That fix then returned **empty** titles on every single article — worse
   than the bug it replaced. Meta's `<title>` tags carry an `id` attribute
   (`<title id="pageTitle">...`), and an exact `<title>(.*?)</title>`
   match silently finds nothing against any attributed tag. Fixed the
   regex to `<title[^>]*>` in both `fetch_announcements.py` and
   `deepmind_harvest.py` — the latter was never actually exercised against
   an attributed `<title>` tag before this, so it was an unconfirmed,
   equally-live bug there too, just not yet triggered.

Final verified state: 5 articles, June 29 – July 27 2026, `text_source:
full_text`, 3.6k-11k chars each, clean titles, correct dates. Tests added
for every one of the three bugs above, plus the multi-domain GitHub
change and pagination edge cases (empty page stop, out-of-window early
stop, `max_pages` hard cap, per-article fetch failure not aborting the
page). 393 tests pass.

**Consequence.** `config/sources.yaml`'s register is now six labs
configured (xAI's `wayback_sitemap` remains the one gap). No LLM cost on
this leg — announcements and GitHub are both zero-marginal-cost, unlike
the papers leg.

## D16 — Meta AI's papers leg, on title-based arXiv resolution instead of a direct link

**Decision.** D15 deferred papers because Meta's own publications page
500s and the working alternative (a paginated search endpoint,
`ai.meta.com/results/?content_types[0]=publication&years[0]=<YEAR>`)
populates its arXiv link via client-side JS — confirmed live, no href in
server HTML on any detail page checked. Built
`research/papers/meta_harvest.py` around resolving each Meta title to
arXiv externally rather than scraping a link that isn't there, after a
small proving run (5 papers) to check the resolution rate before
committing to the design: 4/5 resolved on an exact title match once
Meta's own `" | Research - AI at Meta"` site suffix is stripped; the 5th
needed a relaxed all-fields search, accepted only when the top candidate's
abstract shared enough vocabulary with Meta's own meta-description to
rule out a title collision (calibrated on two real papers: the correct
match scored 1.00 overlap both times, the closest false positive 0.19 —
threshold set at 0.3). Meta's detail pages also carry no date anywhere in
the server HTML, confirmed live — window filtering happens after arXiv
resolution, using the arXiv entry's own `published` date, not Meta's page.

**Real run: 27 candidate papers (2026, the only year the 3-month window
touches), 6 in-window after arXiv-date filtering, 5 processed (1 correctly
excluded as published outside the window once resolved), 41 distinct
authors, $0.43, 6/6 real candidates resolved — no unresolved papers in
this window.** Better than the proving run suggested, once two bugs below
were fixed.

**Two real bugs, both found by hand-checking output against the live
arXiv HTML, neither caught by a test written in advance — both in shared
code every papers leg depends on, not Meta-specific.**
1. `llm_byline.py::prepare_html`'s `HTML_BUDGET` (60,000 chars) cuts from
   byte 0 of the page. One real paper ("Reinforcement Learning for Code
   Optimization") came back with 0 authors despite a real, visible byline
   (Pierre Chambon, Kunhao Zheng, Juliette Decugis, ...) confirmed by hand
   in the raw HTML. Cause: arXiv's HTML template ships ~66KB of site
   chrome (search modal, "Report Issue" widget, license banner) before
   `<article>` even starts — past the entire budget, so the byline was
   never sent to the model, and it correctly reported `no_byline` on a
   page it had never actually seen. Fixed by starting the budget window at
   `<article>` when present. Checked live against every DeepMind paper
   already cached (D13) before trusting this as safe: all 7 of DeepMind's
   cached arXiv/OpenReview pages had `<article>` well inside the old
   budget, so this is additive, not a silent correction to D13's numbers —
   stated here because that question needed an answer, not an assumption.
2. `resolve_arxiv`'s relaxed-search fallback checked only `relaxed[0]`.
   For "AIRA$_2$: Overcoming Bottlenecks in AI Research Agents" (Meta's
   page renders the subscript as a Unicode glyph, `AIRA₂`, that doesn't
   text-match arXiv's plain `AIRA_2`), the correct paper scored 1.00
   overlap but ranked *second* in arXiv's own relevance ordering, behind
   an unrelated risk-audit paper that only shared the "AIRA" acronym
   (0.11 overlap). Checking only the top-ranked result missed a paper that
   was sitting right there in the candidate list. Fixed to score every
   candidate and take the best, not the first.

**Idempotent by construction**, same pattern as D13: extraction keyed by
Meta URL (`meta_extraction_cache.json`), unresolved candidates recorded to
`meta_unresolved.json` with a reason rather than silently dropped (empty
in this run, since all 6 resolved) — the project's "no citation, no
insight" non-negotiable meant a paper that fails both resolution passes
is excluded, not force-fit to the nearest-sounding arXiv result.

**Not done:** the earlier discovery pass's full historical corpus (~2,400
papers across Meta's history, unsorted, would need per-year pagination
back through most of the site's lifetime) — this pass covers the
configured 3-month window only, same scope as every other lab's papers
leg.

## D17 — Mistral's papers leg (title-list-from-announcements, not a maintained list), xAI's ruled out again, arXiv resolution extracted to a shared module

**Decision.** Built `research/papers/mistral_harvest.py`. Re-checked
xAI live first and confirmed there is still nothing to build for it.

**xAI: reconfirmed zero papers, not attempted again.** `x.ai/research`
and `x.ai/sitemap.xml` are still fully Cloudflare-blocked (403, live).
arXiv search for `au:xAI`, `all:"x.ai"`, `all:xAI`, `ti:Grok`, `all:Grok`,
`ti:"Grok 4"` returns either nothing or acronym/word collisions (XAI =
eXplainable AI dominates every "xAI" query; "Grok" collides with
"grokking," an unrelated ML phenomenon) and third-party papers
*evaluating* Grok, never one *published by* xAI. Matches D7's own
rationale (xAI's register-worthy events are compute contracts, not
papers) and D15's earlier finding. No code written — a harvester with a
guaranteed-empty corpus is not a leg, it's a stub, and CLAUDE.md's
"no features beyond what was asked" applies to building things that
produce nothing as much as it applies to anything else.

**Resolution logic extracted to `arxiv_resolve.py`, shared with
`meta_harvest.py`.** Mistral needs the identical title-based arXiv
resolution D16 built for Meta, and that logic had already needed two real
bug fixes there. Duplicating ~100 lines of matching logic into a second
file would mean applying every future fix twice; extracted instead, with
`meta_harvest.py` updated to import from it and its own copies removed.
Re-ran Meta's harvest afterward against the real cache to confirm
identical output post-refactor (same 5 papers, 41 authors, zero
re-billing) before trusting the extraction.

**Discovery: Mistral's own announcements, not a maintained model-name
list.** Mistral has no publications listing page at all (every path
tried 404s), and no arXiv query enumerates its output (`all:Mistral`
collides with the "MISTRAL" acronym in unrelated astronomy/plasma-physics
papers; arXiv has no affiliation-search field at all — confirmed live
against its own API docs, see the discussion earlier this session). The
plan going into this session was a manually maintained list of known
model names (`Mistral 7B`, `Mixtral`, `Pixtral`, `Magistral`, `Voxtral`,
`Devstral`), the same shape as `deepseek_harvest.py`'s `EXTRA_TITLES`. It
was already stale before being used: live-checking Mistral's own recent
announcements (already fetched for the announcements leg, zero extra
cost) surfaced two real 2026 model launches — "Shieldstral" and
"Robostral Navigate" — that were not on that list and both turned out to
have real arXiv papers. Built on the announcements corpus instead:
every in-window Mistral announcement title is a resolution candidate,
over-generating on purpose (most are product posts, not model releases —
7 of 9 in the real window correctly resolved to nothing) and letting
`arxiv_resolve.resolve_title` be the actual filter, same design as
Meta's. This also means the leg needs no separate maintenance as Mistral
ships new models — the announcements leg (already scheduled) is the
discovery source.

**No disambiguation signal, so relaxed matching is effectively disabled
here.** Meta's detail pages have a real meta-description usable for
`resolve_title`'s relaxed-match overlap check; Mistral's announcement
pages don't produce anything comparable (`text` is the full page
including nav chrome, not a summary). `mistral_harvest.py` passes `desc=""`
to `resolve_title`, which makes the relaxed fallback always score 0.0
overlap and never accept — every paper here is either an exact title
match or unresolved, nothing guessed at from a weak signal. Both real
papers in the current window resolve on exact match, so this hasn't cost
anything yet; recorded as a real, load-bearing limitation, not
discovered-then-hidden, in case a future title diverges from arXiv's the
way Meta's did.

**Third real HTML_BUDGET bug, found the same way as the first two: by
hand-checking output, not by a test written in advance.** Both real
Mistral papers came back with 0 authors despite real bylines confirmed by
hand in the raw arXiv HTML. Cause: unlike DeepMind's and Meta's papers,
whose byline sits in a `<div class="ltx_authors">` right after the title,
Mistral's papers carry their contributor list as a plain "Contributors"
section heading positioned 70KB-135KB into the document — not near the
top (D16's `<article>` fix didn't help) and not near the end either (a
references/bibliography section follows it, so a blind head+tail split,
tried first, grabbed the bibliography instead and still returned 0
authors). Fixed `llm_byline.py::prepare_html` to locate a
"Contributors"/"Author List" heading (the same shape
`deepseek_harvest.py::parse_author_list` already special-cases,
there via an unbounded regex search rather than an LLM budget) and window
around it, stopping at the next heading or section close rather than
grabbing a flat `tail_budget` — the first version of this fix still
overran into the references section and produced truncated, invalid JSON
on the larger paper. Checked live that DeepMind's and Meta's already-cached
pages are unaffected (their byline sits in the first few hundred
characters of `<article>`, inside `head_budget` regardless).

One more finding surfaced by this fix, not a bug: Shieldstral's paper has
*two* adjacent sections, "Core contributors" (11 names, specific to this
paper) and "Contributors" (400+ names, an apparent company-wide
boilerplate list). The heading search matches "Core contributors" first
(it appears earlier in the document) and correctly stops there — which
also happens to be the higher-signal answer, not a workaround for it.

**Real run: 9 in-window Mistral announcements, 2 resolved to real papers
(Shieldstral, Robostral Navigate), 7 correctly unresolved, 23 distinct
authors, $0.09.** Hand-checked both author lists against the raw arXiv
HTML directly — exact match, not approximately-right.

**Fourth bug, in cost instrumentation, found while chasing the third.** One
of the failed attempts above billed the API, then failed to parse the
model's JSON — and `llm_byline.py::extract()` built the cost record before
parsing, but only returned it alongside a successfully parsed result, so
the `JSONDecodeError` silently lost it. Directly against CLAUDE.md's "cost
is instrumented at the call site... cannot be reconstructed later," so
fixed rather than left as a disclosed gap: `extract()` now raises
`ExtractionError(message, cost)` on a parse failure, and all three papers
harvesters (`meta_harvest.py`, `mistral_harvest.py`, `deepmind_harvest.py`)
record that cost before treating the paper as unresolved. Exact spend
figures for the debugging arc that found this are in
[`docs/cost.md`](cost.md).

**Consequence.** `docs/planning.md`'s papers-leg register now covers
Anthropic, OpenAI, DeepSeek (pre-existing), DeepMind (D13), Meta AI (D16)
and Mistral (this decision) — six of the seven deep-coverage labs. xAI is
the one gap, and it is a gap in the source material, not the harness:
confirmed twice now that the lab publishes nothing findable by any method
tried.

## D18 — Mistral's GitHub leg refreshed; two more bots caught, one uncatchable by login pattern

**Decision.** Re-ran `harvest_github.py`/`aggregate_github.py` against
`mistralai` (D12 built this leg originally) after clearing its per-repo
disk cache, rather than trusting the day-old cached numbers as still
current — same live-over-stale-data discipline used throughout this
session. Confirmed no second Mistral-affiliated org exists to add (the
Meta AI pattern from D15): `mistral-ai` exists on GitHub but has 0 public
repos, dead/squatted, same as `meta-ai` was for Meta.

**Numbers essentially unchanged from D12** (1 day apart, expected): 12
repos, 1366 commits, same repo list. `D12`'s core finding stands exactly:
0 people evidenced via corporate email, 93 (was 95, see below) contributors
all `unknown` tier — almost every commit uses GitHub's noreply-relay
address, not `@mistral.ai`.

**Two more bots caught while hand-checking the "top 30" list, same class
of bug as D12's `copybara-github` and D15's `copilot` — logins that don't
match `BOT_PATTERNS`' hyphen/bracket assumptions.**
1. `speakeasybot` (Speakeasy's SDK-generation automation, 20 commits/2
   repos) — confirmed live via its own GitHub profile ("Speakeasy Bot",
   bio "I'm a helpful bot that automates Speakeasy operations"). Login
   ends in "bot" with no hyphen before it, so `-bot$` doesn't match.
2. `maiengineering` (Mistral's own CI automation, 50 commits/4 repos) —
   caught by a different signal entirely, since the login itself gives no
   hint of being a bot at all: a blank profile (no name, bio, or
   company), a role-based team mailbox (`engineering@mistral.ai`, not a
   personal address), and half its commits carry the raw commit `name`
   field "Buildkite CI" rather than a person's name. Found by checking
   commit-level `name`/`email` fields directly, not the login string —
   the first bot this session that no login-pattern generalization would
   ever have caught.

Both added to `BOT_LOGINS` by exact name, same policy as every prior bot
fix this session: a specific found account, not a generalized pattern
that might catch (or miss) others by resemblance.

**Consequence.** People count corrected 95 → 93 (both bots removed), bot
commit count corrected 39 → 109. Employment tiers unaffected — removing
bots doesn't create email evidence for the remaining humans, so
`confirmed`/`confirmed_org_wide` both stay at 0, consistent with D12.

**Same check re-run against `xai-org`, no additional bots found.** Cache
cleared and re-harvested live: 8 repos, 307 commits (306 previously, one
new commit since last harvest — a real, small change, unlike Mistral's
exact match). Checked every commit's `login`/`name`/`email` by hand, not
just the top-30 display, the same way `maiengineering` was found for
Mistral: `grokkybara[bot]` (42 commits) and `github-actions[bot]` (24)
are both already caught by `BOT_PATTERNS`' `\[bot\]$`, and 21 commits
under an unresolved login with commit name "CI agent" and email
`support@x.ai` are already correctly bucketed as `unattributed`, not
miscounted as a person. 66 bot + 21 unattributed + 33 real people
accounts for all 307 commits exactly — no gap. `confirmed: 12` unchanged.
No second xAI-affiliated org exists to add either (`xai`, `x-ai`,
`spacexai`, `xai-labs`, `grok`, `xaicorp` all checked live, none exist).

**Same check re-run against `google-deepmind`, the largest source by far
(168 repos), no additional bots found.** Cache cleared and re-harvested
live: 168 repos, 9,883 commits (9,870 previously — 13 new since the last
harvest). Full commit-level scan (login, name, email — not just the
top-30 display) across all 638 distinct logins found nothing beyond the
5 bot logins already caught: `copybara-github` (199 commits, via
`BOT_LOGINS`) and `dependabot[bot]`/`google-labs-jules[bot]`/
`copybara-service[bot]`/`github-actions[bot]` (108 combined, via the
`[bot]` bracket pattern). Bot (311) + unattributed (881, commits with no
resolvable GitHub login at all — a mix of real staff committing under
unlinked emails and library-release identities like `RLaxDev`,
`DistraxDev`, `KfacJaxDev`; correctly excluded from the people count
either way since none has a login) + real people (632, commits 8,691)
accounts for all 9,883 commits exactly. `confirmed_org_wide: 221`
unchanged. No second DeepMind org exists either: `deepmind` itself is
confirmed dead (0 repos, redirect stub, matches the original discovery);
`google-research`/`google-research-datasets` are real and large but are
Google-wide research orgs, not DeepMind-specific — the same Alphabet-vs-
DeepMind distinction that makes `@google.com` `confirmed_org_wide` and
not `confirmed` applies to not adding them here either.

**Same check re-run against `openai`, one more bot caught.** Cache
cleared and re-harvested live: 99 repos, 19,964 commits (19,705
previously — real growth). Full commit-level scan found one new leak:
`goreleaserbot` (1 commit, "GoReleaser Bot", bio "I'm a bot, do not @ me"
— confirmed live), same class of miss as `speakeasybot` (login ends in
"bot" with no hyphen). Added to `BOT_LOGINS`. One other flagged account,
`Traccia-Official` (1 commit, `support@traccia.ai`), checked and left
alone: a blank-but-plausible external business account contributing to
`openai-agents-js`, no bot indicator anywhere in its profile — correctly
`unknown` tier already, not force-excluded on suspicion alone. People
1150 → 1149, bot 2485 → 2486, `confirmed: 428` unchanged. `openai-labs`
and `openai-community` (nonzero repos, worth checking) confirmed
unaffiliated: the former is entirely forks of other orgs' repos with a
`.edu.ge` blog domain, the latter an unrelated third-party API wrapper.

**Same check re-run against `anthropics`, no new bots found.** Cache
cleared and re-harvested live: 63 repos (61 after mirror exclusion,
unchanged: `OpenROAD-flow-scripts`, `claude-code-base-action`), 20,611
commits (16,896 previously — real growth, driven largely by one new
large repo, `claudes-c-compiler`). Full scan flagged two accounts,
both checked and left alone, not new leaks: `msegner-bot` (1 commit,
real `msegner@anthropic.com` staff email, but the login already matches
`BOT_PATTERNS`' `-bot$` and is correctly excluded — likely a personal
service-account identity, not a gap); `WeAreResilience` (2 commits to
`claude-code-action`, a real external software agency's account, no
bot indicators — correctly `unknown` tier). `confirmed: 178` (was 176,
consistent growth). No second Anthropic org exists: `Anthropic-AI` is
dead (0 repos), `claude-ai` is an unaffiliated joke repo, `anthropic-labs`
is entirely forks of other orgs' content, none original.

## D19 — DeepSeek's GitHub leg built for the first time, and a legitimate 97%-of-commits repo that looks exactly like an undetected mirror but isn't

**Decision.** DeepSeek's GitHub leg was explicitly left unbuilt at D12
("not yet attempted... left for a future explicit ask"). Built it now
following the same procedure as every other lab's leg this session: find
the real org live, rule out a second one, harvest, aggregate, hand-check
the top contributors at the commit level (login/name/email), not just
the top-30 display.

**Org: `deepseek-ai`, confirmed live, no second org.** `deepseek` itself
is a dead redirect stub (0 repos), same pattern as `deepmind`/`meta-ai`/
`mistral-ai`. `deepseek.com` is confirmed as a genuine, lab-specific,
actively-used commit-email domain (3,274 commits) with no shared-parent-
company ambiguity to resolve, unlike Google's or Meta's domains. No
work-handle convention found (`work_suffix: null`, same as Mistral) —
staff use personal-looking logins, not a `-ds` suffix or similar.

**A single repo, `deepseek-harness`, is 97.5% of the entire corpus and
initially looked exactly like the `OpenROAD-flow-scripts` mirror problem
(D-prior, `github/README.md`) — checked properly rather than assumed
either way.** Created 2026-08-13 (three weeks before this check),
14,981 of 15,359 total commits, 210,256 stars, 24,586 forks. GitHub's
API reports `fork: false`, `mirror_url: null` for it — exactly what
`OpenROAD-flow-scripts` also reported despite being a real mirror,
so those fields alone prove nothing either way (the known caveat: GitHub
only sets `fork: true` for repos created via its own Fork button, not a
manual clone-and-push). Distinguished by checking who actually committed
to it: 31 distinct contributors, top commit emails are `@deepseek.com`
(a real staff address, `jczhai@deepseek.com` among them) and Chinese
personal-email providers (`qq.com`, `sina.com`) consistent with a
DeepSeek-internal team, not the sprawling external-maintainer population
`OpenROAD-flow-scripts` had (43 EDA engineers with no lab connection).
Read as a genuinely fast-viral DeepSeek release — plausible given the
lab's history of high-profile launches (the same effect this project's
own DeepSeek-vs-NVIDIA calibration point is about) — not a mirror.
**Not excluded, but recorded in `config/github_sources.yaml`'s own notes**
so a future reader checking why one repo dominates the raw numbers finds
the reasoning already worked out, not a fresh investigation.

**Real run: 19 repos, 15,359 commits, 129 people, 12 confirmed (all
`@deepseek.com`), 11 bot commits (8 `dependabot[bot]`, 3 `Copilot`, both
already caught), 387 unattributed.** Full commit-level scan found zero
new bot leaks — the cleanest result of any lab checked this session.
Bot + unattributed + people-attributed commits (11 + 387 + 14,961)
accounts for all 15,359 exactly.

**Consequence.** All seven deep-coverage labs now have a GitHub leg:
Anthropic, OpenAI, DeepSeek (this decision), Google DeepMind, Mistral,
xAI, Meta AI. `research/github/README.md`'s "DeepSeek not yet attempted"
line is now stale and updated alongside this entry.

**Same check re-run against both Meta AI orgs, no new bots found —
completing the sweep across all seven labs.** Both caches cleared and
re-harvested live. `meta-llama`: 8 repos, 351 commits (unchanged exactly
— stable corpus), 28 people, `confirmed_org_wide: 16`, matching D15
exactly. `facebookresearch`: 395 repos (the largest single org checked
this session), 10,944 commits (10,943 previously), 734 people (was 736),
`confirmed_org_wide: 347`, matching D15 within noise. Full commit-level
scan on both found only `facebook-github-bot` ("Facebook Community Bot",
144 commits/24 repos on `facebookresearch`, a handful more on
`meta-llama`) — already caught by `BOT_PATTERNS`' `-bot$`, not a new
leak. Bot + unattributed + people-attributed commits accounts for the
full total exactly on both orgs (823 + 1,861 + 8,260 = 10,944 on
`facebookresearch`; 54 + 29 + 268 = 351 on `meta-llama`).

**One new org candidate found and excluded: `facebookincubator`
("Meta Incubator," 104 repos, genuinely real and active).** Checked its
repo list before deciding: predominantly general systems/infrastructure
engineering (C++ execution engines, Linux kernels, TLS implementations,
load balancers), not AI-research-specific — the same category `google-
research`/`google-research-datasets` were excluded from DeepMind's
register for (D-prior, this file). Including it would dilute the
`meta-ai` register with general engineering contributors unrelated to AI
research output, so it was left out, not added — the register boundary
is "AI-research-specific org," not "any org Meta owns."

With this, every one of the seven deep-coverage labs' GitHub legs has now
been re-verified live against fresh data in this session, not just
trusted from its original build.

## D20 — xAI's announcements leg built: Wayback CDX discovery, not sitemap-via-Wayback as originally planned

**Decision.** xAI's announcements leg was the one register gap left after
D18/D19's sweep — never built, blocked on live `x.ai` being fully
Cloudflare-blocked on every path including `sitemap.xml` itself. The
Internet Archive, which had been returning 503 earlier in this session
(the original motivation for planning.md §4b's retry-cadence note), was
re-checked live and confirmed back up. Built on that.

**The originally planned method doesn't work, found out by checking
before building, not after.** The plan was to fetch x.ai's own sitemap
through its archived copy on Wayback, then parse it exactly like
`from_sitemap` already does. Checked live first: `x.ai/sitemap.xml` has
not been re-archived since February 2026 — seven months stale relative
to today, meaning its URL list would miss every article actually inside
the 3-month announcements window. A sitemap that exists but is
structurally too old to help is a different failure mode from a sitemap
that doesn't exist at all, and it would have looked like a working
integration returning zero results, not an obvious break.

**What works instead: a direct CDX wildcard query on the article path**
(`x.ai/news*`), the same bulk-query technique `backfill_openai.py`
already uses for text recovery, here used for *discovery* itself rather
than recovering text for an already-known URL. This finds real, recent
article snapshots regardless of whether the sitemap itself was ever
re-crawled — confirmed live, articles from August 2026 were found this
way.

**A snapshot's crawl date has no relationship to the article's publish
date, and trusting it would have been a real, silent bug.** Confirmed
live: a 2024-dated funding-round page (`series-b`) was recrawled by the
Archive in August 2026 — completely ordinary Wayback behaviour (it
recrawls existing pages on its own schedule, unrelated to when the
underlying content changed), but if the crawl timestamp had been used as
the article's date, that 2024 page would have landed inside a June-2026
window. Every candidate is instead dated from its own JSON-LD
`datePublished` field (confirmed present and reliable across six real
pages spanning 2023–2026, from the original Grok announcement to
Composer 2.5), with the existing visible-text date regex as a fallback
for pages that lack it.

**Real run: 78 raw distinct article URLs discovered (broad, unbounded
check during investigation) narrowed by a 60-day-buffered CDX date bound
to avoid wasted fetches on years of evergreen pages; 35 landed inside the
real 3-month window** — Grok 4.5/4.6 releases, a dozen third-party
integrations (Amazon Bedrock, GitHub Copilot, Databricks, Interactive
Brokers, eToro), Grok Build tooling. Hand-checked several titles and dates
against the live announcement list — real, current, correctly dated.
Zero marginal cost: no LLM involved in this leg, same as GitHub's.

**Site branding is "SpaceXAI," rendered even on old pages' titles.**
Confirmed the `series-b` (2024) and `grok` (original 2023 announcement)
pages both render "SpaceXAI" in their `<title>` when crawled in August
2026 — a page's `<title>` is generated at crawl time from the site's
*current* branding, not frozen at original-publish-time branding. Both
"SpaceXAI" and the earlier "xAI" suffix are stripped from titles
unconditionally, not conditioned on article age.

**Consequence.** `config/sources.yaml` now configures all seven
deep-coverage labs for announcements — the register gap flagged at the
top of this file's earlier header comment is closed.
`tests/test_pipeline_db.py`'s pinned counts updated (201→236 articles,
watermark set gains `xai`) to match the real corpus, same recurring
pattern as every prior register addition this session.

## D21 — Independent code review (bitcap-reviewer) of this session's diff, all 9 findings fixed

**Decision.** Ran the `bitcap-reviewer` subagent against the real
committed diff (26 source/config/test/doc files, the ~1,280 auto-generated
cache/output files excluded from scope). Verdict: "Ship after fixes," 9
CONFIRMED findings, most-severe first. Fixed all 9 rather than triaging a
subset — none were speculative, each was traced end to end against real
code or real cached data before being reported.

**1. `arxiv_resolve.py` accepted a lone `ti:` hit as an exact match with no
similarity check at all.** arXiv's `ti:` field is a token match, not a
phrase match — the repo's own cached response for `ti:"HyperAgents"`
already proved this (an unrelated paper returned alongside the real one),
but the single-hit branch never checked it. Fixed by gating on the same
`overlap()` function already used for the relaxed pass, against
`OVERLAP_THRESHOLD`.

**2 & 3. `score_announcements.py`'s `run_batch` lost cost records on
failure, two distinct ways.** Cost was only written to disk after the
*entire* batch's results loop finished, so one malformed item partway
through a hundred-item batch would throw before any of it was persisted —
losing every already-billed item's cost, not just the failing one. And
refused/`max_tokens`-truncated items were billed by the API but skipped
`call_cost()` entirely before their `continue`. Fixed by moving cost
recording inside the loop (a `bill()` closure, same incremental-write
guarantee the interactive path's `record()` already had) and calling it
on every path that consumes real tokens, including the two failure paths.
Zero test coverage existed for `run_batch` before this — added 6 tests
using stub Anthropic Batches API objects.

**4. `llm_byline.py::prepare_html`'s head+tail window could duplicate the
author section.** When a contributor heading falls inside the first 30K
characters already sent as the head slice, starting the section window at
its own start re-sent that overlap — the model would see the same author
list twice, inflating appearance counts in every harvester's `aggregate()`.
Not live on either real Mistral paper (both headings sit well past
head_budget), but latent. Fixed with `start = max(section.start(),
head_budget)`.

**5. The `truncated` flag `prepare_html` already computed was silently
discarded by `extract_page`.** All three papers harvesters built this
session stored bylines with no record of whether the source page was cut
— a future paper whose contributor section falls outside the window would
look identical to a complete extraction. `extract_page` now returns a
3-tuple; each harvester's `Paper` dataclass gained a `truncated` field,
round-tripped through the extraction cache so a cache hit reports it
correctly too. Touched `deepmind_harvest.py`, `meta_harvest.py`,
`mistral_harvest.py`, and the superseded `harvest_contributors.py` (kept
functionally correct, not actively run).

**6. `extract()` never checked for `stop_reason == "max_tokens"`.** A
truncated-by-length response fell through to `json.loads()`, failed to
parse, and got reported as "malformed JSON from model" — true in effect,
hiding the actual fix. Added the same two-line check
`score_announcements.py`'s `classify()` already has for this exact
`stop_reason`.

**7. New config keys were unvalidated.** `sources.yaml`'s method-specific
keys (`page_param` for `listing_pagination`, etc.) were indexed directly
by each discovery function with no defensive `.get()` — a missing one
would `KeyError` deep into a live `collect()` run, after every earlier
lab had already finished, with no output written for any of them.
`github_sources.yaml` was not read by `validate.py` at all. Added
`check_sources()` (per-method required-key sets, mirroring `METHODS`) and
`check_github_sources()` (cross-references `lab:` against `sources.yaml`'s
real ids, validates `domain_shared` is a bool and `work_suffix` compiles)
to `config/validate.py`. New `tests/test_validate.py`, 11 tests including
"the real committed config passes with zero errors" for both.

**8. `aggregate_github.py` kept an Anthropic-only docstring and hardcoded
defaults after `LABS` moved to config.** `aggregate(org, org_domain=
"anthropic.com", work_suffix=r"[-_](ant|anthropic)$")` meant a caller that
omitted them — an orchestrator following `docs/handover.md` §7 partially,
or a new test — would silently score any org against Anthropic's domain
and return an all-`unknown` (or wrongly `confirmed`) column indistinguishable
from clean data. Made both required (no default); `domain_shared` keeps its
safe `False` default, which doesn't point at a specific wrong answer the
way the other two did. Updated the module docstring to describe the
per-org, config-driven reality instead of one hardcoded org. ~10 test call
sites in `test_aggregate_github.py` that relied on the old defaults now
pass Anthropic's values explicitly, or `NEVER_MATCHES` where a test
doesn't care about handle-suffix merging at all.

**9. Unused `import sys` in `meta_harvest.py`.** Dead weight, evidently
carried over from `deepmind_harvest.py` (which does need `sys` for a
`sys.path.insert`). Removed.

**Verified, not just applied.** Every harvester was re-run live against
its real cached data after the fixes (Meta: 5 papers/41 authors/$0.4327,
Mistral: 2 papers/23 authors/$0.0891, DeepMind: 15 papers/65 authors/
$0.8569, `aggregate_github.py anthropics`: 455 people/178 confirmed) —
identical to the pre-fix baseline in every case, confirming the fixes are
behavior-neutral for already-correct data and only change behavior on the
specific failure paths each one targets. Full test suite: 483 passed, 1
skipped (up from 456 before this pass — 27 new tests, one new file,
`tests/test_validate.py`).

## D22 — Research pages extended to every lab; the Anthropic-shaped prose they carried was asserting evidence that does not exist

**Decision.** Built the missing per-lab research pages (six GitHub orgs, three
papers registers) plus a cross-lab survey, `research/corpus_survey.html`. The
work was supposed to be renderer-only. It was not: both builders had been
written against Anthropic and hardcoded its assumptions, so pointing them at
another lab produced pages that were confidently wrong.

**1. `build_github_page.py` hardcoded a two-lab `LABS` dict, Anthropic's
`-ant` handle convention, and a mirror-exclusion sentence describing
Anthropic's two specific mirrors.** Run against Mistral — which evidences
*nobody* by commit email (D18) — it rendered "a commit made from an `@None`
address is direct evidence and outranks everything else (0 people)". Now reads
label, domain and convention from `config/github_sources.yaml` and generates
one of three different evidence paragraphs: lab-owned domain, parent-company
domain, or no domain at all. Where no work-handle convention exists it says so
rather than implying one was used.

**2. `enrich_github.py` predated the `confirmed_org_wide` tier and silently
skipped it.** `worth_fetching()` listed the evidenced tiers by hand, so a
DeepMind or Meta staffer evidenced by an `@google.com`/`@meta.com` commit but
below the two-commit threshold was never fetched — 8 of meta-llama's 16
evidenced staff. Its totals recount omitted the tier too, reporting it as 0.
Both fixed; `EVIDENCED` and `TIERS` are now named constants, and
`build_github_page.py`'s `EVIDENCE` dict would have raised `KeyError` on the
tier had the page ever been built for those orgs.

**3. The profile route recovers Mistral's register, which commit email could
not.** D18 recorded Mistral as evidencing zero people by email and treated that
as a real finding about the org rather than a harvesting gap. That still holds
— but running the profile pass identifies **22 people** by the profile company
field, with 9 blogs, 5 X handles and 322 personal repos among them. The finding
was right; the conclusion that Mistral was therefore unreachable was not. Same
pass on the other three small orgs: DeepSeek 12 → 23 identified, meta-llama 16
→ 21, xAI 12 (no gain; all already had email evidence).

**4. A claim written into this entry's first draft was wrong, and checking it
produced a better finding.** The draft said the three older registers "do not
record a primary source" because they lack `source_url`. They do record one —
under a different key. OpenAI and DeepSeek put the arXiv abstract URL in `url`
and also keep `arxiv_id`; Anthropic's `url` is the publishing venue itself
(`alignment.anthropic.com`, `transformer-circuits.pub`), which is the primary
source for work it never puts on arXiv, not a weaker substitute. **Citation
coverage is 54/54, 7/7, 8/8, 15/15, 5/5, 2/2 — every paper in every register.**
The real cost is that three key shapes must be normalised by anything reading
across registers, done once in `build_corpus_survey.primary_source()` and
asserted per-register in the tests so a future drop below full coverage fails
loudly.

**Alternative rejected: build the new pages from a copy of the Anthropic
builder.** Three near-identical scripts diverging on the exact prose that is
wrong for two of the three labs. The papers side does use one builder for
DeepMind/Meta/Mistral, but only because those three genuinely share a register
schema; the older three do not, and are left on their own builders rather than
forced into a shared one.

**Alternative rejected: merge the affiliation strings in the survey's
candidate-source table.** "MATS", "MATS Program" and "ML Alignment and Theory
Scholars" are one organisation counted three times. Merging needs an alias map
— the same entity-resolution problem the people register already solves — and
guessing at it inline would bury the collision. The page states the counts are
a floor and names the collision instead.

**Consequence.** `config/github_sources.yaml` gains `label` and
`company_pattern` per org, so adding a lab stays a config change. Enrichment
was run for the four small orgs only (~156 profiles); `google-deepmind` and
`facebookresearch` are still unenriched — 568 evidenced staff whose channels
have not been fetched — which both their own pages and the survey state
explicitly rather than showing an empty column. No LLM cost: GitHub REST only.
`tests/test_research_pages.py` adds 61 tests, four load-bearing ones verified
by mutation to fail when the fix they cover is reverted.

## A category row loses to a contradicting mechanism row on the same holding (2026-09-02)

Real case, surfaced by Neil while using the frontend: OpenAI shipping its own
inference accelerator tags `category: accelerator_custom_si` positive (good for
the group) and, via NVIDIA's own `custom_silicon_substitution` mechanism edge,
`mechanism` negative for NVIDIA specifically (a socket it loses). Both routes
fired on the same article, so NVIDIA showed "chips ↑" and "chips ↓" side by
side — not wrong individually, but presented together it reads as the system
contradicting itself rather than as two different kinds of evidence.

**Decision.** In `app/connect.py`, `_drop_contradicted_category_rows` drops a
holding's category-route row when that same holding has a mechanism-route row
on the same article with the strictly opposite direction (positive vs
negative). `mixed` contradicts nothing, so it never suppresses. A holding with
mechanism rows on *both* sides (a genuinely mixed picture) still loses the
category row — its own mechanism evidence is more specific either way, so the
group-level claim adds nothing.

**Alternative rejected: keep both rows, let the frontend pick one.** Pushes a
data-quality decision into a rendering layer, and every future consumer of
`connections` (a second frontend, an alert digest, an export) would have to
reimplement the same rule or reproduce the same contradiction. The suppression
belongs where the join is computed, once.

**Alternative rejected: average or net the two directions.** A synthetic
"slightly positive" for NVIDIA would be worse than either input — it asserts a
number nobody's evidence actually supports, exactly the "arbitrary weighted sum
dressed up as a score" the scoring rule already refuses to do (`scoring.yaml`).

**Consequence.** `connect()`'s per-route counts are now post-suppression —
a category count can drop between runs with no config change if more articles
land in this shape. Category route only affected; `lab_exposure` and `named`
are hardcoded `mixed` and can't contradict anything. Tested in
`TestCategoryMechanismContradiction` (`tests/test_connect.py`): the NVIDIA
shape, agreement (both rows kept), no mechanism row (category row kept),
`mixed` mechanism (no suppression), and a holding with mechanism rows on both
sides (still suppressed).

**Also recorded here:** the frontend's Portfolio-impact panel groups
connections by holding instead of listing every route flat, and every
connection row now carries a resolved label (the mechanism/category name, or a
humanized lab-relationship/named-mention phrase) instead of the bare route
word — the flat, unlabeled list was unreadable on an article that hit a dozen
holdings across several routes each. `api/queries.py` computes the label and
the holding/article-side magnitude and confidence; `frontend/app/page.js`
groups and sorts. A row's reason text is shown only when it says something
Evidence doesn't already — true for `mechanism`/`lab_exposure`/`named` (their
`note` prefers the holding-specific `why`), not for `category` (which has no
holding-side text and would just repeat the tag's own reason).

## D22 — Migrations become necessary the moment the pipeline persists state (2026-09-03)

**Decision.** Adopt Alembic, and stop `bitcap-db rebuild` from dropping the
operational tables.

**Why now, and not before.** `app/db.py` said, correctly, that there were no
migrations *because the database is fully derived from committed files*: every
table was a pure function of `config/*.yaml` and `research/docs/*.json`, so
drop-and-reload was always safe and always cheap. That claim stops being true
with the scheduled pipeline. `source_state` (how many consecutive runs a source
has failed), `run_sources` (what each run did per source) and `alerts` (what was
raised, and whether it was delivered) record things that *happened*. No file
reproduces them. The argument for migrations here is not tidiness — it is that
`create_all` cannot alter a table that already holds data nobody can regenerate.

**The bug this exposed, already present.** `rebuild` dropped `pipeline_runs`
too. Every reload silently discarded the run history that
`status='failed' AND alerted_at IS NULL` — the standing query `app/runs.py` was
written around — depends on. Nothing errored and no table looked wrong, because
a freshly reloaded run history looks exactly like a young one. `drop_all` now
drops only the derived, reference and raw layers; `models.OPS_TABLES` names the
five that survive. Verified live: a `rebuild` against the working Postgres kept
runs [1] and [2] and appended [3].

**Alternative rejected: keep `create_all` and version the schema by hand.**
Workable while the schema is only ever created, not altered — which is exactly
what stops being true here. The first `ALTER` against a database holding real
run history would have to be written by hand anyway, without the safety of
`upgrade`/`downgrade` or a recorded history of what shape the deployed database
is actually in.

**Alternative rejected: a separate operational database.** Genuinely separates
derived-from-files from recorded-by-runs, and removes the need for migrations on
the main one. Rejected because the alerting and the run views need to join run
state against content (`connections` above a threshold, gold snapshots against
`prompt_version`), and a cross-database join is a worse problem than a migration
directory.

**Consequence.**
- `alembic/versions/0001` is the 19-table schema as it stood before this work,
  so an existing database is stamped and upgraded rather than rebuilt; `0002`
  adds the three ops tables. An existing deployment migrates with
  `alembic stamp 0001 && alembic upgrade head`.
- `app/db.ensure_schema()` replaces bare `create_all` in the CLI. A database
  Alembic has never seen (a fresh clone, or one built before this) is created
  and stamped `head`; anything else is upgraded. Without the stamp such a
  database would be permanently unmigratable — Alembic would try to create
  tables that already exist.
- `tests/test_migrations.py` asserts `upgrade head` and `create_all` produce an
  identical schema. That is the test that fails when a column is added to
  `models.py` without a migration, which is otherwise invisible: the suite and a
  fresh clone both keep passing on `create_all` while every deployed database
  drifts a column behind.
- `alembic/env.py` prefers a URL set explicitly on the `Config` over
  `DATABASE_URL`. Found by the tests, which without it ran their migrations
  against the developer's live Postgres instead of their own temp database.

## D23 — The orchestrator drives labs, not legs, and counts failures rather than backing off (2026-09-03)

**Decision.** One `Source` per (leg, independently-failing thing) — a lab for
announcements and papers, a GitHub org for github — driven through per-leg
adapters, with cross-run state per source. Two sub-decisions inside that are
worth recording separately.

**1. Lab granularity, not leg granularity.** `fetch_announcements.collect()`
loops over all seven labs in one call with no error handling between them, and
`sys.exit`s on an unknown method. Calling it from an orchestrator would mean one
Cloudflare-blocked lab costing six working ones — the exact failure the
orchestrator exists to prevent. The module already exposes its `METHODS`
dispatch table, so the adapter calls the per-lab discovery function directly and
`collect()` is left untouched for hand-running. Demonstrated live: with
Anthropic pointed at a non-existent host, the run returns 201 articles from six
labs and records one failure, where previously it would have returned nothing.

GitHub is keyed on the org login rather than the lab id, because Meta AI owns
two orgs and they fail independently. Harvest and aggregate run as one source,
not two: they are strictly sequential for a given org and neither is useful
alone, so splitting them would buy a cross-source dependency and nothing else.

**2. No cross-run backoff. Count, don't skip.** The obvious design — back a
failing source off exponentially across firings — was considered and rejected.
It contradicts what planning.md §4b actually concluded: a source down for one
firing and up for the next is *exactly* what the next run's own per-request
retries handle, and skipping it would convert a ten-minute outage into a
self-inflicted multi-hour one. The retries are cheap (every fetch is disk-cached)
and the failing case is rare. What the cross-run layer is genuinely for is
*counting*: `consecutive_failures` makes "down for N scheduled runs" a query
instead of a guess, which is what the `source_down` alert needs. `disabled` is
the kill switch for a source that should truly stop being called, and a human
sets it.

`consecutive_failures` resets to zero on any success, deliberately. The question
it answers is "is this source down *now*", not "how unreliable has it been" — a
source that fails every other run is a different problem, visible in
`run_sources`, and conflating them would make the alert fire on flakiness rather
than on outages.

**3. Ingestion commits per source; the ETL stays atomic.** `app.runs.tracked`
wraps the load in one transaction on purpose, so a mid-load failure leaves the
database stale rather than empty. Ingestion is the opposite case: a fetch that
succeeded is a fact about the world, and on the LLM legs it was paid for.
Rolling it back because a *later* source failed would discard real work and
re-spend money to redo it. So the orchestrator commits each source's state and
run row as it goes, and the ETL keeps its single transaction.

**Alternative rejected: rewrite the harvesters to a common interface.** Cleaner
on paper and wrong in practice. Their differences are real — the labs publish
differently (D13, D16, D17), and the signatures reflect that. Rewriting them
would risk reintroducing bugs each one encodes a fix for (the HTML-budget
windowing of D16/D17, D20's snapshot-vs-publish date trap) to buy uniformity the
adapter layer provides for ~40 lines. Nothing under `research/` was modified.

**Consequence.**
- `config/papers_sources.yaml` is new, and makes the papers register
  enumerable. Coverage used to be encoded in which files existed under
  `research/papers/`, which made xAI's *deliberate* absence (D16, reconfirmed
  D17) indistinguishable from work nobody had done. xAI is now an explicit
  `enabled: false` carrying its reason. `tests/test_registry.py` asserts every
  deep-coverage lab is either covered or explicitly ruled out in all three
  registers — the silent failure being a lab added to one register and not
  another, which ingests less than anyone thinks and looks like a decision.
- The registry tests also check that every declared module, entry function and
  argument list actually matches the real harvester signature. Config naming a
  function that does not exist would otherwise fail one lab at a time, in
  production.
- **Bug found by running it at n=1:** the GitHub adapter first read its
  aggregation arguments straight off the YAML, which passed `work_suffix: null`
  into `re` and raised for every org without a handle convention.
  `aggregate_github.load_labs()` is not a passthrough — it substitutes a
  deliberately never-matching pattern so a lab with no known convention yields
  zero alias merges instead of borrowing another lab's. The adapter now goes
  through it. Regression test in `tests/test_adapters.py`.
- `merge_announcements` merges the corpus by URL rather than rewriting it. The
  harvester writes the file from a full sweep; the orchestrator only has the
  sources that worked, so writing the same way would delete a failed lab's
  articles and present an outage as a shrinking corpus. Verified: two
  consecutive full runs produce `added 0, updated 0, cost $0` across all 236
  articles.

## D24 — A budget ceiling replaces the scoring off-switch, and the guard has to count in-flight calls (2026-09-03)

**Decision.** Bound LLM spend with a per-run and per-month ceiling in
`config/pipeline.yaml`, and let the scheduled run classify. D10's
`SCORING_ENABLED` switch stays, but as a manual kill switch rather than as the
safety mechanism.

**Why the switch had to go.** D10 turned scoring off because an uncontrolled
scoring run was genuinely dangerous against a EUR 100 budget, and that was the
right call with no other control available. But a pipeline whose most valuable
stage is permanently disabled is not a pipeline, and the cost of leaving it off
turned out to be larger than it looked: **61 articles across four labs — xAI,
DeepMind, Mistral, Meta AI — had been fetched and never classified.** The
register claimed seven labs; the product could see three. Classifying them cost
$1.08 and took connections from 612 to 809. A safety switch with no bounded
alternative had quietly become a coverage gap that looked like a finished
register.

Two ceilings, because they catch different things. `per_run_usd` catches a loop
or a prompt regression inside one firing; `per_month_usd` catches the slow leak
of thirty individually-reasonable runs. Month-to-date is read from `raw_costs`,
not assumed zero — seeded wrong, thirty "in-budget" runs spend thirty monthly
limits.

**On exceeding: abort the LLM stage, not the run.** Everything already ingested
still commits and the ETL still finishes. Failing the whole run would discard
fetches that already happened and, on the LLM legs, were already paid for.

**The bug this found, which is the interesting part.** The first live probe set
a deliberately tiny $0.08 ceiling to check the guard actually stopped a real run.
It stopped it — after spending **$0.53, 6.6x the ceiling**. The guard tested a
running total, and with 12 concurrent workers every worker saw a figure none of
its peers had yet contributed to; a dozen calls were already in flight when the
limit tripped, and a cancelled call is billed anyway. The overshoot is bounded by
`workers x per-call cost`, so at the real $3.00 ceiling it would have been ~14%
and almost certainly never noticed.

Fixed by making the guard predictive rather than reactive: `begin_call()` asks
whether *this* call would breach the ceiling counting everything in flight, and
`end_call()` settles it. The per-call estimate is seeded from the measured
$0.029 average and replaced by the run's own observed mean. Re-probed live at a
$0.20 ceiling: **$0.2112 spent, 5.6% over**. This is the argument for probing a
control at a value where its failure is visible — at a realistic ceiling the bug
was within noise.

**A second bug, same class as one already fixed.** `run()` writes the register
from the articles it was handed, so classifying only what is new rewrote a
236-article `scored_announcements_v7.json` down to the 61 rows that run touched.
Identical in shape to the announcements-corpus overwrite that `sink.py` already
guards against, and identically silent — the register simply becomes a record of
the last run instead of the corpus. `run()` now merges by URL. `failures` is
still this-run-only, deliberately: it describes the attempt, where `scored`
describes the corpus.

**Alternative rejected: estimate cost before each call and refuse ahead of
time.** Would remove the overshoot entirely, but requires trusting a token
estimate rather than the provider's own usage figures, which is the thing this
project's cost instrumentation exists to avoid. Reserving against a *measured*
running average keeps every recorded number provider-reported.

**Alternative rejected: run the scheduled classification in batch mode.** Half
price, and latency is irrelevant for a 48h digest. Rejected for now because
`run_batch` polls to completion, which turns a scheduled firing into an
open-ended wait; it stays available via `classification.batch` for backfills,
which is the shape planning.md §4a actually recommends it for.

**Consequence.**
- Drift monitoring now has a writer, and `gold_snapshots` its first row.
  First measurement: mechanism F1 **0.947** against a 0.80 floor, event-type
  agreement 0.833, $0.195 for six items.
- **Drift deliberately bypasses the cache**, and that is the whole design.
  `classify_one` returns a cached result keyed on the article URL, so a drift
  check reading through it would report perfect agreement forever — the one
  failure mode indistinguishable from success. It therefore costs money every
  run, which is why the sample is six fixed items rather than the corpus
  (planning.md §6a), chosen to span richly-tagged and zero-tag articles, both
  scoring axes, and two labs.
- The headline metric is **mechanism micro-F1**, not a composite. Under
  `scoring.yaml` an article with no mechanism tag scores zero by construction,
  so mechanism agreement is score agreement; blending axes would be the
  arbitrary weighted sum the scoring rule already refuses to be.
- Month-to-date lags until `bitcap-db load` copies the cost log into
  `raw_costs`. The worker loads on every firing, so the next run is always
  current; several classification runs *within* one firing would undercount.
- `score_announcements.main()` is now argparse plus a call to `run()`. One code
  path for the CLI and the scheduler, so the cache-warming rule and the locked
  cost-log write exist once. All 101 existing tests for that module still pass
  unchanged.

## D25 — Papers and GitHub become a register of people, and its design is a set of refusals (2026-09-03)

**Decision.** Papers and GitHub land in Postgres as `people`,
`person_identity`, `person_evidence` and `unresolved_items` — a register of
*who works at a lab and since when* — not as a second insight stream with its
own scoring and holdings join.

**Why a register rather than a second pipeline.** Both legs have been harvested
for weeks and nothing has ever read them: no tables, no product surface. The
tempting move is to treat a paper like an announcement — classify it, score it,
join it to holdings. That is a second full pipeline (its own prompts, its own
scoring rule, its own gold set) and it answers a question announcements already
answer better. What papers and GitHub uniquely answer is *who is inside the
lab*, which is what a departure or a stealth-startup formation — the brief's own
top-tier signal, and something nothing in this system currently produces — is
measured against. So they become people, evidence and dates.

**The design is three refusals, and each is enforced structurally rather than by
convention.** Over-merging is the dangerous direction here: two people collapsed
into one yields a register that looks *cleaner* and is wrong, with nothing to
notice.

1. **No cross-lab merge.** `people` is keyed on lab. Two researchers sharing a
   name at DeepSeek and DeepMind are two people; merging them asserts that
   somebody changed employer, which is a finding, not a default.
2. **No cross-leg merge.** A GitHub login and a paper byline are different kinds
   of identifier, and matching them on a string is precisely the
   entity-resolution collision CLAUDE.md names as a failure mode.
3. **No LLM-asserted employment.** A byline affiliation read by a model is
   recorded at tier `model_asserted` and can never appear as `confirmed`. This
   is planning.md §11's hard rule made structural: on Anthropic the extractor
   asserted fellowship status for four people whose pages state no affiliation
   at all.

**The bug the tests caught, which is the reason refusal #2 needed schema
support.** `people` was first keyed on (lab, canonical_name), and person lookup
went through the name. That silently merged a GitHub login `Ada` with a paper
byline `Ada` at the same lab — the exact cross-leg guess the module's docstring
claimed to refuse. A comment asserting a property the schema permits is not a
control. Fixed by adding `source_kind` to the person key and resolving through
`person_identity` rather than through the name, so the collision is impossible
rather than merely discouraged.

**`person_identity` is unique on (kind, value, lab), not (kind, value).** A
GitHub login really is globally one account, so the stricter key would correctly
merge someone committing to two of *one* lab's orgs — which matters, since Meta
owns both `facebookresearch` and `meta-llama`. But it would equally merge
someone committing to two *different* labs' orgs into one person, which is a
cross-lab employment claim commit data cannot support. Including `lab` keeps the
common case right and refuses the interesting one, which is the correct
direction to be wrong in.

**What *is* merged:** human-confirmed aliases from `config/aliases.yaml`
(`confirmed: true` only — a proposal is a question, not an answer), and the
evidence-based alias merges `aggregate_github` has already computed from commit
emails within an org.

**Alternative rejected: fuzzy-match names across legs and record a confidence.**
Attractive because the union is the genuinely useful register (planning.md §12:
"a person can appear in both, and the union is the register"). Rejected for now
because a confidence score on an identity claim invites exactly the treatment
this project refuses elsewhere — a number nobody can defend, quietly hardening
into a fact downstream. The right shape is `aliases_proposed.yaml`: propose,
have a human confirm, then merge. That is a follow-on, not part of this stage.

**Consequence.**
- `unresolved_items` gives the harvesters' existing "record failures, don't drop
  them" discipline a table. It is not optional scope: "we found 27 Meta
  candidates and resolved 6" is a materially different claim from "Meta
  published 6 papers", and a register whose gaps are invisible reads as
  complete.
- Every person carries at least one `ref_url`. A byline with no paper URL behind
  it produces no person at all — "no citation, no insight" applied to people.
- The lab's own page is the citation, never the arXiv page the byline was
  scraped from (`meta_url`, `announcement_url`), consistent with the adapter.
- Migration `0003`. `people`/`person_*` are derived and are dropped by a
  rebuild; they are reproducible from the raw layer, unlike the ops tables.

**Run live, all 14 sources, $0.00** (fully cached): 3,253 GitHub person-records
across 8 orgs collapsing to 3,250 people — the 3-row difference is exactly the
three logins committing to both of Meta's orgs — plus 49 papers and 1,674
paper-derived people. Re-running changes nothing. Every one of the 4,927 people
carries at least one resolvable `ref_url`; zero orphans.

**Known limitation, pinned rather than fixed: `unresolved_items` is a floor, not
a census.** Only Meta and Mistral return the candidates they could not resolve;
the other four papers harvesters drop failures internally and hand back only
what worked. Observed in the live run — DeepMind lost two papers to a 403 and a
404, Anthropic one to an unparseable byline, and none left a record anywhere. So
a lab absent from `unresolved_items` has either lost nothing or cannot say, and
today those look identical. Closing it means changing four harvesters' return
signatures under `research/`, which is more surgery than this stage warrants;
`tests/test_registry.py::TestUnresolvedReportingCoverage` pins the count at two
of six so it cannot drift in either direction unnoticed.

## D26 — Two kinds of alert, and the dedupe key is the actual design (2026-09-03)

**Decision.** One `alerts` table, two `kind`s (`system` | `content`), pure-function
rules, one dispatcher, and a dedupe key per rule chosen so that one *incident*
produces one alert.

**The kinds are not cosmetic.** CLAUDE.md requires system-failure alerting
distinct from content alerting. `system` means the pipeline is broken and
someone has to fix it; `content` means the pipeline is working and found
something. They share a table because they share a lifecycle — raise, record,
deliver — and nothing else. Every consumer filters on `kind`, and a degrading
classifier is filed under `system`, not as a finding about the world.

**The dedupe key is where the engineering is.** Rules re-run every firing, so the
naive version alerts about the same dead source nightly and trains everyone to
mute the channel — at which point the alerter is worse than nothing, because the
real alert is now also muted. Each rule keys on what identifies the *episode*:

- `source_down` keys on `last_success_at` — the moment the outage began, which
  does not move while it continues. **Proven live: six consecutive firings
  against a dead source produced exactly one alert.** A recovery followed by a
  new failure produces a genuinely new key, so a real second incident is not
  suppressed.
- Content rules key on the item, so an article alerts once, not once per firing
  for the three months it sits in the rolling window.
- `budget_exceeded` keys on the run for a per-run breach and on the *month* for
  a per-month one — the monthly ceiling stays breached until it resets, and
  re-alerting on every subsequent run would be the same mistake.

**Recording and delivery are separate, and only delivery is capped.** Evaluated
live against the real corpus, the rules produce **135 content candidates** (16
high-band articles, 119 holding impacts). Firing 135 notifications on the first
run would guarantee the channel is ignored. Everything raised is recorded and
visible in the app; delivery stops at `max_deliveries_per_run` (10) and the rest
carry `delivery_error: suppressed`, so they read as deliberate rather than
failed. Measured: `raised 135, delivered 10, suppressed 125`, then
`duplicate 135` on the next dispatch.

**Delivery failure is never fatal and never loses an alert.** The row is the
record of truth; the channel is a courtesy. A dead webhook records the exception
on the alert and the run continues. A `webhook` channel with no
`ALERT_WEBHOOK_URL` raises rather than no-oping — a silent no-op looks exactly
like a working alerter, which is the worst possible failure for this component.

**Alternative rejected: alert on every failed source immediately.** Simpler, and
it is what "the pipeline tells me when it breaks" sounds like it means. Rejected
because a source down for one firing and back the next is exactly what the next
run's own per-request retries handle (planning.md §4b) — alerting on it converts
normal transient behaviour into a page.

**Consequence — a design point the live test exposed, for the worker to settle.**
Marking a run `failed` whenever any single source fails produces a `critical`
`run_failed` alert on *every* firing while that source stays down, duplicating
what `source_down` already reports once. Observed directly: three firings with
one dead source gave three critical alerts plus one warning, for one problem.
So `worker.py` must distinguish **"the run completed but a source failed"**
(run `succeeded`; `source_down` owns it) from **"the run itself broke"** (run
`failed`). The orchestrator already isolates sources precisely so a dead source
is not a dead run; the run status has to say the same thing, or the alerting
contradicts the design one layer down.

**Consequence — the fan-out shows up here too.** 119 of the 135 content
candidates are `holding_impact`, because one compute-buildout sentence reaches a
dozen holdings (see docs/insights.md). The rule already collapses an article's
several routes to one alert per holding, which is why it is 119 rather than 400+.
Grouping by *event* rather than by holding is the remaining lever, and is a
digest-design decision rather than an alerting one — not taken here.

## D27 — What "failed" means for a firing, and a dedupe key that did not survive a rebuild (2026-09-03)

**Decision.** `bitcap-worker` is one firing recorded as one `pipeline_runs` row,
spanning ingestion (which commits per source) and the ETL (which stays atomic).
`app.runs.tracked` gained an optional `run=` parameter so both phases record
against the same row rather than opening two.

**A dead source is not a dead run, and the exit code says so.** This is D26's
open question, settled. The orchestrator isolates sources precisely so one
broken lab does not cost the other six; if the run status contradicted that, the
alerting would contradict the design one layer down — measured in D26, three
firings with one dead source produced three `critical` `run_failed` alerts plus
one `source_down` warning, for a single problem. So:

- a firing that completes is `succeeded`, even with sources down, and exits `0`
- `source_down` escalates a source after N consecutive firings, once per outage
- non-zero exit means the *firing* broke, keeping the platform's own cron
  alerting a signal rather than a nightly red light

**Cadence is counted in firings, not dates**, so a platform that misses a night
does not also skip a leg's turn. `(firing - 1) % cadence == 0`, which makes
firing 1 run everything — a fresh deployment gets a full sweep instead of
waiting six days for its first GitHub harvest.

**The bug, and it is the interesting one.** Running the worker live, the second
firing raised all 135 content alerts again with zero duplicates. The dedupe was
correct; the *key* was not. Content rules keyed on `article_id`, and `articles`
is a derived table — `load_refs` wipes the entire clean layer on every load and
`transform` rebuilds it, handing every article a fresh autoincrement id. So each
firing invented new dedupe keys and re-raised everything, which is exactly the
"alerts nightly forever" failure the dedupe exists to prevent, reached by a
different route and invisible to every unit test because none of them rebuilt
the clean layer.

Fixed by keying on the article **URL**, which survives the rebuild and is also
the citation. `tests/test_alerts.py::TestDedupeKeysSurviveARebuild` reproduces
the wipe-and-rebuild explicitly rather than trusting the id to be stable.
Verified live: firing 2 raised 135, firing 3 raised 0 and deduplicated 135.

**The general lesson, worth stating in the design doc.** Anything derived is not
an identifier. This project deliberately rebuilds its clean layer wholesale so
denormalised copies cannot drift (D-"the database layer"), and the cost of that
choice is that no row id from it can be used as a stable key by anything
outside. Two other things already key on derived ids — nothing else persists
across runs today, but any future consumer of `connections` or `classifications`
inherits the same trap.

**Two smaller bugs found by the worker's own tests**, both of which would have
produced a run that looks fine and is not: `firing_number()` was called after
the firing's own row was committed and so counted itself, putting every firing
one ahead; and a failure anywhere outside the ETL (the sink, the register load,
the classifier) left the run row at `running` forever — a corpse the
`run_failed` rule never matches and no operator can tell from a firing still in
progress. `run_once` now closes the run out on any exception.

**Alternative rejected: a second run row per phase.** Would avoid extending
`tracked` and keeps each transaction's lifecycle self-contained. Rejected
because "how many firings have there been" and "how many sources failed in the
firing on the 3rd" both become joins across two row kinds, and the ops view
would show every night twice.

**Deployment.** One `Dockerfile`, two entrypoints — the cron overrides `CMD`
with `bitcap-worker`, the web service with uvicorn. Building them separately
would mean two Dockerfiles that must not drift. `render.yaml` declares the
database, schedule and volume so the deployment is reviewable in the diff rather
than clicked together in a dashboard. **The volume at `/app/research/docs` is
not optional**: the per-URL caches are what make a re-run nearly free, and
without it every firing starts cold and re-pays for LLM extraction. Verified by
building the image and running the worker inside it against Postgres.

## D28 — The operational surface, and a chart that had to choose its own axis (2026-09-03)

**Decision.** Four read-only endpoints (`/api/runs`, `/api/alerts`,
`/api/health`, `/api/drift`) and a separate `/ops` route, plus a system-alert
badge on the dashboard header.

**Why it exists at all.** `pipeline_runs`, `source_state`, `run_sources` and
`alerts` were all being written and none of it was reachable outside psql. That
makes "system-failure alerting distinct from content alerting" true in the
schema and false in practice — a pipeline that reports its health into a table
nobody opens is not reporting its health.

**A separate route, not a tab.** The dashboard answers "what did we learn";
`/ops` answers "can I trust what the dashboard is showing". Different readers,
different question. It also leaves `frontend/app/page.js` as the single-file
prototype it was deliberately built as, rather than growing three more views
into a 700-line component.

**The badge counts system alerts only.** Content alerts are the product
*working* — 135 of them today. Putting those on a badge meant to be noticed
would make it permanently red and therefore meaningless.

**`run_sources` is joined into the run history, and that is the point.** D27
made a firing that lost one source `succeeded`. Correct for the alerting, but it
means the run row alone reports a green night on a broken source. `sources` and
`sources_failed` are what make "the run completed" and "everything worked"
separable in the UI, exactly as they are in the schema.

**The chart made two decisions worth recording.**

*It refuses to draw itself below two points.* There is one drift measurement
today; a line through one point is a trend line implying history that does not
exist. Under two measurements it renders the number and says how many there are.

*Its y-axis starts at 0.5, not 0.* Agreement clusters near 1.0, so a full 0–1
axis squashes every real movement into the top quarter — measured: 20px of
travel in a 160px chart for a realistic series, versus 40px on the chosen
domain. Truncating an axis is a genuine anti-pattern for bars, where area
encodes magnitude; for a line, position encodes value and a labelled axis is
honest. The domain extends *downward* if a measurement falls below it, so it
never truncates data, only empty space.

Colours are the existing brand tokens. Run through the palette validator they
pass CVD separation (ΔE 20.1 deutan) and contrast (≥3:1) against the dark
surface, and fail only the categorical lightness band — a check scoped to
multi-series categorical palettes, which this is not: one series and one
labelled threshold line.

**Two bugs found by reading the code back rather than by a test.** The alerts
filter built `/api/alerts&limit=40` — no `?` — whenever "all" was selected, so
the unfiltered view would silently return the default instead. And the chart's
multi-point branch had never rendered, since only one snapshot exists; its
geometry was checked directly for NaN and viewBox overflow across four series
shapes instead of being assumed.

**Consequence.** `api/ops.py` is a separate read model from `api/queries.py` on
purpose: that one builds the content view, this one answers whether the pipeline
is healthy. Endpoint limits are clamped (max 200) — an unbounded `limit` on a
shared database is a denial of service, and the existing `/api/items` returning
the whole corpus unpaginated is already flagged in the frontend handoff as the
thing to fix before the row count grows.

## D29 — Independent review of the pipeline work: one CRITICAL, eleven MAJOR, all fixed (2026-09-03)

Two `bitcap-reviewer` passes over the staged change set before commit — one on
the pipeline core, one on the API/frontend/tests, both briefed to be adversarial
and told that test quality mattered more than anything else. 20 findings. Every
one is fixed, and the ones worth recording are below.

**CRITICAL — `ensure_schema` failed on exactly the case its docstring claimed to
handle.** It called `create_all` unconditionally *before* `command.upgrade`.
`op.create_table` has no `checkfirst`, so pre-creating the tables a pending
migration is about to add made that migration die on "table already exists" —
on every firing, until a human stamped the database by hand. Reproduced by the
reviewer against a throwaway sqlite file. The existing test only covered the
never-migrated and at-head cases, so it stayed green. `create_all` now runs only
when the database is unstamped, or *after* the upgrade on the rebuild path;
`test_a_database_behind_head_upgrades_instead_of_colliding` upgrades to N-1 and
then calls it.

**The cost instrumentation had three holes, and cost is a graded section.**

1. *Double counting.* The worker added the classifier's own figure to
   `run.cost_usd`, then the ETL added `load_costs`' `new_usd` — the same money,
   because the classifier writes those very rows to the log the loader reads. A
   20-article firing would have reported $1.16 against $0.58 of real spend.
   Fixed by making the ETL the single accounting point (assign, never
   accumulate, as `cmd_load` already did) and moving both LLM stages before it
   so their records are in the log by the time it reads.
2. *Drift spend was never recorded.* `measure()` took `cost["usd"]` and threw the
   record away, so ~$5/month of real, nightly spend never reached `raw_costs`
   and the monthly ceiling could not see it. It now writes to the same
   append-only log at the call site, per call.
3. *The papers leg was outside the budget entirely.* Its harvesters do spend on
   LLM byline extraction, but into per-lab logs that carry **no timestamp** — so
   those rows cannot key into `raw_costs` (unique on url+at) without one being
   invented. Rather than fabricate a field, the adapter reads its lab's log total
   before and after the call and reports the delta, which lands on
   `run_sources.cost_usd`, is charged to the `Budget`, and is summed into
   `month_to_date` alongside the timestamped rows. Non-overlapping by
   construction: classification and drift never write `run_sources.cost_usd`.
   `render.yaml`'s claim that the papers leg was "bounded by budget.per_run_usd"
   had been false.

**`SystemExit` walked through both isolation layers.** `harvest_github.load_token`
calls `sys.exit()` when `GITHUB_TOKEN` is unset — and `SystemExit` derives from
`BaseException`, so `except Exception` caught it in neither the orchestrator nor
the worker. The result was the worst combination available: the firing aborted
mid-ingest, the run stayed `running` forever, `record_failure` was never reached
so `consecutive_failures` stayed 0, and *neither* `run_failed` (which matches
`failed`) nor `source_down` could ever see it. Both layers now catch it;
`KeyboardInterrupt` deliberately still propagates, because ctrl-C means stop.

**A confirmed alias added after first load did nothing.** `_person` resolved on
the identity row before consulting the alias map, so once both spellings existed
the merge was inert — and that is the operator's *only* entity-resolution
control ("notice a duplicate, confirm it, re-run"). Reproduced by the reviewer.
Merging is now retroactive: `_absorb` moves identities and evidence onto the
canonical person, widens the seen-span, and deletes the duplicate.

**The batch classification path enforced no ceiling at all** while still
recording a budget snapshot on the run — a control that reported itself as
working. `classification.batch: true` is a one-line config change. A batch is
one submission, so the ceiling now bounds the *size of the submission* against
the remaining budget (at half rate, since batch is half price) instead of being
applied after the fact.

**Two new config files had no validator**, and every value in them fails
silently: `content_band: High` raises **zero content alerts forever**, a mistyped
`cadence` key means that leg never runs, and a mistyped `url_field` makes every
paper resolve to a null citation and be dropped while the source still reports
SUCCEEDED with a healthy `items_seen`. `check_pipeline` and
`check_papers_sources` now cover both, and their tests assert each named typo is
caught *and* that the committed files pass.

**The drift alert re-fired every firing.** It keyed on the snapshot id, and a
snapshot is written every run — 14 warnings for a fortnight of one degradation,
the exact failure `source_down` avoids. Now keyed on the last snapshot that was
*above* the floor, which is the episode's start and the direct analogue of
`last_success_at`.

**The health page lied when the backend was down.** `sources_failing` was read
with `|| []`, so a failed `/api/health` rendered a calm blue `0` and the words
"all healthy" — on the one page whose job is to be trusted. And a 500 has a
valid JSON body, so `r.json()` *resolved*, `.catch` never fired, and the page
then crashed on `runs.map` and went blank. Both fixed, plus the API now calls
`ensure_schema` at startup: a fresh Render deploy provisions an empty database,
so `/api/status` would have 500'd and failed the platform health check before
the first cron ever fired.

**One of my tests was tautological and the reviewer proved it.** `test_limits_are_clamped`
asserted only `status_code == 200`; the reviewer re-registered the endpoint with
the clamp removed and both assertions still passed. It now asserts row counts,
and every listing endpoint is covered. Two other assertions that could not fail
were removed. The migration/model comparison also only compared columns, so a
`UniqueConstraint` present in a model and missing from its migration was a false
green — and `alerts.dedupe_key`'s uniqueness is what the whole alerting design
rests on; it now compares unique constraints and foreign keys too.

**My own tests corrupted a committed artifact.** The new drift tests stubbed
`sa.classify` to return `{"usd": 0.01}` with no `url`/`at`, and `_record_cost`
appended that to the *real* `announcement_cost.json` — after which `load_costs`
crashed on it. Three fixes, because one was not enough: the drift tests now
redirect `COST` to a temp file (autouse), `_record_cost` refuses to write a
record that cannot be keyed, and `load_costs` counts and reports a malformed row
rather than dying on it.

**What the reviews found working**, recorded because it is the evidence the
design decisions were right: `Budget`'s in-flight accounting is sound under
fan-out with no leak on the exception path; `tracked(run=...)` keeps correct
transaction semantics for both callers; `drop_all` preserving `OPS_TABLES`;
`run_history`'s two-query join; and `TestDedupeKeysSurviveARebuild` /
`test_the_uncached_path_is_used`, both of which encode a measured production
failure as a property.

**Two limitations both reviewers named and neither I nor they resolved.** Every
test runs on sqlite while production is Postgres and Alembic — the CRITICAL above
is a concrete instance of that gap biting, and a `JSON` vs `JSONB` divergence
would still pass green. And on a cold cron container `announcements.json` resets
to the image copy, so an article pending in `raw_articles` but absent from the
reset corpus would never be classified and no rule alerts on that shape. Both
are recorded rather than fixed.

---

## D30 — The people leg: `config/people.yaml`, sourced per person, with departures as a first-class field (2026-09-03)

The register tracks labs. Labs do not post — people do. `config/people.yaml`
adds the missing leg: 36 senior figures across all seven registered labs, each
with their X handle and blog where one exists, keyed on the same lab ids as
`sources.yaml` and joined by `config/validate.py` like every other lab-keyed
config file.

**Nothing in it comes from model knowledge**, on the same rule as
`research/labs/frontier_labs.md`. Everything was fetched on 2026-09-03 and the
fetch is cited at the field it supports. That was not ceremony: re-researching
from scratch contradicted what recall would have produced on four of the seven
labs. Yann LeCun left Meta in November 2025 (AMI Labs, announced 2026-03-10).
Jeff Dean, Oriol Vinyals, Quoc Le and Sanjay Ghemawat left Google on 2026-08-05
for Discovery Loop. Demis Hassabis moved off GDM CEO to Chair + Alphabet Chief
Scientist on the same day, with Koray Kavukcuoglu taking day-to-day as SVP. All
twelve xAI co-founders were gone by 2026-03-28. Every one of those would have
been a confident, wrong attribution.

**So `departed` is a field, not a footnote.** It is the guard against this
file's most likely failure — rendering someone as a voice of a lab they left —
and `validate.py` fails if a name appears in both lists.

**X handles are not fetch-verified and the file says so.** x.com returns HTTP
402 to the fetcher, so no profile page was read. Each handle carries an
`x_evidence` tier instead — `own_site` (their site links it), `self_post` (a
post from the handle in which they state their own role), `lab_post` (the lab's
account names them), `search_index` (the profile URL appears in search and
nowhere better). `search_index` is explicitly the weak tier and must be
promoted before anything ships on it. The alternative — asserting handles at a
uniform confidence — is the shape of error this project exists to avoid.

**A source recorded but not read must admit it.** One CNBC URL (2026-06-14,
Wang still in post) is cited with `fetched: null` and "NOT FETCHED" in its
note, because cnbc.com 403s here. A test fails on any unfetched source without
that admission: a URL nobody read, sitting unflagged in a citation list, gets
promoted to verified by nothing more than being in the list.

**Two findings worth keeping.** DeepSeek has effectively no people leg — its
researchers do not run public personal channels, and the two handles found sit
on the weak tier; for that lab the papers *are* the channel. And xAI's people
leg mostly reports an absence: the entire founding research bench is gone,
replaced by SpaceX operators with no public research channel, three of four
with no findable handle and none with a blog. That is consistent with D16/D17's
decision not to run a papers harvester for xAI, and it is a signal about the
lab rather than a gap in the research.

Also recorded: Jan Leike's own site still states he leads Alignment Science
while a secondary aggregator reports he stepped back in May 2026. Both were
live on the research date. The entry carries the conflict rather than picking a
side.

## D31 — Ingestion becomes incremental, because the deployed shape has no disk (2026-09-03)

Every adapter took a `state` argument and every adapter ignored it. The three
docstrings said why, and all three said the same thing: a full re-derive of the
window is nearly free, because every page is cached on disk. `SourceState.watermark`
was written on every success and never once read back.

That reasoning was sound when the only deployment was a laptop. It is false on
the one that ships. Render cron jobs cannot mount a disk, so each firing starts
from the image and every page in the window is a live fetch — roughly 500
announcement pages, nightly, to discover the two articles that are new. The
earlier framing of a cold start as costing "time, not money" was true and beside
the point: re-scraping a three-month window every night is wrong regardless of
what it costs.

**The window is now anchored to the last success, with a 48-hour floor.** A
source that has never succeeded fetches its full configured window — that is the
first firing against an empty database, and it is a newly-added lab backfilling
itself without dragging the other six through a re-harvest. Everything after
that fetches since `last_success_at`, never less than 48 hours and never wider
than the configured window.

Two alternatives rejected. **A fixed 48-hour window** — what was first proposed —
loses articles permanently to any outage longer than two nights, and nothing in
the system would report the gap: no failed source, no budget breach, just a
quieter digest. Anchoring to the last success costs one comparison and makes a
missed night self-healing. **A URL anti-join against `raw_articles`**, the same
shape `classify_new` uses, is more precise still and needs no clock, but it
wants a session inside the adapter; `last_success_at` is already threaded
through `run_source` and is the same fact in every path that exists today.

The 48-hour floor is not slack. Labs backdate posts and correct dates after
publishing, and a window pulled tight to the last run would never see them.

**The papers leg gets whole months, not hours.** Three harvesters compute
`timedelta(days=months * 30.5)` and would take any float. `harvest_contributors`
does calendar arithmetic — `today.month - months`, formatted `{y:04d}` — which
raises on a float and cannot express a sub-month window at all. So papers is
floored at one month rather than 48 hours: about a third of the fetches back
instead of all of them, which is the right trade at cadence 3 for a leg where a
one-month floor cannot miss a paper. Buying the rest means rewriting six
harvesters' notion of a window.

**GitHub is left alone, and not because it was forgotten.** Its window is not a
feed of new items but a rolling 12-month aggregate: `aggregate_github.aggregate`
builds the people register by reading `github_commits_<org>.json` whole.
Narrowing the window there does not produce fewer fetches and the same answer,
it produces a different answer. Making that leg incremental means persisting
per-repo commit history in Postgres to replace the disk cache, which is a bronze
table and a migration, not an argument change. Recorded here rather than done.

Consequence: the steady state of the announcements leg becomes one index fetch
per lab plus the bodies of whatever is actually new. The cold-container
behaviour that §13.3 describes is unchanged — this narrows what gets fetched,
not where the text lives.

## D32 — GitHub gets the bronze layer it never had, and stops re-walking a year of commits (2026-09-03)

D31 made announcements and papers incremental and left GitHub alone, because
narrowing its window is not the same operation. The other two legs fetch a feed:
ask for less, get less, keep the rest in bronze. This one builds a rolling
12-month *aggregate* — `aggregate_github` reduces every commit in the window to
one row per person — so a narrower window does not return the same answer for
less work, it returns a different and wrong answer.

**The actual gap was a missing table.** `raw_github_people` stores the
aggregate: counts, date range, email domains. The commits it was computed from
were never in Postgres at all; they lived in `research/docs/github_cache/`, 772
files on disk. Bronze skipped a layer for this leg, which is why the register
could not be rebuilt from the database and why a container without a disk had to
re-walk twelve months of history to recompute a summary it already had.

`raw_github_repos` is that layer — one row per (org, repo), history verbatim,
`pushed_at` alongside it. The listing call returns every repo the org has pushed
to in the window and is one cheap REST page; a repo whose `pushed_at` has not
moved since bronze last saw it *cannot* have new commits, so it is never walked
again. Only genuinely changed repositories cost a GraphQL walk.

**`pushed_at` rather than a per-source watermark.** One watermark for the whole
org would be wrong in both directions: too coarse to skip the 90% of repos that
did not change, and useless for deciding which did. The right granularity is per
repository and it belongs in bronze next to the payload, not in `source_state` —
which is why `fetch_github` still takes `state` and still ignores it.

**Two bugs fell out of this rather than being hunted.** First, `collect()`
returns the harvest but only `__main__` ever wrote `github_commits_<org>.json`,
and `aggregate()` read that file — so on the deployed path the harvest was
discarded and the register was rebuilt from whatever JSON was committed to the
image. Passing the harvest to `aggregate(raw=...)` closes it. Second, the disk
cache had no max-age: once a repo was cached it was never refetched however many
commits it gained. `pushed_at` is a real freshness check, so the replacement is
more correct than the thing it replaces, not just portable.

**Stored commits are trimmed to the window before aggregation.** A quiet repo is
never re-walked, so its stored commits were fetched against an older, wider
`since` and still carry commits that have aged out. Counting them would turn the
12-month register into an all-time one, one night at a time, with nothing
reporting it. `_in_window` drops them; the drift is the kind of silent
degradation that only shows up months later as a number nobody can explain.

Consequence: the adapter signature gains a `session`, since deciding what *not*
to fetch requires reading what is already stored. All three adapters take it so
there is one signature; only this one uses it. Persistence stays in
`register.load_github_repos`, ordered before the aggregate load so a run that
dies between the two keeps the commits it paid to fetch.

## D33 — Don't cache the page; don't ask for it twice (2026-09-03)

The proposal was to move the ~250 MB of cached HTML under `research/docs/` into
Postgres, so that production would not depend on a disk it does not have. The
premise was right and the remedy was not.

Nothing reads that HTML after ingestion. `fetch()` downloads a page,
`strip_html()` pulls the text out, and the text lands in `raw_articles.payload`.
Scoring, transform, quote verification and the API all work off the stored text;
the HTML is never opened again. It is not data being kept, it is a receipt for an
HTTP request, kept only so the request is not repeated.

That makes it different in kind from the two caches that *were* moved into
Postgres. A classification costs $0.029 to recreate and a year of commit history
costs a GraphQL walk and a rate limit, so both earn a row. A page costs one GET.
Relocating a quarter of a gigabyte of the cheapest, most disposable bytes in the
system into a 1 GB database is paying storage rates for rubbish.

**So the fix is not to make the second request cheap, it is not to make it.**
`_settled_urls` is the same anti-join `classify_new` already uses, one layer
earlier: an article already in `raw_articles` yields nothing by being fetched
again. Every discovery method takes it and filters candidates before the body
fetch. Strictly better than any cache — no request, no bytes, no rate limit, and
nothing lost when the container is discarded.

**Stored is not sufficient; it has to be classified as well.** This is the part
that is easy to get wrong. `classify_new` reads article *text* from the corpus
file rather than from the database, and on a disk-less container that file resets
to the image copy every firing. Skipping an article that was stored but still
pending would leave it pending for ever with nothing able to classify it — which
is planning.md §13.3 promoted from a rare edge case to the normal path. The skip
set is therefore "ingested **and** classified at the current prompt version",
which leaves every straggler being refetched until it is scored.

That is a coupling between ingestion and classification state, and it is the
price of not touching §13.3 here. The cleaner fix remains the one recorded
there: put the text where the classifier can read it, and this condition
collapses back to "is it in `raw_articles`".

Where it pays most is xAI. `from_wayback_cdx` fetches every archived snapshot at
two seconds a request purely to read a publication date out of it, because the
CDX timestamp is a crawl date and says nothing about age. Those are now fetched
once each, ever.

Consequence: the announcement disk cache stops mattering in production, because
production stops asking for pages it already has. It stays useful locally, where
re-running a harvester over a window on purpose is a normal thing to do. Running
any harvester as a script passes no skip set and still refetches everything.

## D34 — A managed Postgres URL names no driver, and SQLAlchemy's default is one we do not install (2026-09-03)

The API died on its first Render deploy with `ModuleNotFoundError: No module
named 'psycopg2'`, at `get_engine()`, before serving a request.

`pyproject.toml` pins `psycopg[binary]>=3.2` and always has. The README tells a
developer to write `postgresql+psycopg://`, so every local run names the driver
explicitly and works. Render's `fromDatabase` supplies a bare `postgresql://`,
SQLAlchemy resolves that to its historical default of psycopg2, and nothing
local reproduces it — the one environment that hits the bug is the one nobody
can run before deploying.

`normalise_url` rewrites a bare `postgres://` or `postgresql://` onto
`postgresql+psycopg://`, in `app.db`, which is the single place both paths to an
engine already pass through (`get_engine`, and `alembic/env.py` via
`database_url`). A URL that names its own driver is left alone: `+psycopg2` from
someone who installed it deliberately still means that, and so does `+asyncpg`.
Only the ambiguous case is decided.

Rejected: setting `DATABASE_URL` by hand on the service with the driver spliced
in. It works, and it silently unpicks `fromDatabase` — the password stops
rotating with the database and the blueprint stops describing the deployment.
Also rejected: adding psycopg2 to the dependencies, which would make the default
work by installing a second Postgres driver nothing else uses.

The gap this exposes is that no test ever fed the code a URL in the shape a
platform actually emits. `tests/test_db.py` now does, including asserting the
resolved dialect is psycopg — `create_engine` resolves the DBAPI eagerly, so a
regression fails in CI rather than on deploy.

## D35 — The drift check goes to the whole gold set, because six items measured its own noise (2026-09-04)

Firing 7 raised the first real drift alert: mechanism micro-F1 0.750 against
the 0.80 floor. It is not clear that it means anything.

The sample was six articles carrying ten reference mechanism tags, so one tag
missed or gained moves micro-F1 by ~0.05 and the floor sits three tags away from
the score. Precision was 1.00 and recall 0.60 — the classifier tagged nothing
wrongly and found six of ten — which is the shape of under-tagging, and also the
shape of a small sample. Six items cannot distinguish those. A check that cannot
separate drift from its own sampling error is not measuring drift, and a system
alert that fires ambiguously every week is how the channel gets muted — the same
failure `max_deliveries_per_run` already exists to prevent on the content side.

The original argument for six (planning.md §6a) was that variance probing is
pure cost with no product output, and that 12 × 3 runs bought worse statistics
than 6 × 5 for the same money. That reasoned about *total* spend across runs
when the budget was the binding constraint. It is a real argument and it was
answered by raising the budget rather than by disputing it.

`DEFAULT_SAMPLE` is now all 20 adjudicated gold articles. The sample stays a
fixed named list rather than a draw, which is the part of §6a that still holds:
a movement between runs must be the classifier changing, never the sample. The
six originals are still in it, so the failure-mode coverage that made them the
choice is not lost — richly-tagged release, compute commitment, regulatory
action, practices-only, and two with no tags at all.

Cost, from the measured $0.2247 for six: ~$0.0375 an item, so ~$0.75 a run.
Drift is uncached by design (a cached drift check reports perfect agreement
forever — the failure mode that looks exactly like success) so this recurs every
firing. At the nightly cadence that is ~$22/month, which is why `per_month_usd`
goes 20.00 → 75.00. Cadence stays 1: the check runs whenever the pipeline runs,
so a prompt or model regression is caught on the firing that introduces it
rather than up to a week later.

Rejected: dropping drift to weekly, which would have fitted the old ceiling at
~$3/month. It buys a cheaper bill by making the detector slower than the thing
it detects — a bad prompt version would ship and classify six nights of articles
before anything noticed.

Two tests replace the old `4 <= len(sample) <= 8` assertion, which encoded a
policy that is no longer true. One asserts the sample is exactly what is on
disk, so adjudicating a new gold article and forgetting to register it fails
loudly instead of quietly measuring less than it claims. The other bounds
per-run drift cost, so growing the gold set cannot walk the budget up unnoticed.

Not addressed: 20 items is better than 6 and still small. The honest statement
in the design doc is that this detects gross regression, not a two-point shift.

## D36 — The page cache had no expiry, and it was caching the sitemaps (2026-09-04)

A local firing reported zero new articles from all seven labs in 0.0 seconds
each. That was read as a warm cache being fast. It was not: `fetch()` served any
cached file on `if cached.exists()`, with no age check, and the cache holds
*discovery* documents alongside article bodies —
`https_www_anthropic_com_sitemap_xml.html` was two days old. The run was reading
a sitemap as of 2026-09-02 and could not have discovered anything published
since.

This is the worst shape a bug can take here. Nothing failed, nothing was slow,
no source errored, and "no new articles" is exactly what a correct quiet day
looks like. Every layer above the cache — `items_seen=0`, `succeeded`, the run
summary — reported success truthfully. Only the mtimes on disk showed it.

The rule is a property of the document, not of the fetch: an article body is
immutable, so caching it forever is correct and it keeps `max_age_hours=None`.
A sitemap, an RSS feed, a listing page and a Wayback CDX query all exist to
change, so an unexpiring copy of one is always wrong. `DISCOVERY_MAX_AGE_HOURS`
= 6, passed at the four discovery call sites; the three body sites and the
timestamped Wayback snapshot (immutable by construction) are untouched. Six
against a nightly cadence: anything materially under 24h makes the scheduled
firing always see a live feed, and six still spares a lab's server during an
afternoon of local re-runs.

Production was never affected, which is why this survived. Render cron jobs
cannot mount a disk (D31), so the cache directory is empty on every boot and
discovery is always live. The deployed system was correct and the development
system was blind — the reverse of the usual asymmetry, and the reason the
deployment work surfaced it rather than the pipeline work.

The test that would have caught it asserts at the call site, not just on
`fetch`: a perfect expiry policy is worthless if `from_rss` never passes one.
Verified by re-breaking `_cache_is_fresh` and confirming the stale-sitemap test
fails.

## D37 — `raw_classifications` becomes `raw_llm_responses` (2026-09-04)

The name put the table on the wrong side of the bronze/silver line for anyone
reading the schema cold, and it prompted exactly that question: bronze is meant
to be unprocessed, so why is a *classification* — the output of a processing
step — sitting in it?

The answer is that the row is not a classification. It is the provider's
verbatim response to one prompt, unparsed and unscored, keyed by `url` and
`prompt_version`. The LLM is an external system, and calling it is an
acquisition, not a transformation: `raw_articles` holds what openai.com
returned, `raw_llm_responses` holds what the model returned, and neither has any
of our logic applied.

The test that settles which layer it belongs to is reproducibility, not whether
computation happened. Silver is whatever our own deterministic code can
regenerate from bronze: `classifications` is dropped and rebuilt from
`raw_llm_responses` + `config/scoring.yaml` on every `rebuild`, for free. The
raw response cannot be regenerated at all — re-running the call costs money and
returns a *different* answer, because the model is not deterministic. Putting it
in silver would make every rebuild either re-spend the budget or be impossible.

The two version columns say the same thing. Bronze carries `prompt_version`
(which call produced this); silver adds `scoring_version` (which rules
interpreted it). Change the rules and silver rebuilds free; change the prompt
and new bronze must be bought.

Rename only — `op.rename_table` in migration 0005, no column, constraint or data
change, verified against the populated local database (236 rows, all v7,
preserved). The baseline migration keeps the old name: it is history, and
rewriting it would break any database that has already applied it.

Not renamed: `load_classifications`, which is the domain operation that fills
the table and still reads correctly. `raw_costs` has the same ambiguity and was
left alone for now.

## D38 — An article found tonight was not scored until tomorrow (2026-09-04)

Run 12 ingested four OpenAI articles, and `classify` reported `pending: 0`.
The ETL, later in the same firing, reported `classifications.missing: 4`.

`classify_new` picks its work list with `pending_urls` — `raw_articles` LEFT
JOIN `raw_llm_responses` at the current prompt version. `raw_articles` was
filled by `load_articles`, which ran inside `_etl` at phase 5, two phases
*after* the classifier. So the join could only ever see articles a previous
firing had landed. Every article was scored exactly one firing late, and a first
run against an empty database scored nothing at all.

This is the same failure shape as D36 and as the cadence staggering removed
earlier the same day: the run succeeded, the article was stored, the summary was
truthful, and the only symptom was a number being lower than it should have
been. Three instances in one day is a pattern, and the pattern is that "nothing
to do" and "we did not look" are indistinguishable unless something asserts the
difference.

Fixed by moving `load_articles` from phase 5 to phase 2, next to
`merge_announcements` — both are "land what was ingested where the loaders read
it", which is what phase 2 already claimed to be. The phase-3 comment about LLM
stages preceding the ETL still holds: that constraint is about `load_costs`
being the single place spend is totalled, which is untouched.

Safe outside the ETL's transaction because bronze is upsert-only and no ref
reload deletes it. If a later phase fails, the articles stay landed and the next
firing rebuilds the derived layer over them — the same guarantee `tracked` gives
for everything else.

The regression test asserts the ordering directly: it spies on `pending_urls` at
the moment `classify_new` is called and requires this firing's article to be in
it. Verified by restoring the old phase order and confirming it fails.

## D39 — A pipeline tab, and the login it forced (2026-09-04)

The pipeline could only be run from a terminal or by waiting for 3am. `/pipeline`
adds four checkboxes, a run button, and a live status — but a button that spends
money on a public URL is a different object from a read-only dashboard, and the
whole design follows from that.

**Whole site behind a login, not just the button.** Asked for explicitly, after
the counter-argument was put: the reviewers for this case study open a link, and
a login wall is friction in front of the thing being judged. The mitigation is
that credentials go in the submission. Recorded because it is a product
trade-out, not a technical one.

**One account, from the environment.** `AUTH_EMAIL`, `AUTH_PASSWORD_HASH`,
`AUTH_SECRET`. No users table, no signup, no reset — every one of those would be
machinery serving a single operator. Standard library only: `hashlib.scrypt` is
a memory-hard KDF and `hmac` signs an expiring token, so hashing one password
and signing one token costs no dependency. Adding `passlib` and `pyjwt` for two
function calls would carry a transitive stack into a project that pins seven
things.

Rejected: a shared secret in the URL, and dry-run-only-without-a-token. Both are
lighter and both were on the table; the explicit ask was for real credentials.

**What the gate does and does not do.** The frontend is a static export, so its
HTML and JavaScript are public files and the login screen hides only the
interface. The guarantee lives on the API, where every route requires a bearer
token — an unauthenticated visitor can render the shell and receive no data.
`tests/test_api_pipeline.py` walks the app's own route table rather than a
hand-written list, so a route added later is covered the day it lands instead of
whenever someone remembers to extend a list. `/api/health` stays open on
purpose: it is the platform's deploy probe, and a probe that needs a credential
fails the deploy before the service is ever live. This also caught a
deploy-breaker in `render.yaml`, whose `healthCheckPath` still pointed at
`/api/status` — now gated, and would have 401'd on every deploy.

**The run is a thread, and the state is the database.** A firing takes 9 to 30
minutes; no HTTP client waits that long. `POST /api/pipeline/run` claims a row
and returns a run id, and the browser polls. Nothing is held in process memory,
so closing the tab does not stop the run, reopening picks it back up, and a
restarted API reports the truth about a run it did not start.

**One at a time, guarded in the database rather than the process.** The cron is
a separate container, so a process-local lock cannot see it. The row is claimed
*synchronously in the request*, before the thread starts — claiming it inside
the thread lets two clicks in the same instant both pass the check. `run_once`
gained a `run` parameter so it executes against the claimed row rather than
opening a second one; one firing stays one row.

**A crashed run must not look like a running one**, because the guard is a query
for a `running` row and one corpse blocks every future run until somebody edits
the database by hand. The thread catches `SystemExit` as well as `Exception` —
code under `research/` is scripts first and calls `sys.exit()` on missing config.

**Manual runs are `kind: manual`.** `firing_number` counts *scheduled* runs to
decide cadence, so a button press must not consume the GitHub leg's turn.

Also fixed while in here: `FRONTEND_ORIGIN` now accepts a comma-separated list,
so a local dev server and the deployed site can both be allowed. It was a single
origin, which forced a redeploy to develop against.

Not addressed: the API instance must stay alive for the length of a run. A plan
that sleeps on idle would kill a run mid-flight and leave exactly the corpse
described above. Noted on the service in `render.yaml`; it is a plan choice, not
something code can defend against.
