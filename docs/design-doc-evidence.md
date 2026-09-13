# Design doc — evidence pack

**Not a draft.** Material sorted into *your* outline, in your order, so no section starts
from a blank page. You write the prose.

Hard constraints from the brief: **max 10 pages**, every claim carries *decision →
rationale → consequence*, "if a paragraph could have been written without building the
system, cut it", and it must not read as AI-written.

**Two rules for using this file.** Decisions go **inline, in the section they belong to**
— not in a section of their own. And write only what you can defend without notes; if an
agent noticed it and you didn't, cut it however good it looks.

**Refreshed 2026-09-06 against `907fde3` (D67).** The 09-05 version was organised by the
brief's seven questions; this one is your nine sections. Same material, re-sorted, plus
the corrections below. Old version in git history if you want it.

| § | Your section | Pages | State |
|---|---|---|---|
| 1 | Overview of the app | 1.5 | Strong — one diagram, plus the 3 insights |
| 2 | Principles of design | 0.5 | Strong — this is where "one core, two renderers" lands |
| 3 | Scoring mechanism | 1.5 | Very strong — the richest material you have |
| 4 | Sources | 0.75 | Strong — four legs, one interface |
| 5 | Pipeline | 1.25 | Strong — needs the ETL diagram |
| 6 | System health | 1.5 | Strong, and more honest than it was |
| 7 | Model selection | 1.25 | Very strong — four model roles |
| 8 | Development cycle | 1.0 | Needs your `[NEIL]` answers in `agentic-log.md` |
| 9 | Next steps | 0.75 | Strong — decided against on evidence, not omission |

**Where the brief's questions land in your order**, so you can check coverage without
reorganising: architecture → §1, §5. Decisions and rejected alternatives → inline
everywhere, and the "rejected" line of each block below is what a grader is looking for.
Model choices and agent-vs-deterministic → §7. Evaluation → §6. Cost → §5 and §7.
How you worked → §8. What works / what's next / 3–5 insights → §1 and §9.

---

## §1 Overview of the app — 1.5pp

### The spine, as built

```
config/*.yaml
    │
    ├── announcements (4 discovery methods + 2 channels) ─┐
    ├── papers (6 lab harvesters, abstracts) ─────────────┼→ raw_articles
    ├── github releases (per-org, star-ranked) ───────────┤
    ├── github people/repos ──────────────────────────────┤
    └── X posts (lab leads) ──────────────────────────────┘
                                                          ↓
        classify — sonnet-5, announcements at v9 / papers at p1 → raw_llm_responses
                                                          ↓
              transform → articles + classifications + three tag tables
                                                          ↓
              group (embed → band → adjudicate) + link → article_groups, article_links
                                                          ↓
       connect (deterministic, 4 routes) → connections → ┬ investment view
                                                         └ AI-team view
```

### Numbers — RE-PULL BEFORE QUOTING

The corpus below is `bitcap-db status` at 09-05 plus what landed after (posts, lab
leadership, repo filter, D63–D67). **Run it again before you write.** Corpus split
verified 09-06: **304 announcements · 238 posts · 155 github releases · 47 papers**.

Per-lab announcement coverage, 09-06, all starting 2026-06-08:
openai 163 · google-deepmind 42 · anthropic 35 · xai 33 · meta-ai 20 · mistral 9 ·
deepseek 2.

At 09-05: 1,047 connections, 26 holdings, 1,862 cost rows, 8 gold snapshots, 36 digests,
228 alerts, 1,419 tests, 17 prompt files across 5 families. Bands: 28 high · 10 medium ·
57 low · 599 none — **the mass at `none` is the filter working**; say so before they ask.

### The four surfaces

Dashboard (the corpus) · `/digest` (the cut, per audience) · `/register` (who is tracked,
and the moves in it) · `/ops` (can I trust what the other three say).

### What it surfaced — the 3 insights

The brief calls these "your proof that it works" and wants 3–5. They're here rather than
at the end because this is the question they open the document to answer. Move them if
you'd rather, but don't bury them on page 9.

**1. The efficiency claim that exists only in the paper.** DeepSeek-V4's abstract, scored
100.0, quoted verbatim: *"In the one-million-token context setting, DeepSeek-V4-Pro
requires only 27% of single-token inference FLOPs and 10% of KV cache compared with
DeepSeek-V3.2."* KV cache is HBM-resident, so a tenfold reduction per served token is a
first-order claim about memory demand — **the DeepSeek→NVIDIA transmission the brief
names as its own calibration case, in numbers rather than inference**. Routes to NVIDIA,
Micron, TSMC, Amazon, TeraWulf and IREN at strength 1.00; DeepSeek-V3's report routes
*negative* to the same names. The announcements leg structurally cannot reach it.
DeepSeek-R1 scores 100.0/100.0 — the brief's calibration case recovered from an abstract,
unprompted. The counterweight matters as much: **33 of 47 papers score zero for
investors**, including *"A moral Turing test"*. If those scored, they'd bury the eight
that matter.

**2. A model name in the lab's own code before any announcement — one instance, not
four.** `gpt-5.6-luna` appears 2026-08-11 in `openai-agents-python` v0.20.0, as the
Agents SDK default, and **nothing in the announcements corpus names it**. OpenAI is the
best-covered lab in the corpus (163 announcements), so this is not a coverage hole.

