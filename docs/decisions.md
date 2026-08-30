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
