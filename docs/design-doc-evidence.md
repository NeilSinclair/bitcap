# Design doc — evidence pack

**Not a draft.** A map from the brief's seven required sections to the material that
already exists, with the numbers pulled and the line references into `decisions.md`.
You write the prose; this exists so you never start a section from a blank page.

Brief's hard constraints: **max 10 pages**, every section carries *decision → rationale
→ consequence*, "if a paragraph could have been written without building the system,
cut it", and it must not read as AI-written.

**Refreshed 2026-09-05 against `deployment-dev` @ `fefa7a7`.** The previous version of
this pack was written on 09-04 at 250 articles and D49. Since then the corpus has
roughly tripled, two more corpora landed, and §6 stopped being a gap. What changed is
summarised below; every section has been re-pulled.

Suggested page budget, summing to 10:

| § | Section | Pages | Material state |
|---|---|---|---|
| 1 | Architecture and data flow | 1.5 | Strong — needs one diagram; three corpora now, not one |
| 2 | Key decisions, trade-offs, rejected alternatives | 2.5 | Very strong — 72 entries, the problem is cutting |
| 3 | Model choices, fallbacks, agent vs deterministic | 1.5 | Very strong — now four model roles, not two |
| 4 | Evaluation | 1.5 | Strong, and much more honest than it was |
| 5 | Cost | 1.0 | Complete — `cost.md` is already the section |
| 6 | How you worked (agentic setup) | 1.0 | **No longer a gap** — `agentic-log.md` exists, needs your `[NEIL]` answers |
| 7 | What works, what's next, 3–5 insights | 1.0 | Strong — two new candidates worth displacing an old one |

---

## What changed since the 09-04 version of this pack

Read this first if you were working from the old numbers. Every headline figure moved.

| | 09-04 | now |
|---|---:|---:|
| articles / classifications | 250 | **694** |
| connections | 823 | **1,047** |
| decision entries | 49 | **72** |
| tests | 947 | **1,419** |
| prompt files | 12 | **17** |
| project spend | $16.19 | **~$18.20** |

Structurally new since then, in the order it landed:

- **Releases as a fourth leg** (D52) — 380 GitHub release notes in the same table.
- **The corpus overwrite and the v9 re-score** (D55, D56, D56a) — OpenAI was being
  scored on its own meta descriptions for two days; recovering the text and re-asking
  the question moved the high band 6 → 20 and the verbatim audit from 237-of-470
  unresolvable to **0 of 523**.
- **Papers as a second scored corpus** (D57, D58) — 47 papers under prompt `p1`,
  sharing every table and every rule with announcements. One prompt file differs.
- **Near-duplicate collapse** (D59, D59a–d) — 647 rows become 547 groups.
- **Release↔announcement pairing as a *link*, not a merge** (D61) — 25 links.
- **`docs/agentic-log.md` now exists**, in four dated sections. §6 was the gap; it
  isn't any more.

Not landed, in flight in this working tree as of writing: an **X/posts leg**
(`research/posts/`, `config/posts_sources.yaml`, D63). Planning §7's open question
about whether X earns its cost is being answered right now — check its state before
you write §7 rather than repeating "spike never run".

---

## §1 Architecture and data flow — 1.5pp

The spine, as built. Note that the fan-in is now three corpora, not one:

```
config/*.yaml
    │
    ├── announcements (4 discovery methods + 2 channels) ─┐
    ├── papers (6 lab harvesters, abstracts) ─────────────┼→ raw_articles (694)
    ├── github releases (per-org, star-ranked) ───────────┤
    └── github people/repos ──────────────────────────────┘
                                                          ↓
              classify — sonnet-5, announcements at v9 / papers at p1 → raw_llm_responses
                                                          ↓
                    transform → articles + classifications + three tag tables
                                                          ↓
        group (embed → band → adjudicate) + link → 647 rows in 547 groups (article_groups)
                                                    + 25 article_links
                                                          ↓
             connect (deterministic, 4 routes) → connections (1,047) → ┬ investment view
                                                                       └ AI-team view
```

Live numbers to quote (`bitcap-db status`, 2026-09-05):

- **694 articles, 694 classifications**, 189 mechanism tags, 45 category tags,
  717 practice tags, **1,047 connections**, 26 holdings, 1,862 cost rows, 8 gold
  snapshots, 36 published digests, 228 alerts.
- Corpus split in one table: **380 GitHub releases · 267 announcements · 47 papers**.
- Classification versions: **v9 (647) · p1 (47)** — one spine, two vocabulary versions.
- Grouping: 647 grouped rows resolve to **547 distinct groups** (100 collapse); 25 links.
- Bands: 28 high · 10 medium · 57 low · 599 none. The mass at `none` is the filter
  working, not a shortfall — say so before they ask.
- Per-lab watermarks: openai 241, google-deepmind 135, anthropic 105, meta-ai 81,
  xai 41, mistral 37, deepseek 7.
- 1,419 tests, 17 versioned prompt files across 5 families.

Points worth making here rather than in §2, because they *are* the architecture:

