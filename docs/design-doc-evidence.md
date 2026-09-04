# Design doc — evidence pack

**Not a draft.** A map from the brief's seven required sections to the material that
already exists, with the numbers pulled and the line references into `decisions.md`.
You write the prose; this exists so you never start a section from a blank page.

Brief's hard constraints: **max 10 pages**, every section carries *decision → rationale
→ consequence*, "if a paragraph could have been written without building the system,
cut it", and it must not read as AI-written.

Suggested page budget, summing to 10:

| § | Section | Pages | Material state |
|---|---|---|---|
| 1 | Architecture and data flow | 1.5 | Strong — needs one diagram |
| 2 | Key decisions, trade-offs, rejected alternatives | 2.5 | Very strong — the problem is cutting |
| 3 | Model choices, fallbacks, agent vs deterministic | 1.5 | Very strong |
| 4 | Evaluation | 1.5 | Strong, and honest about its own limits |
| 5 | Cost | 1.0 | Complete — `cost.md` is already the section |
| 6 | How you worked (agentic setup) | 1.0 | Reconstructed → `docs/agentic-log.md`; needs your `[NEIL]` answers |
| 7 | What works, what's next, 3–5 insights | 1.0 | Strong |

---

## §1 Architecture and data flow — 1.5pp

The spine, as built:

```
config/*.yaml  →  ingestion (4 discovery methods)  →  raw_articles
                                                          ↓
                          classify (Sonnet 5, 3-vote consensus)  →  raw_llm_responses
                                                          ↓
                    transform  →  articles + classifications + tags
                                                          ↓
             connect (deterministic, 4 routes)  →  connections  →  ┬ investment view
                                                                   └ AI-team view
```

Live numbers to quote (from `bitcap-db status`, 2026-09-04):

- 250 articles, 250 classifications, 145 mechanism tags, 28 category tags,
  297 practice tags, **823 connections**, 26 holdings, 877 cost rows, 6 gold snapshots.
- Seven labs, per-lab watermarks: anthropic 37, openai 150, xai 35, google-deepmind 12,
  mistral 9, meta-ai 5, deepseek 2.

Points worth making here rather than in §2, because they *are* the architecture:

- **Four table layers with different truth-guarantees** — raw (verbatim, upsert-only),
  reference (YAML is source of truth), clean (derived, droppable), ops (never dropped,
  irreproducible). `rebuild` is safe precisely because of that split. README has the table.
- **One shared core, two renderers** — the brief's explicit requirement. The join is
  computed once in `app/connect.py`; the audience split is a client-side framing of the
  same rows, not a second pipeline. Say this outright; it's a graded point.
- **Four discovery methods, one interface** `(lab, cutoff) -> list[dict]`:
  `sitemap`, `rss`, `listing_pagination`, `wayback_cdx`, plus `model_index` as a
  second channel (D46). Adding a lab that fits an existing method is config-only.
- **Four connection routes**, each carrying why it fired: `mechanism`, `category`,
  `lab_exposure`, `named`.
- **Four surfaces, one core**: the dashboard (the corpus), `/digest` (the cut,
  per audience), `/register` (who is tracked, and the moves in it), `/ops` (can I
  trust what the other three say). Each answers a different question for a
  different reader; none of them is a second pipeline.

---

## §2 Key decisions — 2.5pp

You have 49 entries and room for about eight. The list below is ten, ranked —
cut from the bottom. These are the ones that carry a real
trade-off *and* a rejected alternative — the two things the brief asks for. Ranked by
what I'd keep:

1. **D49 — The register becomes browsable, and the refusal to merge pays**
   (`decisions.md`, end). The cleanest decision→consequence pair in the log, and
   the one that answers the brief's "researcher moving to a competitor" directly.
   D25 refused to merge a name across labs and recorded that as a *cost*. It
   turns out to be the mechanism: a move is one name with two records, so
   finding one is `GROUP BY canonical_name HAVING count(distinct lab) > 1` — no
   model involved. Then the evidence gate does the taste: **50 cross-lab names
   exist, 4 are shown.** The 46 dropped are GitHub logins with no lab-owned
   email on either side. Asymmetric on purpose — requiring the same evidence on
   the papers leg would drop all four real ones, because a byline carries no
   email domain. If §2 needs a single entry that shows engineering judgment
   *and* product taste at once, this is it.

2. **D48 — A digest is a dated cut, and the cut is the product**
   (`decisions.md`, end). The suppression count is rendered as prominently as
   the items; 84 considered → 8 surfaced over 30 days. Carries three sub-points
   worth their space: the window is half-open because closed bounds made a 48h
   window span three calendar days and fourteen articles appeared in two
   editions each; the 48h cadence is *measured* over the corpus, closing
   planning §7; and a per-lab cap was rejected as a lie about the world (OpenAI
   really did publish 150 of 250) — noticed and declined, not overlooked.