> **Corrected 09-06 — the original version of this insight claimed four instances and
> was wrong.** `gemma-4` was claimed at "nearly four months early". The announcement
> exists: *"Introducing Gemma 4 12B"*, 2026-06-09, against the repo tag at 2026-05-13 —
> **27 days**. The four-month figure was an artifact of the DeepMind sitemap gap, and
> D66's own list of the 18 launches the sitemap missed names "Gemma 4 12B" outright. The
> insight predates the fix and was never re-run. `gpt-6-astra` is **same-day**: codex
> release and announcement both 2026-09-03, and releases carry timestamps while
> announcements carry dates, so ordering inside the day is not recoverable. `gemma-3` has
> no hits in the corpus at all.
>
> **The structural limit is the better paragraph.** The announcements corpus starts
> **2026-06-08** for every lab; github releases reach back to **2019**. Anything named in
> code before June 2026 is first-in-code *by construction*. Say this — it is the honest
> bound on the method, you found it yourselves, and the claim does not survive the
> question "how far back does your announcements corpus go?" without it.

**3. The register found four possible researcher moves without being asked to** — and
found them *because* it refuses to merge people across labs (D25, D49). A move is one
name with two records, so finding one is `GROUP BY canonical_name HAVING count(distinct
lab) > 1`, no model involved. Then the evidence gate does the taste: **50 cross-lab names
exist, 4 are shown**, 46 dropped as GitHub logins with no lab-owned email. Ties directly
to the brief's "a key researcher quietly moving to a competitor", and is reproducible
from the database rather than quoted.

**Held back if you want a fourth:** BIT's biggest positions are pinned by regulation, not
conviction — all six funds sit just inside the UCITS 5/10/40 limits, largest position
anywhere 10.43%. If so, the product's job isn't to rank conviction but to say which fund
has headroom and what a purchase displaces. Most likely to start a conversation on the
day. Also available: one xAI announcement fanning out to seven holdings on a single
sentence (*"trained across tens of thousands of NVIDIA GB300 GPUs"*) — which cuts both
ways, and is the argument for grouping by event.

---

## §2 Principles of design — 0.5pp

Short section, high value. Four claims, each with its decision inline.

- **Scope is BIT Global Technology Leaders only** (D8), and concentration is measured
  against fund NAV, never against the 13F (D4). A deliberate narrowing — say why.
- **Register admission is decided by transmission path, not by lab prominence** (D7,
  amended by D14 for Meta). This is the principle that makes the register defensible
  rather than a list of famous labs.
- **One shared core, two renderers** — the brief's explicit requirement, and a graded
  point. The join is computed once in `app/connect.py`; the audience split is a
  client-side framing of the same rows, not a second pipeline. **Papers are the proof**:
  they share `raw_articles`, the JSON schema, `vocabularies()`, `drop_unknown_tags`,
  `enforce_quotes`, `call_cost`, `scoring.yaml`, `app/scoring.py`, `classifications`,
  all three tag tables, `connect`, `digest`, `/api/items` and the dashboard. **One prompt
  file and one work-list filter differ** (D57, which explicitly supersedes D25).
- **Config, not code** — adding a lab, person or source is a YAML change, validated on
  load rather than trusted (D2). Non-negotiable in the contract; one line proves it.
- **Explainability** — every connection carries the route that fired it and every tag
  carries a verbatim quote. The UI shows *why*, not just *that*.

**Four table layers with different truth-guarantees**, which is what makes `rebuild`
safe: raw (verbatim, upsert-only), reference (YAML is source of truth), clean (derived,
droppable), ops (never dropped, irreproducible). If you use medallion vocabulary, use it
precisely: raw is bronze, clean is silver, and `connections` / `digests` / `alerts` /
`gold_snapshots` are gold. **The boundary is not aggregation — it is immutability of what
was served.** Silver is a pure function of `config/scoring.yaml`, so every score moves
when the config moves; what the product *published* and *alerted on* cannot be recomputed
after the fact and therefore survives a rebuild.

---

## §3 Scoring mechanism — 1.5pp

Your richest material. Ordered as your outline has it.

### Mechanisms, confidence, weights

**Scoring is a rule, not a model** (`decisions.md:868`) — classify with the LLM, score
with `config/scoring.yaml`. The LLM does the reading; the arithmetic is auditable and
every score moves when the config moves. This is the single most defensible design choice
in the project and it belongs early.

**Four connection routes**, each carrying why it fired: `mechanism`, `category`,
`lab_exposure`, `named`. AI compute demand and crypto demand are separate categories, and
categories may overlap (D9).

**The verbatim quote gate** (`decisions.md:1241`) runs *before* any judgement, and the
adjudicator is held to it too — the build fails if the adjudicator's own quote isn't in
the document. The number that proves it works: **v7→v9, 237 of 470 quotes unresolvable →
0 of 523.** Non-negotiable #1 in the contract; this is where it's evidenced.

### Events, and grouping