- **Four table layers with different truth-guarantees** — raw (verbatim, upsert-only),
  reference (YAML is source of truth), clean (derived, droppable), ops (never dropped,
  irreproducible). `rebuild` is safe precisely because of that split. README has the
  table.

  *If you use the medallion vocabulary, use it precisely:* raw is bronze, clean is
  silver, and `connections` / `digests` / `alerts` / `gold_snapshots` are gold. The
  boundary here is **not** aggregation — it is immutability of what was served. Silver
  is a pure function of `config/scoring.yaml`, so every score moves when the config
  moves; what the product *published* and *alerted on* cannot be recomputed after the
  fact and therefore survives a rebuild. The one thing you'd add with more history is a
  time-series rollup, and there isn't enough history yet to trend.

- **One shared core, two renderers** — the brief's explicit requirement. The join is
  computed once in `app/connect.py`; the audience split is a client-side framing of the
  same rows, not a second pipeline. Say this outright; it's a graded point. **Papers are
  the proof**: they share `raw_articles`, the JSON schema, `vocabularies()`,
  `drop_unknown_tags`, `enforce_quotes`, `call_cost`, `scoring.yaml`, `app/scoring.py`,
  `classifications`, all three tag tables, `connect`, `digest`, `/api/items` and the
  dashboard. **One prompt file and one work-list filter differ.** That is one more
  corpus, not a second system (D57, which explicitly supersedes D25).

- **Four discovery methods, one interface** `(lab, cutoff) -> list[dict]`:
  `sitemap`, `rss`, `listing_pagination`, `wayback_cdx`, plus `model_index` and
  `discourse` as second channels (D46, D47).

- **Four connection routes**, each carrying why it fired: `mechanism`, `category`,
  `lab_exposure`, `named`.

- **Grouping and linking are two different operations, deliberately** (D61). Union-find
  is transitive, so feeding releases into it pulls the Astra launch, the safety
  overview *and two customer stories* into one group. `config/dedupe.yaml` states the
  asymmetry that decides it: **a missed merge leaves a visible duplicate row a reader
  can see; a false merge silently deletes a document.** Only one is recoverable by the
  reader. So pairing writes `article_links` and never touches `article_groups`.

- **Four surfaces, one core**: the dashboard (the corpus), `/digest` (the cut,
  per audience), `/register` (who is tracked, and the moves in it), `/ops` (can I
  trust what the other three say).

- **The display window is a read cut and nothing else** (D61). `display.corpus_window_days: 90`
  exists because 186 of 380 releases predate 90 days (oldest 2019-06-26) while 260 of
  267 announcements already sat inside it. Grouping, pairing and first-mention still
  read the whole archive — `first_mention` is pinned by a test that stops the window
  ever reaching it, because firstness is a claim about the entire corpus and windowing
  the input reports a name as **new when it is not**.

---

## §2 Key decisions — 2.5pp

You have 72 entries and room for about eight. Ranked; cut from the bottom. These are
the ones carrying a real trade-off *and* a rejected alternative — the two things the
brief asks for.