3. **D24 — A budget ceiling replaces the scoring off-switch** (`decisions.md:2851`).
   The best single entry you have, because the consequence was measured, not asserted:
   the guard stopped the run *after spending $0.53 against an $0.08 ceiling* — a 6.6×
   overshoot, because 12 concurrent workers each tested a total none of its peers had
   contributed to yet. Fixed by counting in-flight calls; the re-probe overshot 5.6%.
   Pair it with the insight that the off-switch it replaced had silently hidden four
   labs from the product (`insights.md`).

4. **D25 — The people register is a set of refusals** (`decisions.md:2940`).
   Deliberately does *not* merge same-name people across labs. Consequence: four
   researcher-move candidates fell out (Wu, Tian, Chen, Li) that a tidy merge would
   have destroyed. This is the brief's named top-tier signal arriving as a by-product
   of a refusal. Strongest product-judgment story in the log.

5. **The verbatim quote gate** (`decisions.md:1241`) — the hallucination control, and
   the thing that makes "every insight cites a resolvable primary source" enforceable
   rather than aspirational. Mechanical check before any judgement.

6. **D26/D27 — Two alert kinds, and the dedupe key is the actual design**
   (`decisions.md:3035`, `3103`). System vs content, one dispatcher, consumers filter
   on `kind`. Note D27's dedupe key that did not survive a rebuild — a bug found by
   its own test.

7. **The silence pattern — D36, D38, D45, D46, plus cadence staggering**
   (`decisions.md:3644, 3716, 3932, 3969`). Five instances of one failure shape: *the
   run reports what it did, never what it failed to do*. D45 is the sharpest (drift
   returned zero comparisons on Render, recorded every number truthfully, and reported
   **succeeded**), D46 the most interesting because the cause was the *source* being
   wrong rather than us — OpenAI omitted its own GPT-6 Astra launch from its feed.
   If you only have room for one theme in §2, make it this one. It is the most
   senior-engineer thing in the whole log.

8. **D23 — The orchestrator drives labs, not legs** (`decisions.md:2769`), and counts
   failures rather than backing off.

9. **Confidence becomes a gate, not a discount** (`decisions.md:1025`).

10. **D22 — Migrations become necessary the moment the pipeline persists state**
   (`decisions.md:2712`).

Available but I'd cut for space: D11–D21 (per-lab register extensions — say "seven labs,
config-driven" in §1 and move on), D34 (the psycopg driver), D40, D41, D42.

**One negative result worth 3 sentences somewhere:** publication-derived importance does
not generalise across labs — it's Anthropic-specific (`decisions.md:693`, planning §11b).
Negative results read as honesty and cost almost no space.

---

## §3 Model choices, fallbacks, agent vs deterministic — 1.5pp

This section is nearly written for you at `decisions.md:937`. The table:

| Task | Model | Why |
|---|---|---|
| Announcement classification | `claude-sonnet-5`, 3-vote majority | Only config that reproduced |
| Gold adjudication | `claude-opus-5` | Different model from the classifier, by design |
| Byline extraction (fallback only) | `claude-sonnet-5` | Deterministic parsing is primary |

**The variance finding is the centrepiece.** Two full runs over 375 announcements flipped
`is_signal` on 9% of items; a dedicated probe found only 6 of 12 borderline items scoring
identically, with one article ranging **17.8 to 80.0 across six runs**. And there is no
temperature to turn down — deprecated on the Claude 5 family, verified against the API,
not assumed. So the fix had to be architectural: 3-vote consensus, ~$0.19/item.

Rejected, each with the measurement:

- **Haiku 4.5** — 4.3× cheaper and *more* consistent (9/12 identical), but scored both
  S-1 filings at 0.0, stably. "Its apparent stability is partly confident wrongness."
  That sentence is the whole argument; keep it.
- **GPT-5-mini** — 4.7× cheaper, but the vote didn't converge (4/6 reproducible, 7 tags
  dropped vs 1). Disagrees *systematically*, not just noisily.
- **Removing `is_signal` (v3→v4)** — adopted, but **the hypothesis was wrong**. Worth
  keeping for exactly that reason.

**Agent vs deterministic — the brief asks for this explicitly.** You have measured cases,
which is rare:

- **Byline extraction, measured not argued** (`decisions.md:335`). Deterministic parsing
  stays primary. The eval found *more bugs in the deterministic parser than in the LLM*
  (three, all in the parser) — and deterministic still won, on reproducibility and
  visible failure, not on accuracy. F1s where both were run: 0.978 / 0.996 / 0.879.
  Deterministic extraction of 17 pages: **$0.00, under a second**. LLM: $0.86.
