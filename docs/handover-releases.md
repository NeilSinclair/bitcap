# Handover: GitHub releases — tagged, windowed, and linked to launches

Written 2026-09-05, merged as PR #22 (`feature/github-release-pairing`) into
`deployment-dev` at `fefa7a7`. Rationale lives in
[`decisions.md`](decisions.md) §D61; this is the operational picture — what is
done, what is not, and what to be careful of.

Read [`§D60`](decisions.md) alongside it. It was written the same day by another
agent and decides the adjacent question (papers are *not* deduped or linked),
including two hazards this work had to design around.

---

## 1. The one-paragraph version

The releases leg (D52) put 380 GitHub release notes in the corpus and D56 scored
them, but they reached the dashboard mislabelled as announcements, half of them
predated any sensible horizon, and a release was never connected to the launch
post it shipped support for. All three are fixed: `docType: "release"` with its
own filter, a 90-day read window, and a new `article_links` table that **relates**
a release to a launch without merging them. No LLM call, no cost. **Merged and
live in `deployment-dev`; the migration has been applied to the shared local
Postgres.**

## 2. State when this was written

| | |
|---|---|
| branch | `feature/github-release-pairing`, merged as PR #22, branch deleted |
| tests | 1,418 pass, 1 skipped (26 new); config validate 0 errors |
| alembic | `0012` (`article_links`), applied to local Postgres |
| corpus | 647 articles at `v9`, 380 of them releases |
| dashboard | 453 items rendered (90-day window), 260 announcements + 193 releases |
| links | 25, over 11 releases and 5 announcements |
| groups | unchanged by this work — `release_train 55, singleton 479, exact 2, llm 6, embedding 5` |
| cost | **$0.00.** Regex and set intersection only |

## 3. What is where

| what | where |
|---|---|
| doc-type map | `api/queries.py` `_DOC_TYPES`, `registry.RELEASES_CORPUS` |
| display window | `config/pipeline.yaml` `display.corpus_window_days`, applied in `queries.window_start` |
| release identifier extraction | `dedupe.release_subjects` (body, not title) |
| shared extraction helper | `dedupe._extract` |
| the join | `dedupe.release_links` |
| the gate that bounds it | `config/dedupe.yaml` `pairing.announcement_events` |
| storage | `models.ArticleLink`, `alembic/versions/0012_article_links.py` |
| render | `frontend/app/page.js` `RelatedItems` + `relatedForGroup` |
| name collision | `config/entities.yaml` `deny: [sonnet-2]` |

## 4. The five things to understand before changing any of it

**a. A link is not a merge, and must never become one.** `release_links` returns
edges and writes `article_links`; it never touches the union-find. Measured: with
stems and no event gate, one `openai/codex` release transitively pulls the Astra
launch, the safety overview and two customer stories into a single group. A false
merge deletes a claim from the product (see `config/dedupe.yaml`). The regression
test is
`tests/test_dedupe.py::TestAssignWritesTheGrouping::test_pairing_writes_links_and_moves_no_group`.

**b. The two sides of the join read different fields, deliberately.** A release
title is built by `fetch_releases.as_announcement` as `{org}/{repo} {tag}` — our
string — so `subjects()` returns the empty set for **every** release. Measured: 1
release title yields an identifier against 24 bodies. `first_mention.text_fields`
already encoded this asymmetry; follow it rather than "fixing" the inconsistency.

**c. `event_type` is what bounds the join, not the identifiers.** This is the
part most likely to be undone by someone tidying up. On identifiers alone the
launch post and a customer story are *identical* — "GPT-6 Astra: A new generation
of intelligence" and "Legora reviewed 41 documents in minutes with GPT-6 Astra"
both yield exactly `{gpt-6}`. No threshold over identifiers can separate them.
`announcement_events` can, and gate 2 already trusts that axis.

**d. The window is a READ cut only.** `build_items` applies it. The digest already
windows to 48h. Grouping and pairing run over the whole corpus. And
`research/corpus/first_mention.py` must never be windowed — firstness is a claim
about the entire archive, so truncating its input makes a name report as *new
when it is not*. `tests/test_first_mention.py::TestTheDisplayWindowNeverReachesHere`
asserts on `load_corpus`'s signature so adding a `since=` parameter fails CI.