1. **The silence pattern — now six instances, and it is the spine of the section**
   (D36, D38, D45, D46, D53, D59a, plus D61's own admission). One failure shape: *the
   run reports what it did, never what it failed to do*. The sharpest examples:
   - D45 — drift returned zero comparisons on Render, recorded every number truthfully,
     and reported **succeeded**.
   - D53 — the disk cache every papers harvester was built around **had never existed
     on the deployment** (`.gitignore` line 156, `.dockerignore` line 7, Render cron
     with no disk). Four of six papers sources died to arXiv 429s in one firing.
   - D59a — the dedupe phase spent money on `--dry-run`, because it was the one LLM
     stage not gated on `spend`.
   - D61 — a release↔launch join that produced 20 correct-looking links and **silently
     missed its own flagship example**.

   If §2 needs one theme, make it this. It is the most senior-engineer thing in the log,
   and it now has enough instances to be a claim about the *system* rather than a
   sequence of bugs.

2. **D57 — papers become a scored corpus, and D25 is explicitly overturned.** The
   cleanest supersession in the log, and the strongest evidence that "one shared core"
   is real rather than asserted. D25 rejected paper scoring on the grounds that it
   would be "a second full pipeline … and it answers a question announcements already
   answer better". The second half turned out to be **wrong, measurably**: DeepSeek-V4's
   abstract states *"requires only 27% of single-token inference FLOPs and 10% of KV
   cache compared with DeepSeek-V3.2"*, and those figures exist in the technical report
   and nowhere else. Carries three sub-decisions worth their space:
   - **Abstract, not full text**, measured before deciding: five arXiv `/html/` pages
     average **182,974 characters ≈ 59,200 tokens, ~43× the mean article** (4,290).
     Full text $11.30, abstracts **$0.75 actual**. Cost is not what decided it —
     every number that made these papers worth scoring is in the abstract. The honest
     cost: a figure stated only in a results table is invisible to us, which is why
     `paper_abstract` is its own `text_source` rather than being passed off as
     `full_text`.
   - **Its own prompt version `p1`**, not a shared `v10`. Sharing would have forced
     re-asking an unchanged question of 647 unchanged rows at **~$16.70**.
   - **Content alerts bounded by publication age** (`content_max_age_days: 120`),
     measured not chosen — otherwise the first firing pages someone about GPT-4's
     technical report.

3. **D59 + D59b + D59c + D59d — the duplicate collapse, and the eval that graded
   itself.** The richest methodological story in the project, and worth the space
   because the *corrections* are the content:
   - Mechanism overlap read as the natural signal and is **wrong** — "TCS and Anthropic
     bring Claude to regulated industries" vs "DXC integrates Claude into systems"
     scores a perfect Jaccard 1.0 and they are two different partnerships. The
     vocabulary encodes what *kind* of event an item is, not *which* event.
   - **The blind labelling file was not blind.** Sorted by cosine descending with the
     number withheld — the same information one transform away. Re-labelled from a
     shuffled file, two labels flipped, **and they were the top two negatives**;
     `cosine_high` moved 0.87 → 0.84 and precision-1.0 recall went 0.20 → 0.43. The
     leak had been suppressing the auto-merge threshold. Both files are kept, because
     the difference between them *is* the evidence.
   - **A headline that was a training score.** D59c reported the v2 rubric at 0.90
     against Neil's 20 human marks. Seven of those 20 are worked examples *inside v2's
     own prompt*. Split out: in-sample 3/7 → 6/7, **out-of-sample 12/13 → 12/13**.
     Every point of the gain was in-sample, and v2 adds one false merge v1 did not
     make. The defensible claim is *"v2 corrects the errors it was shown, and its
     behaviour on unseen pairs is unmeasured."*
   - Cost: **$0.000682 to embed 647 articles**, $0.057 of adjudication on a production
     pass. Calibration was billed three times; that is a build cost, not a running one.

4. **D24 — a budget ceiling replaces the scoring off-switch** (`decisions.md:2851`).
   Still the best single entry, because the consequence was measured, not asserted: the
   guard stopped the run *after spending $0.53 against an $0.08 ceiling* — 6.6× over,
   because 12 concurrent workers each tested a total none of its peers had contributed
   to yet. Fixed by counting in-flight calls; the re-probe overshot 5.6%. Pair it with
   the insight that the off-switch it replaced had silently hidden four labs from the
   product, **and** with the fact that the same bug was later reintroduced in `drift`
   next to its own fix, at $13.00 against a $2.00 ceiling (D43).

5. **D25 + D49 — the people register is a set of refusals, and the refusal pays.**
   Deliberately does *not* merge same-name people across labs, and recorded that as a
   cost. It turns out to be the mechanism: a move is one name with two records, so
   finding one is `GROUP BY canonical_name HAVING count(distinct lab) > 1` — no model
   involved. Then the evidence gate does the taste: **50 cross-lab names exist, 4 are
   shown**, 46 dropped as GitHub logins with no lab-owned email. Asymmetric on purpose.

6. **D48 + D50 — a digest is a dated cut, and the cut is the product.** Suppression
   count rendered as prominently as the items; 84 considered → 8 surfaced over 30 days.
   Three sub-points: the window is half-open because closed bounds put fourteen articles
   in two editions each; **periods are a fixed grid anchored at the epoch**, not an
   offset from whenever a run started, which is what makes the idempotence key able to
   collide at all; and the preview is allowed to roll *because it writes nothing*, while
   `publish` must quantise.

7. **D53 — the cache that never existed where it mattered**, and the trap it nearly
   walked into. Moving the cache to Postgres was the easy half. The interesting half:
   a permanent URL-keyed cache would have **frozen discovery** — `meta_harvest` caches
   its listing pages and `deepseek_harvest` caches the `au:"DeepSeek-AI"` query, the two
   URLs whose entire purpose is to return something different the day a new paper
   appears. Two cache classes: a versioned arXiv id is immutable (`expires_at = NULL`),
   everything else carries a TTL. Steady state ~70 live arXiv requests a night → ~8.

8. **D55 + D56 — enrichment that runs after the fetch does not survive the next one.**
   The corpus quietly stopped being the corpus: a backfill recovered 141 OpenAI articles
   from the Internet Archive, and the next fetch one day later rebuilt the file from
   scratch and put every one back to a blurb. Nothing failed. 1,194 tests stayed green.
   OpenAI is 59% of the corpus. Recovery moved *inside* the fetch, and v9 re-asked the
   question: high band 6 → 20, connections 481 → 882, **verbatim audit 0 of 523
   unresolvable against 237 of 470 for v7**.

9. **D26/D27 — two alert kinds, and the dedupe key is the actual design**
   (`decisions.md:3035`, `3103`), with **D49's amendment** as the sharper story:
   acknowledgement is withdrawn by the pipeline, not trusted to the operator, because
   the key holds still *during* an outage — so one click greened the badge for an entire
   incident. And the test that "proved" otherwise hand-wrote two keys that only ever
   occur when an outage ended and restarted.

10. **D58 — shipping without a check, by decision rather than omission.** The paper gold
    set carries **4 mechanism tags**; D35 already rejected a sample carrying 10 on
    exactly this ground. Four tags moves micro-F1 by ~0.15 each. So no nightly paper
    drift check — the alert would fire on jitter, and a false alarm a week is how a
    system-alert channel gets muted, which costs more than the check is worth because
    the announcements check shares that channel. **Extending the gold set does not
    rescue it**: the whole 47-paper corpus carries 27 mechanism tags across 33 papers
    with none. Papers are mechanism-sparse; that is a fact about research papers, not a
    sampling defect.

Available but I'd cut for space: D11–D21, D34, D40–D42, D23, D22.

**One negative result worth 3 sentences somewhere:** publication-derived importance does
not generalise across labs — it's Anthropic-specific (`decisions.md:693`, planning §11b).
Negative results read as honesty and cost almost no space.

---

## §3 Model choices, fallbacks, agent vs deterministic — 1.5pp

The table is now four roles, and the fourth is not a Claude model:

| Task | Model | Why |
|---|---|---|
| Announcement classification | `claude-sonnet-5`, 3-vote majority, prompt `v9` | Only config that reproduced |
| Paper classification | `claude-sonnet-5`, prompt `p1` | Same model, new `text_source`, so a new version |
| Gold adjudication (announcements) | `claude-opus-5` | Different model from the classifier, by design |
| Gold labelling (papers, dedupe) | `claude-fable-5`, blind | Third model; blind labelling cannot anchor |
| Duplicate adjudication | `claude-sonnet-5`, prompt `duplicate_adjudication/v2` | Only inside the cosine band |
| Corpus embedding | `text-embedding-3-small` (OpenAI) | $0.000682 for 647 articles |
| Byline extraction (fallback only) | `claude-sonnet-5` | Deterministic parsing is primary |

**The variance finding is the centrepiece, and it now has a second measurement.** Two
full runs over 375 announcements flipped `is_signal` on 9% of items; a probe found only
6 of 12 borderline items scoring identically, with one article ranging **17.8 to 80.0
across six runs**. There is no temperature to turn down — deprecated on the Claude 5
family, verified against the API. So the fix had to be architectural: 3-vote consensus.

The second measurement is stronger and it came from trying to justify a shortcut (D56).
On the **78 corpus articles whose text is byte-identical between prompt versions and
which are scored under both**, v9 disagreed with v8 on the mechanism id set for
**17 (22%)** and on the band for **8 (10%)**. So *"same prompt plus same text implies
same output" is false at 22%.* That killed the original justification for carrying
release classifications forward and replaced it with a narrower one that survives:
**re-running buys a different sample from the same noisy distribution, not a better
one.** ~1 in 5 of the 380 carried rows is not what a v9 call would have returned, and
that cost is recorded in the decision rather than in a footnote. Every carried row
stamps `_carried_forward` with its source version.

Rejected models, each with the measurement:

- **Haiku 4.5** — 4.3× cheaper and *more* consistent (9/12 identical), but scored both
  S-1 filings at 0.0, stably. *"Its apparent stability is partly confident wrongness."*
  That sentence is the whole argument; keep it.
- **GPT-5-mini** — 4.7× cheaper, but the vote didn't converge (4/6 reproducible, 7 tags
  dropped vs 1). Disagrees *systematically*, not just noisily.