- **D46 model-index parsing** — "which ids are on this page" is a set difference. An LLM
  would be slower, dearer, and able to hallucinate a model that doesn't exist.
- **Scoring itself is a rule, not a model** (`decisions.md:868`) — classify with the LLM,
  score with `config/scoring.yaml`. The LLM does the reading; the arithmetic is auditable.
- Where no deterministic path exists and you say so: DeepMind's arXiv pages
  (`decisions.md:1885`), confirmed live.

Fallbacks to state plainly: provider choice is a shim (`research/announcements/providers.py`)
so re-testing a cheaper model is a flag, not a rewrite; byline LLM is the fallback *from*
the parser, not the primary.

**Prompts in repo:** 12 files, versioned — `announcement_scoring/v1–v7`,
`byline_extraction/{v1,v2,deepseek_v1,openai_v1}`, `summarisation/v1`. The brief asks the
doc to explain the rationale behind how they were written, not to reproduce them: v4
removed `is_signal`, v7 is the current classifier, and v2 vocabulary came from *measured
confusions* (`decisions.md:1575`).

---

## §4 Evaluation — 1.5pp

The brief wants: extraction quality, hallucination control, scoring validation, and your
ground-truth approach. You have a genuinely defensible answer to all four *and* a candid
account of what it can't do — which is worth more than a better-sounding number.

**Ground truth: there isn't any, and the doc says so** (`decisions.md:1309`). Human
labelling was dropped because it was not going to happen — 20 articles at ~14,000
characters is most of a working day. An empty `gold_human/` folder was deleted rather than
left implying a validation step that doesn't exist. Quote that reasoning; it's the kind of
thing they mean by "where did you cut corners and why".