**Grouping and linking are two different operations, deliberately** (D61). Union-find is
transitive, so feeding releases into it pulls the Astra launch, the safety overview *and
two customer stories* into one group. `config/dedupe.yaml` states the asymmetry that
decides it: **a missed merge leaves a visible duplicate row a reader can see; a false
merge silently deletes a document.** Only one is recoverable by the reader. So pairing
writes `article_links` and never touches `article_groups`. 647 grouped rows → 547 groups;
25 links.

**Deduplication is deterministic first, and a model only inside a band** (D59). Three
gates: subject (identifier match, free), event type (must agree, free), redundancy
(cosine, then an LLM only in `[0.70, 0.80)`). Only the last costs money, and it runs on
19 pairs out of 4,412 candidates.

**Rejected: mechanism overlap as the duplicate signal.** It reads as the natural choice
and is wrong — *"TCS and Anthropic bring Claude to regulated industries"* vs *"DXC
integrates Claude into systems"* scores a perfect Jaccard 1.0 and they are two different
partnerships. The vocabulary encodes what *kind* of event an item is, not *which* event.

**Rejected: papers into grouping** (D60) — costed and declined. 230 same-lab paper pairs,
**zero duplicates**, and the event-type gate rejects only 1 pair in 41 because 34 of 47
papers classify `research_result`. The whole burden would fall on a threshold calibrated
on a corpus where that filtering had already happened.

### Practices (the AI-team axis)

The AI-team score was wrong because the prompt never described what it was reading — it
read practice `confidence` as *"is the claimed benefit proven?"*, and a spec sheet never
publishes benchmarks, so the tag could not win. Fixed in v8. This is a good three
sentences on calibration vacuums: the model will fill an unstated question with the wrong
one rather than refuse.

### Prompt versioning, and re-scoring on change

17 versioned files across 5 families: `announcement_scoring/v1–v9`, `paper_scoring/p1`,
`duplicate_adjudication/{v1,v2}`, `byline_extraction/{v1,v2,deepseek_v1,openai_v1}`,
`summarisation/v1`. The brief wants the *rationale behind how they were written*, not the
prompts reproduced:

- **v2's vocabulary came from measured confusions** (`decisions.md:1575`), not from a
  brainstorm.
- **v4 removed `is_signal`** — adopted, but **the hypothesis was wrong**. Keep it for
  exactly that reason; negative results read as honesty and cost no space.
- **v8** fixed the calibration vacuum above.
- **`p1` reworded `research_result`** because a technical report *is* how a model was
  launched.
- **`duplicate_adjudication/v2`** names "companion pieces" as a category, because the
  human spot-check showed v1 had no worked example of that shape (§6).

**Two failures worth three sentences each, because nobody thinks to check for them:**

- **A prompt edited in place** left a label file no longer described by the file named
  `v1`. All three label sets now carry a `prompt_version` stamp instead of being
  identified by filename (D59d).
- **`build_prompt` sent `PROMPT.read_text()` verbatim, and markdown comments are not
  stripped** (D56a) — so v9 carried 686 characters v8 did not, including a sentence
  telling the model that OpenAI's articles *"are now archived full text"*: a leading claim
  about the input, pointing in the direction the scores moved. The guard that was supposed
  to verify the prompts matched split them on different delimiters and removed precisely
  the delta, so **it checked its one precondition vacuously**. Consequence: **the v8→v9
  comparison is two-variable, not one.** The verbatim audit survives, because it's
  mechanical.

**Re-scoring when the prompt changes — and the two times we didn't.** The rule is that a
version change re-asks the question of the whole corpus. Both exceptions are costed:
papers carry their own `p1` rather than forcing a `v10`, because sharing would have
re-asked an unchanged question of 647 unchanged rows at **~$16.70**; and release
classifications were carried forward rather than re-run, **~$10 not spent**, with the
honest cost recorded — see the 22% finding in §7, and every carried row stamps
`_carried_forward` with its source version.

---

## §4 Sources — 0.75pp

Resist writing four mini-sections. One paragraph on the shared spine, then what differs.
**Four discovery methods behind one interface** `(lab, cutoff) -> list[dict]`: `sitemap`,
`rss`, `listing_pagination`, `wayback_cdx`, plus `model_index` and `discourse` as second
channels (D46, D47). New sources plug in without surgery; that's the claim to make.

| Leg | How it's selected | How it's scored |
|---|---|---|
| Announcements | 4 discovery methods + 2 channels | sonnet-5, prompt `v9` |
| Papers | 6 lab harvesters, **abstract not full text** | sonnet-5, prompt `p1` |
| GitHub releases | per-org, star-ranked, **repo filtered on topic** (D65) | carried / `v9` |
| X posts | lab leads only, tiered by evidence (D63, D64) | — |

**Announcements — the silent-omission decisions.** OpenAI's own feed omitted its flagship
launch (D46), which is why a second channel exists at all. The forum became a third
channel and some things stay unreachable, stated rather than papered over (D47). And
**DeepMind's sitemap was the wrong document, not a stale one** (D66): sitemap 12, RSS 30,
the sitemap's 12 a strict subset — and the 18 it missed *were the launches* (Gemini 3.6
through 3.8, Gemma 4 12B, DiffusionGemma, Nano Banana 2 Lite, Robotics ER 2). Every
observable said it was working: 200s, clean parse, real dates, everything in window.
**Rejected: keeping the sitemap as a second channel** — it buys nothing when the in-window
set is a strict subset, and costs ~276 article fetches a run to confirm that. Meta was the
opposite finding and worth stating because the instinct was to fix it: crawled to
exhaustion, 231 articles back to 2019, exactly 5 in window, the register held all 5. **The
blog was quiet; nothing was broken.**