- **Removing `is_signal` (v3→v4)** — adopted, but **the hypothesis was wrong**. Worth
  keeping for exactly that reason.

**A prompt-hygiene failure worth three sentences** (D56a), because it is the kind of
thing nobody thinks to check. `build_prompt` sent `PROMPT.read_text()` verbatim, and
markdown comments are not stripped — so v9 carried 686 characters v8 did not, including
a sentence telling the model that OpenAI's articles *"are now archived full text"*. A
leading claim about the input, pointing in the direction the scores moved. The guard
that was supposed to verify the prompts matched split them on different delimiters and
removed precisely the delta, so **it checked its one precondition vacuously.** The
consequence for the headline: the v8→v9 comparison is **two-variable, not one**. What
survives untouched is the verbatim audit, because that is mechanical.

**Agent vs deterministic — the brief asks for this explicitly.** You have measured
cases, which is rare:

- **Byline extraction, measured not argued** (`decisions.md:335`). Deterministic parsing
  stays primary. The eval found *more bugs in the deterministic parser than in the LLM*
  (three, all in the parser) — and deterministic still won, on reproducibility and
  visible failure, not on accuracy. F1s where both were run: 0.978 / 0.996 / 0.879.
  17 pages deterministic: **$0.00, under a second**. LLM: $0.86.
- **Scoring itself is a rule, not a model** (`decisions.md:868`) — classify with the LLM,
  score with `config/scoring.yaml`. The LLM does the reading; the arithmetic is auditable.
- **First mention is a corpus pass, not a prompt** (D52). Firstness is a property of the
  corpus, not of any document, so no per-document prompt can recover it however it is
  worded. One query over `raw_articles`, no LLM call, and it gets *stricter* as sources
  are added rather than noisier.
