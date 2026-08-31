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
