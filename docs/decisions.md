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