- **Deduplication is deterministic first and a model only inside a band** (D59). Three
  gates: subject (identifier match, free), event type (must agree, free), redundancy
  (cosine, then an LLM only in `[0.70, 0.80)`). Only the last costs money, and it runs
  on 19 pairs out of 4,412 candidates.
- **Release↔launch pairing costs nothing at all** (D61) — regex and set intersection
  over rows already in the database. No embedding, no adjudication, no API call, so
  `docs/cost.md` gets no entry for it.
- Where no deterministic path exists and you say so: DeepMind's arXiv pages
  (`decisions.md:1885`), confirmed live.

**Prompts in repo:** 17 files, versioned — `announcement_scoring/v1–v9`,
`paper_scoring/p1`, `duplicate_adjudication/{v1,v2}`,
`byline_extraction/{v1,v2,deepseek_v1,openai_v1}`, `summarisation/v1`. The brief asks
the doc to explain the rationale behind how they were written, not to reproduce them:
v4 removed `is_signal`; v2 vocabulary came from *measured confusions*
(`decisions.md:1575`); v8 fixed a calibration vacuum the model filled with the wrong
question (it read practice `confidence` as *"is the claimed benefit proven?"*, and a
spec sheet never publishes benchmarks, so the tag could not win); `p1` reworded
`research_result` because a technical report *is* how a model was launched; and
`duplicate_adjudication/v2` names "companion pieces" as a category. One prompt was
edited in place and had to be restored verbatim, because the label file it produced was
no longer described by the file named `v1` — all three label sets now carry a
`prompt_version` stamp instead of being identified by filename (D59d).

---

## §4 Evaluation — 1.5pp

The brief wants: extraction quality, hallucination control, scoring validation, and your
ground-truth approach. You have a defensible answer to all four *and* a candid account
of what they can't do — which is worth more than a better-sounding number.

**Start with the correction, because it is the most honest thing in the section.**
Neither gold set is human ground truth, and an earlier version of this project's own
notes said otherwise. `gold_human/` was deleted on 2026-09-01 because labelling twenty
~14,000-character articles by hand was not going to happen. What exists:

| set | labeller | mode | n |
|---|---|---|---|
| announcements | `claude-opus-5` adjudicating `claude-sonnet-5` | sees the classifier's output + a second run | 20 |
| papers | `claude-fable-5` | **blind** — sees only `text`, `title`, `url`, `text_source` | 10 |
| dedupe pairs | `claude-fable-5`, then a human spot-check | blind, shuffled | 82 (+20 human) |

Both model sets are the defensible proxies the brief permits; **neither may be described
as human-labelled anywhere in the design document**. `load_gold` raises on a file
missing `gold.labelled_by`, so a set whose provenance nobody recorded cannot reach a
metric. Blind labelling means more (it cannot anchor) and buys less (no second run to
show where the scorer was unstable, no recorded reason for rejecting a tag).

**What the announcements adjudication found — the argument for doing it at all.** 7 of
20 articles confirmed entirely, 13 with departures, 21 tag-level verdicts. Both largest
corrections were **under-scoring, on the articles a fund would most want to get right**:
a US directive forcing a lab to disable two frontier models went 13.3 → 80.0; OpenAI
reaching GA on AWS, with Amazon at 11.03% of the fund, went 0.0 → 26.7. *Accuracy is not
uniform across the score range, and the errors concentrate at the top of it.* That
sentence is the finding.

Two limits printed by `gold_metrics.py` on every report so they can't be quoted without
them: the adjudicator wrote the vocabulary it judges against (4 `vocabulary_gaps` were
found this way), and the adjudication was built from a run, so that run scores
optimistically.

**The paper baseline (D58)**, measured once, deliberately not recurring:

```
EVENT TYPE     9/10 = 90%       Cohen's kappa +0.787
axis          ref  run  hit  microF1  macroF1  identical
mechanisms      4    3    3     0.86     0.97      9/10
practices       9   13    8     0.73     0.77      5/10
investment    MAE  3.3    zero-vs-nonzero 10/10    both zero 7   identical 9/10
AI-team       MAE 12.8    zero-vs-nonzero  9/10                  identical 3/10
CITATION GATE  16 tags survived, 1 dropped for an unverifiable quote
```

Two things to say about it. **Mechanism precision is 1.00** — on the axis that routes to
holdings, and where a hallucinated tag would be most expensive, there were no false
positives. And **the noise filter holds**: all seven papers whose correct investment
score is zero scored zero, which is the failure this leg was built to avoid — a study of
how people perceive AI consciousness finding transmission and burying the technical
reports. The headline metric is deliberately `investment.zero_agreement` and **not**
mechanism micro-F1, because seven of ten pairs are empty-vs-empty and contribute to
neither numerator nor denominator: that metric would grade the three papers we are least
worried about and be blind to the seven the corpus exists to pin.