**Papers — abstract, not full text, measured before deciding** (D57). Five arXiv `/html/`
pages average **182,974 characters ≈ 59,200 tokens, ~43× the mean article** (4,290). Full
text $11.30, abstracts **$0.75 actual**. Cost is not what decided it — every number that
made these papers worth scoring is in the abstract. The honest cost: a figure stated only
in a results table is invisible to us, which is why `paper_abstract` is its own
`text_source` rather than being passed off as `full_text`.

**GitHub — the filter is the interesting part** (D65). 39 of 87 watched repos are
off-topic for an LLM tracker — mujoco, alphafold3, habitat, detectron2 — and they scored
*as high as* the on-topic ones (`alphafold3` averaged 35.6 on the AI axis, third of all
87). Relevance is orthogonal to the practice score, so no threshold fixes it. Judge the
**repo**, not the release: 87 calls once, not 380 forever. Also: first mentions are a pass
over bronze, not a prompt (D52) — see §7.

**X posts — the open question that got answered** (D63, D64). Planning §7 asked whether
X earns its cost; it now has a corpus (238 posts) and six handles promoted off the weak
tier with one flagged. Say what the tiering is for, not just that it exists.

**The corpus window is a read cut and nothing else** (D61). `display.corpus_window_days:
90` exists because most releases predate 90 days while announcements already sat inside
it. Grouping, pairing and first-mention still read the whole archive — `first_mention` is
pinned by a test that stops the window ever reaching it, because firstness is a claim
about the entire corpus and windowing the input reports a name as **new when it is not**.

---

## §5 Pipeline — 1.25pp

### ETL, and what runs when

The diagram from §1 does double duty; don't draw a second one. What's worth saying here
is the operational shape, because non-negotiable #5 grades it: retries, backoff, per-source
rate limits, persisted run state, idempotent re-runs.

- **The orchestrator drives labs, not legs, and counts failures rather than backing off**
  (D23). A deliberate choice with a consequence — say what it costs.
- **Ingestion is incremental because the deployed shape has no disk** (D31), and GitHub
  got the bronze layer it never had, stopping a re-walk of a year of commits (D32).
- **An article found tonight was not scored until tomorrow** (D38) — the fix is why
  classification reads bronze rather than a corpus file (D53).
- **A firing that reports nothing for nine minutes is indistinguishable from a broken one**
  (D41). Progress reporting is a design requirement, not a nicety.
- **Reaping**: a killed run leaves a `running` row, and one corpse blocked every future run
  until D43 added reaping.
- **`bitcap-db rebuild` reproduces the whole database from committed artifacts with no API
  key** — this is what makes "clone-to-running in a few commands" true, and it's a
  non-negotiable. Check the README still holds.

### Caching, which is three decisions not one

- **Don't cache the page; don't ask for it twice** (D33).
- **The page cache had no expiry, and it was caching the sitemaps** (D36) — discovery froze
  while every fetch reported success.
- **The papers cache never existed where it mattered** (D53): `.gitignore` line 156,
  `.dockerignore` line 7, a Render cron with no disk. Four of six papers sources died to
  arXiv 429s in one firing. Moving it to Postgres was the easy half. The interesting half:
  a permanent URL-keyed cache would have **frozen discovery** — `meta_harvest` caches its
  listing pages and `deepseek_harvest` caches the `au:"DeepSeek-AI"` query, the two URLs
  whose entire purpose is to return something different the day a new paper appears. **Two
  cache classes**: a versioned arXiv id is immutable (`expires_at = NULL`), everything else
  carries a TTL. Steady state ~70 live arXiv requests a night → ~8. D67 then broke the herd
  on the remaining ones.

### Cadence, and running it by hand

Keep this to four lines — it's README material, and the brief's page budget punishes it.
What *does* belong: **a digest is a dated cut, and the cut is the product** (D48).
Suppression count rendered as prominently as the items: **84 considered → 8 surfaced over
30 days.** The window is half-open because closed bounds put fourteen articles in two
editions each; **periods are a fixed grid anchored at the epoch**, not an offset from
whenever a run started, which is what makes the idempotence key able to collide at all;
and the preview is allowed to roll *because it writes nothing*, while `publish` must
quantise. Two filters the reader actually arrives with (D50).

### Cost

`docs/cost.md` is already this section; compress, don't write.

- **Running total ~$18.20** against the €100 budget. Lead with that — under a fifth of the
  allowance, with every call instrumented, is itself the finding.
- **$0.0276 per article classified**, calibrated against a $0.029 seed in
  `budget.DEFAULT_CALL_USD`. Drift runs dearer at $0.0325/item because it bypasses the
  cache and pays a cache *write*.
- **647 articles embed for under a tenth of a cent** ($0.000682) — because what's embedded
  is title + classifier summary, not the body (37k tokens against ~700k, and the bodies are
  mostly site chrome that inflates similarity between any two pages from one lab).
