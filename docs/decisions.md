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