What replaced it: **adjudicated** labels. Classifier `sonnet-5`, adjudicator `opus-5`
reading the whole article plus *two* independent classifier runs. Four properties make it
better than self-grading (two runs visible so instability routes attention; quotes
mechanically verified first, so the judgement is only ever whether a quote *supports* a
tag; every verdict carries a spot-checkable reason; the adjudicator is held to the same
quote gate and the build fails if its quote isn't in the document).

**Two limits, printed by `gold_metrics.py` on every report so they can't be quoted
without them**: the adjudicator wrote the vocabulary it judges against, so a signal both
models miss stays invisible (4 `vocabulary_gaps` were found this way); and the adjudication
was built from a run, so that run scores optimistically — a baseline for the next run, not
a report card on the one that produced it.

**What it found — the argument for doing it at all.** 7 of 20 articles confirmed entirely,
13 with departures, 21 tag-level verdicts. Both largest corrections were **under-scoring,
on the articles a fund would most want to get right**: a US directive forcing a lab to
disable two frontier models went 13.3 → 80.0 (the model called "low" a document saying the
standard would "essentially halt all new model deployments"); OpenAI reaching GA on AWS,
with Amazon at 11.03% of the fund, went 0.0 → 26.7. *Accuracy is not uniform across the
score range, and the errors concentrate at the top of it.* That sentence is the finding.

**Drift is the production half** (D35, D45): the gold set is re-scored every firing,
mechanism micro-F1 plotted on the ops tab, floor breach raises a system alert — and
`drift_unavailable` fires CRITICAL when the check runs and measures *nothing*, which is
what D45 caught. Standing cost ~$5.85/month, and it buys no product output at all;
justify it as the cost of catching silent degradation.

Also available: the blind test of the adjudicator (`decisions.md:1373`), and the gold set
rebuilt to actually sample the corpus (`decisions.md:1159`).

---

## §5 Cost — 1.0p

`docs/cost.md` is already this section; it needs compressing, not writing.

- **Running total $16.19** against the €100 budget. Lead with that — spending 16% of the
  allowance and instrumenting every call is itself the finding.
- **$0.0276 per article classified**, calibrated against a $0.029 seed in
  `budget.DEFAULT_CALL_USD`. Drift runs dearer at $0.0325/item because it bypasses the
  cache and pays a cache *write*.
- **Cache-write economics as a design driver** — prompt caching on the classifier
  (`decisions.md:1531`).
- **How cost shaped choices** (the brief asks this explicitly, and it's the part people
  forget): the 3-vote consensus is a 3× cost decision taken deliberately for
  reproducibility; Haiku was rejected *despite* being 4.3× cheaper; byline extraction
  stays deterministic at $0.00 versus $0.86 for 17 pages; the Batches API path exists at
  50% for backfills; drift is named as "the first dial to turn" if the €20/month ceiling
  binds.
- **$0.53 was the price of finding a real bug, and it was worth it** — the D24 probe.
  Best line in the cost log.
- Honest caveat already recorded: GPT-5-mini pricing is marked UNVERIFIED in
  `providers.py`; token counts are exact, the dollar conversion wasn't checked.

---

## §6 How you worked — 1.0p — **THIS IS THE GAP**

`docs/agentic-log.md` was specified on day one and never created. Everything else in this
pack is extraction; this section needs reconstruction, and it's a required section
("your own agentic setup, what your agents verified themselves, where the loop broke
down"). I'll assemble a draft from git history and the decision log for you to correct —
but the judgement calls below are yours and only you know them:

Recoverable from the repo, and I'll pull it:

- **The setup**: `.claude/CLAUDE.md` as a working contract, `docs/planning.md` as the
  reasoning behind it, a custom `bitcap-reviewer` subagent for independent review, git
  worktrees for parallel agent sessions.
- **What the agents verified themselves**: 947 tests. Three independent review passes
  where an agent reviewed another agent's work and the findings were fixed —
  `decisions.md:1693` (the first code review), **D21 (9 findings)**, **D29 (one CRITICAL,
  eleven MAJOR)**, **D43 (five real defects in the pipeline tab and login)**. That is a
  real, quantified agentic QA loop and it's unusual to have it logged.
- **Where the loop broke**, with concrete instances already in the log:
  - Runs 16 and 17 both **failed because `uvicorn --reload` restarted the server while
    Claude was editing files** — the agent's own edits killed the agent's own run. It's
    in `bitcap-db status` right now.
  - D22's research pages "were asserting evidence that does not exist" — Anthropic-shaped
    prose generalised to every lab. An agent confabulating structure across labs.
  - D10's scoring off-switch: a safety measure an agent respected so faithfully that four
    labs went unclassified for weeks and the register looked complete.
  - The `is_signal` hypothesis being wrong while the fix worked anyway.

Only you can supply: which parts you'd do by hand next time, where you had to stop an
agent, how much of the plan survived contact, and whether the worktree parallelism paid.

---

## §7 What works, what's next, 3–5 insights — 1.0p

`docs/insights.md` has eight. The brief wants 3–5 and calls them "your proof that it
works", so pick for *range* — one product, one domain, one engineering:

Strongest three, in my order:

1. **One xAI announcement fans out to seven holdings on a single sentence.** "Grok 4.5 was
   trained across tens of thousands of NVIDIA GB300 GPUs" fires `training_compute_up` at
   0.67 against NVIDIA, TSMC, Micron, Marvell, Amazon, IREN and TeraWulf — accelerator,
   foundry, memory, interconnect, cloud, and two power names, each with the same verbatim
   quote. It's the mapping layer doing exactly its job **and** the clearest noise risk in
   the product, and it's the argument for grouping the digest by *event*. A finding that
   cuts both ways is the most credible kind.
2. **The register found four possible researcher moves without being asked to** — and
   found them *because* it refuses to merge people across labs. Ties directly to the
   brief's "a key researcher quietly moving to a competitor." **Now reproducible from
   the database rather than quoted** (`/register`, D49), which is the difference
   between a finding and a feature — and the number to quote is the filter's, not the
   finder's: *50 cross-lab names, 4 shown, 46 dropped as drive-by contributors.*
3. **BIT's biggest positions are pinned by regulation, not conviction** — all six funds sit
   just inside the UCITS 5/10/40 limits, largest position anywhere 10.43%. If so, the
   product's job isn't to rank conviction but to say which fund has headroom and what a
   purchase displaces. That's a domain insight that reframes the product, and it's the
   one most likely to start a conversation on the on-site.

Runners-up if one of the above doesn't land: BIT's own audited report containing an
entity-resolution failure (ISIN `AU0000185993` listed twice either side of the Iris
Energy → IREN rename — a live argument for identity-by-identifier in your own register);
the same analysis reversing sign depending on window (4 quarters: relentless sellers;
12 quarters: net accumulators, Amazon +479%); the people register being 4,927 people of
whom only 630 rows are `confirmed`.

**What's next** — be specific and short. Candidates you can defend because you decided
against them *on evidence*, not by omission: X (spike never run; state the coverage gap
and the €100 argument), embedding-based near-duplicate detection across lab and
researcher posts, the two-tier Haiku/Sonnet classifier the model work already scoped,
5-or-7-vote GPT-5-mini which would still undercut Sonnet on price, and the five items
parked in planning §13.

---

## Sequencing note

Sections 5 and 3 are nearly transcription — start there to build momentum, they'll take
under an hour each. Section 6 is the one that needs you and can't be rushed. Section 2 is
where you'll overrun; decide your eight entries *before* writing, not during.