- **Two guards that fired, with the money attached.** D24: a budget ceiling replaced the
  scoring off-switch, and it stopped the run **after spending $0.53 against an $0.08
  ceiling** — 6.6× over, because 12 concurrent workers each tested a total none of its
  peers had contributed to yet. Fixed by counting in-flight calls; the re-probe overshot
  5.6%. *The price of finding a real bug, and it was worth it.* Then **the same bug was
  reintroduced in `drift` next to its own fix**, at $13.00 against a $2.00 ceiling (D43).
  Pair D24 with the fact that the off-switch it replaced had silently hidden four labs from
  the product (D10).
- **Calibration is a build cost, not a running one** (D59). Duplicate adjudication was
  billed three times — original thresholds, after the ordering leak, after the human
  spot-check — for $0.1834 total. A production pass is 19 pairs, about six cents.
- **A feature that cost nothing** (D61): release↔launch pairing is regex and set
  intersection over rows already in the database. No entry in `cost.md` at all.
- **Honest caveats already recorded**: GPT-5-mini pricing is marked UNVERIFIED in
  `providers.py` (token counts exact, dollar conversion unchecked); and the release
  carry-forward wrote outside any tracked run, so *"~$10 not spent"* holds locally and
  nowhere else — a deploy to a fresh database, or any `rebuild`, spends it.

---

## §6 System health — 1.5pp

Your outline has one section here. **Keep it as one section but split it into two
questions**, because the contract says these are distinct problems and conflating them is
a graded mistake:

> **(a) Does it make things up?** — extraction quality, citations, attribution.
> **(b) Is it calibrated?** — scoring validation against a reference set.

### (a) Hallucination control is mechanical, and it has a number

The verbatim quote gate (§3) runs before any judgement, the adjudicator is held to it too,
and the build fails if the adjudicator's own quote isn't in the document. **v7→v9: 237 of
470 unresolvable → 0 of 523.** That is the whole answer, and it's mechanical rather than
model-mediated, which is why it's trustworthy.

### (b) Start with the correction, because it's the most honest thing in the section

**Neither gold set is human ground truth**, and an earlier version of this project's own
notes said otherwise. `gold_human/` was deleted 2026-09-01 because labelling twenty
~14,000-character articles by hand was not going to happen.

| set | labeller | mode | n |
|---|---|---|---|
| announcements | `claude-opus-5` adjudicating `claude-sonnet-5` | sees the classifier's output + a second run | 20 |
| papers | `claude-fable-5` | **blind** — sees only `text`, `title`, `url`, `text_source` | 10 |
| dedupe pairs | `claude-fable-5`, then a human spot-check | blind, shuffled | 82 (+20 human) |

Both model sets are the defensible proxies the brief permits; **neither may be described
as human-labelled anywhere in the document**. `load_gold` raises on a file missing
`gold.labelled_by`, so a set whose provenance nobody recorded cannot reach a metric. Blind
labelling means more (it cannot anchor) and buys less (no second run to show where the
scorer was unstable, no recorded reason for rejecting a tag).

**What the adjudication found — the argument for doing it at all.** 7 of 20 confirmed
entirely, 13 with departures, 21 tag-level verdicts. Both largest corrections were
**under-scoring, on the articles a fund would most want to get right**: a US directive
forcing a lab to disable two frontier models went 13.3 → 80.0; OpenAI reaching GA on AWS,
with Amazon at 11.03% of the fund, went 0.0 → 26.7. *Accuracy is not uniform across the
score range, and the errors concentrate at the top of it.* That sentence is the finding.

Two limits printed by `gold_metrics.py` on every report so they can't be quoted without
them: the adjudicator wrote the vocabulary it judges against (4 `vocabulary_gaps` were
found this way), and the adjudication was built from a run, so that run scores
optimistically.

### The paper baseline (D58), measured once, deliberately not recurring

```
EVENT TYPE     9/10 = 90%       Cohen's kappa +0.787
axis          ref  run  hit  microF1  macroF1  identical
mechanisms      4    3    3     0.86     0.97      9/10
practices       9   13    8     0.73     0.77      5/10
investment    MAE  3.3    zero-vs-nonzero 10/10    both zero 7   identical 9/10
AI-team       MAE 12.8    zero-vs-nonzero  9/10                  identical 3/10
CITATION GATE  16 tags survived, 1 dropped for an unverifiable quote
```

**Mechanism precision is 1.00** — on the axis that routes to holdings, where a hallucinated
tag would be most expensive, no false positives. And **the noise filter holds**: all seven
papers whose correct investment score is zero scored zero.

**On micro-F1, and why it isn't the headline.** Micro-F1 pools every tag decision across
all items into one precision/recall pair — it answers "of all the tags it emitted, how many
were right, and of all the tags it should have emitted, how many did it find". Macro-F1
averages per-item instead, so a single item with one tag counts as much as one with ten.
The headline here is deliberately `investment.zero_agreement` and **not** mechanism
micro-F1, because seven of ten pairs are empty-vs-empty and contribute to neither numerator
nor denominator: micro-F1 would grade the three papers we're least worried about and be
blind to the seven the corpus exists to pin. **Explaining that choice is worth more than
explaining the metric.**