**e. Links resolve through the anchor in the frontend, not the API.** 17 of 25
links point at a *folded* group member; rendered as stored they would be written,
counted, and never displayed. `relatedForGroup` keys by group and resolves through
`anchorFor`. It has to be client-side because the anchor is decided per audience
(D59d) — `is_anchor` is one corpus-wide flag and would name the wrong row on one
of the two tabs.

## 5. The known gap — read this before trusting the feature

**The recall bound is `announcement_events`, and it has no detector.**

`stats["links"]` and `stats["paired"]` are reported but **no alert rule reads
them**. They are diagnostics a person reads, *not* the equivalent of `coverage`
(which is a detector precisely because `alerts.dedupe_unavailable` reads it). An
earlier draft of D61 claimed that precedent and was wrong.

The realistic failure is not a config edit but classifier drift. The gate reads an
LLM-assigned label, and `product_launch`, `capability_result` and `research_result`
are all excluded and all plausible mislabels for a model announcement. **A prompt
bump that starts calling launches `product_launch` takes links to zero with a
green dashboard and nothing raised.**

An alert arm was considered and deliberately not built: zero links is the normal
state of most windows, and there is no labelled set from which to derive what a
healthy link count looks like. This repo does not choose thresholds by eye. The
honest prerequisite is a labelled set, which is the cost of the detector rather
than a detail of it.

If you pick this up, the promising angle is probably **not** a link-count alert.
It is that the failure is a *label-distribution* change, which is what the
existing drift machinery (`gold_snapshots`, `alerts.drift_agreement_floor`)
already exists to catch. Zero gated event types across a 647-article corpus is
unambiguous and needs no calibration.

## 6. Traps

- **`stem()` on the release side.** Do not add it back thinking it tightens the
  join — it is already applied in `release_links`, and the *reason* moved. An
  earlier version refused stems there and silently lost the launch post, which is
  the case the feature exists for. See D61.
- **`max(shared)` without `sorted`.** `max` over a set breaks ties in iteration
  order, which follows string hash randomisation, so a pair sharing two
  same-length identifiers stored different evidence on different processes.
  `test_the_evidence_is_stable_when_two_identifiers_tie_on_length` pins it; run
  the suite under a couple of `PYTHONHASHSEED` values if you touch this.
- **`sonnet-2` is denied for a subtle reason.** The release *titles*
  (`google-deepmind/sonnet v2.0.1`) extract nothing at all — `max_version_parts:
  2` rejects three-part versions. It is the *bodies* saying "Sonnet 2" that
  collide. A test asserting the title extracts nothing passes whether or not the
  denylist exists; that mistake was made and caught here.
- **The link write shares `assign`'s transaction.** It is not failure-isolated
  from grouping. Nothing in pairing is expected to raise (no network, no provider,
  no foreign parsing), which is why that was judged acceptable — but do not add
  anything fallible to it without revisiting.
- **`article_links` is silver, not ops.** It is rebuilt wholesale each run and is
  deliberately absent from `models.OPS_TABLES`, so `drop_all` clears it. Do not
  add it there.
- **Migration index names** match what `create_all` derives from `index=True`, so
  migrated Postgres and `create_all` sqlite agree. `_schema()` never inspects
  indexes, so nothing would catch a divergence.

## 7. What was NOT done

- ~~**The repo-relevance filter.**~~ **Done — D65.** The eyeball estimate of 39
  off-topic repos measured out at **35 of 87**. The shape held: the repo is
  judged, not the release, and the verdict is cached for ever. Two things this
  handover did not anticipate, both in D65: the gate had to go *inside* the star
  ranking rather than after the slice (so a dropped repo frees its slot), and a
  second gate was needed in `transform`, because that function re-derives silver
  from the whole of bronze on every firing and a forward-only filter would have
  left the 33 off-topic releases already on the dashboard exactly where they were.
- **The detector** in §5.
- **Re-running the digest.** Digest 96 predates all of this. The AI digest was
  6/8 releases before pairing and grouping; it has not been regenerated since.

## 8. `[NEIL]` — only you can answer these

- Is one release related to *one* launch the right presentation, or do you want
  the three Astra posts listed separately? The current behaviour collapses them
  via the group anchor.
- `safety_policy` is excluded from `announcement_events` on the reasoning that the
  safety overview reports a claim the launch post does not. Defensible, but it is
  a judgement about what "related" means to a reader, not a measurement.