**Every disagreement was pre-identified by the labeller as genuinely arguable** — the
one event-type split (`open_weights` vs `frontier_model_release`, both weight 5, so it
costs nothing in score) was flagged as a three-way tie before the comparison was run.
The scorer's errors on this sample fall inside the band where the rubric itself is
ambiguous. What that does **not** establish is corpus-level precision and recall: n=10,
stratified rather than proportional.

**The eval finding that should probably lead the section**, from `agentic-log.md`: the
gold set carries its own copy of each document, and **11 of the 20 were 2×–98× richer
than what the pipeline actually scored** — 13,283 characters against 249 on the same
article. Gold agreement was being measured on text production never sees, so the drift
check would have reported health indefinitely while the product degraded. *An eval that
reads different bytes than the product is not an eval*, and nothing in the design said
so out loud.

**The human spot-check, and why the direction mattered more than the rate** (D59b).
Neil marked a blind stratified sample of 20 dedupe pairs: **15/20 = 0.75**. The finding
is not the rate — it is that **all five disagreements ran one way**. The labeller was
systematically too conservative on *companion pieces*: an umbrella announcement and its
named strand, an introduction and its deep-dive, one rollout staggered across surfaces.
The v1 prompt carried no worked example of that shape; every example in it was about
telling things apart, so that is what it kept doing. Ten minutes of human marking
changed a prompt, a threshold, and a recommendation about a $7.50 spending decision
elsewhere. Reported as a rate alone — *"0.75, acceptable"* — none of that surfaces.
**This is the brief's "examine the disagreements rather than averaging them away",
demonstrated on a set of five.**

It also independently confirmed two things the system had only argued for: the human
marked the Astra launch against the safety overview as `different`, which is exactly what
gate 2 exists to protect; and marked both leaked-label pairs the way the *corrected* pass
did, which is the evidence `cosine_high` belongs at 0.84.

**Drift is the production half** (D35, D45): the announcements gold set is re-scored
every firing, mechanism micro-F1 plotted on the ops tab, floor breach raises a system
alert — and `drift_unavailable` fires CRITICAL when the check runs and measures
*nothing*, which is what D45 caught. Standing cost ~$5.85/month, and it buys no product
output at all; justify it as the cost of catching silent degradation. **Papers have no
equivalent, knowingly** (D58) — the trigger is a code change, not a calendar.

**Hallucination control is mechanical and it now has a number.** The verbatim quote gate
(`decisions.md:1241`) runs before any judgement, and the adjudicator is held to it too —
the build fails if the adjudicator's own quote isn't in the document. The number that
proves it works is the v7→v9 audit: **237 of 470 quotes unresolvable → 0 of 523.**

---

## §5 Cost — 1.0p

`docs/cost.md` is already this section; it needs compressing, not writing.

- **Running total ~$18.20** against the €100 budget. Lead with that — spending under a
  fifth of the allowance and instrumenting every call is itself the finding.
- **$0.0276 per article classified**, calibrated against a $0.029 seed in
  `budget.DEFAULT_CALL_USD`. Drift runs dearer at $0.0325/item because it bypasses the
  cache and pays a cache *write*.
- **How cost shaped choices** (the brief asks this explicitly, and it's the part people
  forget). You now have six clean examples:
  - 3-vote consensus is a **3× cost decision taken deliberately** for reproducibility.
  - Haiku was rejected *despite* being 4.3× cheaper.
  - Byline extraction stays deterministic at $0.00 vs $0.86 for 17 pages.
  - Papers score from abstracts: **$0.75 actual against $11.30 for full text**, and the
    $10.55 saved buys ablations, appendices and bibliographies.
  - Papers carry their own `p1` rather than forcing a `v10`: **~$16.70 not spent**
    re-asking an unchanged question of unchanged text.
  - Release classifications carried forward rather than re-run: **~$10 not spent**, with
    the honest cost recorded (≈1 row in 5 differs, and they are the majority of v9 rows).
- **Two guards that fired, with the money attached.** D24: $0.53 against an $0.08
  ceiling, 6.6× over, fan-out across 12 workers — *the price of finding a real bug, and
  it was worth it.* D43: the same bug reintroduced in `drift`, $13.00 against $2.00.
- **Calibration is a build cost, not a running one** (D59). Duplicate adjudication was
  billed three times — original thresholds, after the ordering leak, after the human
  spot-check — for $0.1834 total. A production pass is 19 pairs, about six cents; an
  incremental run is near zero. And 647 articles embed for **under a tenth of a cent**,
  because what is embedded is title + classifier summary, not the body (37k tokens
  against ~700k, and the bodies are mostly site chrome that inflates similarity between
  any two pages from one lab).
- **A feature that cost nothing** (D61): release↔launch pairing is regex and set
  intersection over rows already in the database.
- Honest caveats already recorded: GPT-5-mini pricing is marked UNVERIFIED in
  `providers.py` (token counts exact, dollar conversion unchecked); and the release
  carry-forward wrote outside any tracked run, so *"~$10 not spent"* holds locally and
  nowhere else — a deploy to a fresh database, or any `bitcap-db rebuild`, spends it.

---