**Every disagreement was pre-identified by the labeller as genuinely arguable** — the one
event-type split (`open_weights` vs `frontier_model_release`, both weight 5, so it costs
nothing in score) was flagged as a three-way tie before the comparison ran. What that does
**not** establish is corpus-level precision and recall: n=10, stratified rather than
proportional.

### The finding that should probably lead the section

From `agentic-log.md`: the gold set carries its own copy of each document, and **11 of the
20 were 2×–98× richer than what the pipeline actually scored** — 13,283 characters against
249 on the same article. Gold agreement was being measured on text production never sees,
so the drift check would have reported health indefinitely while the product degraded. *An
eval that reads different bytes than the product is not an eval*, and nothing in the design
said so out loud.

### The human spot-check, and why the direction mattered more than the rate (D59b)

You marked a blind stratified sample of 20 dedupe pairs: **15/20 = 0.75**. The finding is
not the rate — it's that **all five disagreements ran one way**. The labeller was
systematically too conservative on *companion pieces*: an umbrella announcement and its
named strand, an introduction and its deep-dive, one rollout staggered across surfaces. The
v1 prompt carried no worked example of that shape; every example in it was about telling
things apart, so that is what it kept doing. Ten minutes of marking changed a prompt, a
threshold, and a recommendation about a $7.50 spending decision elsewhere. Reported as a
rate alone — *"0.75, acceptable"* — none of that surfaces. **This is the brief's "examine
the disagreements rather than averaging them away", demonstrated on a set of five.**

It also independently confirmed two things the system had only argued for: you marked the
Astra launch against the safety overview as `different`, which is exactly what gate 2 exists
to protect; and marked both leaked-label pairs the way the *corrected* pass did, which is
the evidence `cosine_high` belongs at 0.84.

**And the eval that graded itself** (D59c): the v2 rubric was reported at 0.90 against your
20 marks. Seven of those 20 are worked examples *inside v2's own prompt*. Split out:
in-sample 3/7 → 6/7, **out-of-sample 12/13 → 12/13**. Every point of the gain was
in-sample, and v2 adds one false merge v1 did not make. The defensible claim is *"v2
corrects the errors it was shown, and its behaviour on unseen pairs is unmeasured."*

**The blind file that wasn't blind** (D59b) is the companion story: sorted by cosine
descending with the number withheld — the same information one transform away. Re-labelled
from a shuffled file, two labels flipped, **and they were the top two negatives**;
`cosine_high` moved 0.87 → 0.84 and precision-1.0 recall went 0.20 → 0.43. Both files are
kept, because the difference between them *is* the evidence.

### Checking a run, and the two alert kinds

**Two kinds of alert, and the dedupe key is the actual design** (D26, D27) — content alerts
and system-failure alerts are separate channels, which is non-negotiable #5. **D49's
amendment is the sharper story**: acknowledgement is withdrawn by the pipeline, not trusted
to the operator, because the key holds still *during* an outage — so one click greened the
badge for an entire incident. And the test that "proved" otherwise hand-wrote two keys that
only ever occur when an outage ended and restarted.

**Drift is the production half** (D35, D45): the announcements gold set is re-scored every
firing, mechanism micro-F1 plotted on the ops tab, floor breach raises a system alert — and
`drift_unavailable` fires CRITICAL when the check runs and measures *nothing*, which is what
D45 caught: it returned zero comparisons on Render, recorded every number truthfully, and
reported **succeeded**. Standing cost ~$5.85/month, and it buys no product output at all;
justify it as the cost of catching silent degradation. The drift check went to the whole
gold set because six items measured its own noise (D35).

**Papers have no equivalent, knowingly** (D58) — and this is a decision, not an omission.
The paper gold set carries **4 mechanism tags**; D35 already rejected a sample carrying 10
on exactly this ground. Four tags moves micro-F1 by ~0.15 each, so the alert would fire on
jitter, and a false alarm a week is how a system-alert channel gets muted — which costs more
than the check is worth, because the announcements check shares that channel. **Extending
the gold set does not rescue it**: the whole 47-paper corpus carries 27 mechanism tags
across 33 papers with none. Papers are mechanism-sparse; that's a fact about research
papers, not a sampling defect.

---

## §7 Model selection — 1.25pp

### Four roles, and the fourth isn't a Claude model

| Task | Model | Why |
|---|---|---|
| Announcement classification | `claude-sonnet-5`, 3-vote majority, prompt `v9` | Only config that reproduced |
| Paper classification | `claude-sonnet-5`, prompt `p1` | Same model, new `text_source`, so a new version |
| Gold adjudication (announcements) | `claude-opus-5` | Different model from the classifier, by design |
| Gold labelling (papers, dedupe) | `claude-fable-5`, blind | Third model; blind labelling cannot anchor |
| Duplicate adjudication | `claude-sonnet-5`, `duplicate_adjudication/v2` | Only inside the cosine band |
| Repo relevance | `gpt-5-mini` (D65) | Cheap, high agreement, 87 calls once |
| Corpus embedding | `text-embedding-3-small` (OpenAI) | $0.000682 for 647 articles |
| Byline extraction (fallback only) | `claude-sonnet-5` | Deterministic parsing is primary |

### Why Sonnet — the variance finding is the centrepiece

Two full runs over 375 announcements **flipped `is_signal` on 9% of items**; a probe found
only 6 of 12 borderline items scoring identically, with one article ranging **17.8 to 80.0
across six runs**. There is no temperature to turn down — deprecated on the Claude 5 family,
verified against the API. So the fix had to be architectural: **3-vote consensus, a 3× cost
decision taken deliberately for reproducibility.**

**The second measurement is stronger, and it came from trying to justify a shortcut** (D56).
On the **78 corpus articles whose text is byte-identical between prompt versions and which
are scored under both**, v9 disagreed with v8 on the mechanism id set for **17 (22%)** and on
the band for **8 (10%)**. So *"same prompt plus same text implies same output" is false at
22%.* That killed the original justification for carrying release classifications forward
and replaced it with a narrower one that survives: **re-running buys a different sample from
the same noisy distribution, not a better one.**

### Rejected models, each with the measurement

- **Haiku 4.5** — 4.3× cheaper and *more* consistent (9/12 identical), but scored both S-1
  filings at 0.0, stably. *"Its apparent stability is partly confident wrongness."* That
  sentence is the whole argument; keep it verbatim.
- **GPT-5-mini for scoring** — 4.7× cheaper, but the vote didn't converge (4/6 reproducible,
  7 tags dropped vs 1). Disagrees *systematically*, not just noisily. **Note the pairing
  with D65**: the same model was then *adopted* for repo relevance, where the task is a
  one-shot judgement rather than a reproducible score. Same model, opposite verdict, for a
  stated reason — that's a good half-paragraph.

### Where we chose not to use a model at all

The brief asks for agent-vs-deterministic explicitly, and you have **measured** cases, which
is rare:

- **Byline extraction, measured not argued** (`decisions.md:335`). The eval found *more bugs
  in the deterministic parser than in the LLM* (three, all in the parser) — and deterministic
  still won, on reproducibility and visible failure, not on accuracy. F1s where both ran:
  0.978 / 0.996 / 0.879. 17 pages deterministic: **$0.00, under a second**. LLM: $0.86.
- **Scoring is a rule, not a model** — see §3.
- **First mention is a corpus pass, not a prompt** (D52). Firstness is a property of the
  corpus, not of any document, so no per-document prompt can recover it however it's worded.
  One query over `raw_articles`, no LLM call, and it gets *stricter* as sources are added
  rather than noisier.
- **Deduplication is deterministic outside a band** (D59) — see §3.
- **Release↔launch pairing costs nothing at all** (D61).
- **Where no deterministic path exists, say so**: DeepMind's arXiv pages
  (`decisions.md:1885`), confirmed live.

### The two small ones from your outline

**Why not summarise first** and **prompt caching** are a line each, not bullets — prompt
caching is why drift runs dearer than production ($0.0325 vs $0.0276, it pays a cache
write). Don't let them take a paragraph.

---

## §8 Development cycle — 1.0pp

`docs/agentic-log.md` is the source, in four dated sections plus two `[NEIL]` blocks. It was
specified on day one and not kept, and the first honest entry says so — keep that admission.

**The setup**: `.claude/CLAUDE.md` as a working contract (non-negotiables, design principles,
expected failure modes, and an explicit parked backlog agents are forbidden to touch);
`docs/planning.md` as the reasoning behind it; a custom `bitcap-reviewer` subagent running
Opus, **read-only by construction** (`Read, Grep, Glob, Bash, ReportFindings` — never Edit or
Write), briefed to review the branch as a PR and to check the repo's own contract rather than
general engineering standards; git worktrees for parallel sessions. **103 commits, 2026-08-24
→ 2026-09-05**, heavily back-loaded: 53 on the last day against 1–3/day for the first week.
The first week was research and manual reading.

**Plan → write → review → write → review → commit → PR**, then CI regression tests, then
merge to `deployment-dev` for local testing and `deployment` for automatic deploy. That's
your outline's shape and it's accurate; what makes it worth a page is the evidence below.

**What the agents verified themselves**: **1,419 tests**, and seven independent review passes
where one agent reviewed another's work with findings reproduced before being accepted (D21 —
9 findings, D29 — 1 CRITICAL and 11 MAJOR, D43 — 5, D51 — 10, D53b — 12, D54 — 10, D55a — 8,
D56a — 10, D59a — 14, D59d, D61). The three worth citing are the ones a human review misses:

1. `ensure_schema` failing on exactly the case its docstring claimed to handle — twice, in two
   different ways (D29, then D48).
2. Cost instrumentation with three holes, in a graded section (D29).
3. A test that walked the app's own route table and skipped the entire `/api/pipeline/*`
   subtree, because an `include_router` entry has `path = None` (D43).

**Where the loop broke** — more than you can use, so pick for range:

- **The agent's own tooling killed the agent's own runs.** Runs 16 and 17 both failed to
  `uvicorn --reload` restarting while Claude edited files, and a killed run leaves a
  `running` row, so one corpse blocked every future run until D43 added reaping.