## §6 How you worked — 1.0p — **no longer the gap**

`docs/agentic-log.md` now exists, in four dated sections plus two `[NEIL]` blocks. It
was still specified on day one and not kept, and the first honest entry in it says so —
that admission is worth keeping in the doc.

**The setup**: `.claude/CLAUDE.md` as a working contract (non-negotiables, design
principles, expected failure modes, and an explicit parked backlog agents are forbidden
to touch); `docs/planning.md` as the reasoning behind it; a custom `bitcap-reviewer`
subagent running Opus, **read-only by construction** (`Read, Grep, Glob, Bash,
ReportFindings` — never Edit or Write), briefed to review the branch as a PR and to
check the repo's own contract rather than general engineering standards; git worktrees
for parallel sessions. **103 commits, 2026-08-24 → 2026-09-05**, heavily back-loaded:
53 on the last day alone against 1–3/day for the first week. The first week was research
and manual reading.

**What the agents verified themselves**: **1,419 tests**, and seven independent review
passes where one agent reviewed another's work with findings reproduced before being
accepted — `decisions.md:1693`, D21 (9 findings), D29 (1 CRITICAL, 11 MAJOR), D43 (5),
D51 (10), D53b (12), D54 (10), D55a (8), D56a (10), D59a (14), D59d, D61. The three
worth citing are the ones a human review misses:
1. `ensure_schema` failing on exactly the case its docstring claimed to handle — twice,
   in two different ways (D29, then D48).
2. Cost instrumentation with three holes, in a graded section (D29).
3. A test that walked the app's own route table and skipped the entire `/api/pipeline/*`
   subtree, because an `include_router` entry has `path = None` (D43).

**Where the loop broke** — you now have far more than you can use, so pick for range:

- **The agent's own tooling killed the agent's own runs.** Runs 16 and 17 both failed to
  `uvicorn --reload` restarting while Claude edited files, and a killed run leaves a
  `running` row, so one corpse blocked every future run until D43 added reaping.
- **Two correct scripts, one shared output key, silent loss — twice.** The OpenAI
  corpus overwrite (141 articles back to blurbs, nothing raised, 1,194 tests green), and
  then Mistral's paper citations *being* its announcement URLs, so landing the papers
  corpus overwrote two 13,000-character announcements with 2,000-character lead sections.
  **The second was caught only because the first taught us to read the insert/update
  counts** — `47 inserted, 2 updated` where 49 inserted was expected.
- **A check that was performed and not read** (D61). The agent had already run the query
  that would have exposed the broken join, and did not notice that the launch post it
  kept quoting was absent from its own output. 20 links, all correct, **a biased sample
  presented as a result**.
- **A test written from intent and never watched fail — three times, across three legs**
  (D54, D59d, D61). Each time the assertion was true with *or without* the thing it was
  written to defend. This is the single most transferable lesson in the log.
- **Confabulated parallelism** (D22): Anthropic-shaped prose generalised to every lab.
  The failure mode is not inventing facts, it is inventing *structure*.
- **A safety switch that became a coverage gap** (D10): agents honoured a scoring
  off-switch so faithfully that four labs sat unclassified for weeks. *An agent will
  honour a constraint long past the point a human would have questioned it.*
- **Verification at n=1 catches "does this work at all", not "does this work for the
  cases I did not sample"** — and the second is where this project's failures have
  actually lived.
- **A wrong answer stated confidently, twice, before the third held** — the v8-halved-
  our-alerts investigation inverted its own finding twice on the way to the truth.

Only you can supply: which parts you'd do by hand next time, where you stopped an agent,
whether the worktree parallelism paid, and whether a standing "a new test does not count
until it has been demonstrated red" rule is worth adopting. Those questions are already
written out at the two `[NEIL]` blocks in `agentic-log.md`.

---

## §7 What works, what's next, 3–5 insights — 1.0p

`docs/insights.md` has ten. The brief wants 3–5 and calls them "your proof that it
works", so pick for *range* — one product, one domain, one engineering. Two new
candidates are strong enough to displace older ones.

Strongest four, in my order — take three:

1. **The efficiency claim that only exists in the paper.** DeepSeek-V4's abstract,
   scored 100.0 and quoted verbatim: *"In the one-million-token context setting,
   DeepSeek-V4-Pro requires only 27% of single-token inference FLOPs and 10% of KV cache
   compared with DeepSeek-V3.2."* KV cache is HBM-resident, so a tenfold reduction per
   served token is a first-order claim about memory demand — **the DeepSeek→NVIDIA
   transmission the brief names as its calibration case, stated in numbers rather than
   inferred**. Routes to NVIDIA, Micron, TSMC, Amazon, TeraWulf and IREN at strength
   1.00; DeepSeek-V3's report routes *negative* to the same names. The announcements leg
   structurally cannot reach it. And the counterweight matters as much: **33 of 47 papers
   score zero for investors**, including both *"A moral Turing test"* and *"Artificial
   Minds, Human Disagreement"*. If those scored, they would bury the eight that matter.
   Also worth the line: **DeepSeek-R1 scores 100.0/100.0**, the brief's own calibration
   case recovered from an abstract, unprompted.