- **Two correct scripts, one shared output key, silent loss — twice.** The OpenAI corpus
  overwrite (141 articles recovered from the Internet Archive, then rebuilt back to blurbs by
  the next day's fetch; nothing raised, 1,194 tests green, and OpenAI is 59% of the corpus),
  and then Mistral's paper citations *being* its announcement URLs, so landing the papers
  corpus overwrote two 13,000-character announcements with 2,000-character lead sections.
  **The second was caught only because the first taught us to read the insert/update counts** —
  `47 inserted, 2 updated` where 49 inserted was expected. D67 is the same shape a third time.
- **A check that was performed and not read** (D61). The agent had already run the query that
  would have exposed the broken join, and did not notice that the launch post it kept quoting
  was absent from its own output. 20 links, all correct, **a biased sample presented as a
  result**.
- **A test written from intent and never watched fail — three times, across three legs** (D54,
  D59d, D61). Each time the assertion was true with *or without* the thing it was written to
  defend. The most transferable lesson in the log, and the one standing rule worth proposing:
  *a new test does not count until it has been demonstrated red.*
- **Confabulated parallelism** (D22): Anthropic-shaped prose generalised to every lab. The
  failure mode is not inventing facts, it is inventing *structure*.
- **A safety switch that became a coverage gap** (D10): agents honoured a scoring off-switch
  so faithfully that four labs sat unclassified for weeks. *An agent will honour a constraint
  long past the point a human would have questioned it.*
- **An insight that was an artifact of our own coverage gap** — the `gemma-4` correction in §1.
  Written before D66 fixed the DeepMind feed, never re-run, and it would not have survived one
  question on the day. **You caught this, not an agent**, which is the point worth making.
- **Verification at n=1 catches "does this work at all", not "does this work for the cases I
  did not sample"** — and the second is where this project's failures actually lived.

**Only you can supply**: which parts you'd do by hand next time, where you stopped an agent,
whether the worktree parallelism paid. Those questions are written out at the two `[NEIL]`
blocks in `agentic-log.md`.

---

## §9 Next steps and improvements — 0.75pp

Be specific and short. These are defensible because each was decided against **on evidence**,
not by omission.

- **Close the corpus-window asymmetry** — announcements start 2026-06-08, releases reach 2019,
  and until those agree, "first seen in code" is only claimable inside the overlap (§1). This
  is now the honest top of the list.
- **Move scoring to the most common of three** — your own note. The tie-breaker is the real
  design question: with three votes and three different answers, either fall back to the
  median band, or emit the disagreement as a low-confidence flag rather than a score. Worth
  saying which and why.
- **A detector for the pairing recall bound** (D61), which currently has none — and the honest
  prerequisite is a labelled set, which is the cost of the detector rather than a detail of it.
  The realistic way it goes silently to zero is classifier drift, not a config edit.
- **A fresh 20-pair blind check on dedupe pairs the v2 prompt has never seen** (D59d) — the one
  thing that would turn "corrects the errors it was shown" into a claim about generalisation.
- **Papers into grouping**, which D60 costed and declined (§3).
- **Additional sources** — say which, and what transmission path earns them a place (D7).
- The five items parked in planning §13.

**One negative result worth three sentences somewhere in the document:** publication-derived
importance does not generalise across labs — it's Anthropic-specific (`decisions.md:693`,
planning §11b). Negative results read as honesty and cost almost no space.

---

## Claims you must not make

Every one was believed at some point in this project and is false. They're also the questions
most likely to be asked.

- ❌ *"The gold sets are human-labelled."* Neither is. Announcements are cross-model
  adjudication; papers and dedupe pairs are Fable 5, blind. Both are proxies and must be named
  as such wherever their numbers appear.
- ❌ *"Four model names appeared in the labs' code before any announcement."* One did. See §1.
- ❌ *"`gemma-4` appeared four months before any announcement."* 27 days, and the four-month
  figure was our own sitemap gap (D66).
- ❌ *"v9 isolates the effect of recovering the text."* It doesn't — v9 also carried a
  686-character comment telling the model the articles were now archived full text. Two-variable.
  The verbatim audit (0 of 523) is the part that holds.
- ❌ *"The v2 dedupe rubric scores 0.90."* That's a training score; 7 of the 20 marked pairs are
  worked examples inside the prompt. Out of sample it's 12/13, identical to v1, plus one false
  merge.
- ❌ *"Same prompt and same text gives the same output."* False at 22% on mechanism ids over 78
  articles.
- ❌ *"Deduplication merges papers and announcements."* It does not, by decision (D60).
- ❌ *"Releases and launch posts are merged."* They are **linked**, never merged (D61).
- ❌ *"Papers have a drift check."* They do not, by decision (D58).
- ❌ *"20 release links proves the join works."* It was a biased sample; the flagship example was
  missing from it.

---

## Order to write in

§5 cost and §7 are nearly transcription — start there to build momentum, whatever order the
finished document sits in. §1's insights are next, because they tell you what the rest has to
justify. §8 needs your `[NEIL]` answers and can't be rushed. §3 and §6 are where you'll
overrun; decide what you're cutting before you start writing, not during.

**Note on D-numbers**: D22, D52 and D53 each appear twice in `decisions.md` from a merge
renumber. Check which one you mean before citing.