2. **Four model names appeared in the labs' own public code before any announcement,
   across three labs.** 39 identifiers first-seen in 120 days over 630 bronze documents;
   35 were first seen in the lab's own announcement, which is an ordinary launch. Four
   were not: `gpt-6-astra` (*"without … showing it in the model picker"*, `openai/codex`),
   `gpt-5.6-luna` (became the Agents SDK's default model), `gemma-3`, and **`gemma-4` —
   *"Add Gemma 4."*, `google-deepmind/gemma` v4.0.0, 2026-05-13, nearly four months
   before anything in the announcements corpus names it.** The distinction between the
   four and the other thirty-five is not something the system was told to look for; it
   falls out of recording which corpus reached a name first, which is only possible
   because every leg lands in one table.

3. **The register found four possible researcher moves without being asked to** — and
   found them *because* it refuses to merge people across labs. Ties directly to the
   brief's "a key researcher quietly moving to a competitor", and is now reproducible
   from the database rather than quoted (`/register`, D49). The number to quote is the
   filter's, not the finder's: **50 cross-lab names, 4 shown, 46 dropped**.

4. **BIT's biggest positions are pinned by regulation, not conviction** — all six funds
   sit just inside the UCITS 5/10/40 limits, largest position anywhere 10.43%. If so,
   the product's job isn't to rank conviction but to say which fund has headroom and what
   a purchase displaces. Most likely to start a conversation on the on-site.

Runners-up: one xAI announcement fanning out to seven holdings on a single sentence
(*"trained across tens of thousands of NVIDIA GB300 GPUs"*) — a finding that cuts both
ways, since it is the mapping layer working *and* the clearest noise risk, and it is the
argument for grouping by event; BIT's own audited report containing an entity-resolution
failure (ISIN `AU0000185993` listed twice either side of the Iris Energy → IREN rename);
and the same analysis reversing sign depending on window.

**What's next** — be specific and short. Candidates you can defend because you decided
against them *on evidence*, not by omission:

- **The repo-relevance filter** (agreed as the next branch). 39 of 87 watched repos are
  off-topic for an LLM tracker — mujoco, alphafold3, habitat, detectron2 — and they score
  *as high as* the on-topic ones (`alphafold3` averages 35.6 on the AI axis, third of all
  87). Relevance is orthogonal to the practice score, so no threshold fixes it. The shape
  is already worked out: judge the **repo**, not the release (87 calls once, not 380
  forever), and label the whole population rather than sample it.
- **A detector for the pairing recall bound** (D61), which currently has none — and the
  honest prerequisite is a labelled set, which is the cost of the detector rather than a
  detail of it. The realistic way it goes silently to zero is classifier drift, not a
  config edit.
- **A fresh 20-pair blind check on dedupe pairs the v2 prompt has never seen** (D59d) —
  the one thing that would turn "corrects the errors it was shown" into a claim about
  generalisation.
- **Papers into grouping**, which D60 costed and declined: 230 same-lab paper pairs,
  **zero duplicates**, and the event-type gate rejects only 1 pair in 41 because 34 of 47
  papers classify `research_result`. The whole burden would fall on a threshold
  calibrated on a corpus where that filtering had already happened, and the 40 survivors
  are Anthropic's alignment blog publishing five different papers in one fortnight.
- **X/posts** — in flight; check where it landed before writing this.
- The five items parked in planning §13.

---

## Claims you must not make in the doc

Every one of these was believed at some point in this project and is false. They are
also the questions most likely to be asked.

- ❌ *"The gold sets are human-labelled."* Neither is. Announcements are cross-model
  adjudication; papers and dedupe pairs are Fable 5, blind. Both are proxies and must be
  named as such wherever their numbers appear.
- ❌ *"v9 isolates the effect of recovering the text."* It does not — v9 also carried a
  686-character comment telling the model the articles were now archived full text. The
  comparison is two-variable. The verbatim audit (0 of 523) is the part that holds.
- ❌ *"The v2 dedupe rubric scores 0.90."* That is a training score; 7 of the 20 marked
  pairs are worked examples inside the prompt. Out of sample it is 12/13, identical to
  v1, plus one false merge.
- ❌ *"Same prompt and same text gives the same output."* False at 22% on mechanism ids
  over 78 articles.
- ❌ *"Deduplication merges papers and announcements."* It does not, by decision (D60).
- ❌ *"Releases and launch posts are merged."* They are **linked**, never merged (D61).
- ❌ *"Papers have a drift check."* They do not, by decision (D58).
- ❌ *"20 release links proves the join works."* It was a biased sample; the flagship
  example was missing from it.

---

## Sequencing note

Sections 5 and 3 are nearly transcription — start there to build momentum. Section 6 is
no longer a reconstruction job but still needs your `[NEIL]` answers and can't be rushed.
Section 2 is where you'll overrun: **decide your eight entries before writing, not
during**, and note that the silence pattern now has enough instances to carry the whole
section on its own if you want it to.
