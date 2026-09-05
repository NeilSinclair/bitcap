# Handover: papers become a scored corpus

Written 2026-09-05, on branch `feature/papers-scoring` (off `deployment-dev`,
PR [#17](https://github.com/NeilSinclair/bitcap/pull/17)). Rationale lives in
[`decisions.md`](decisions.md) §D57, which supersedes D25; the receipt is in
[`cost.md`](cost.md). This is the operational picture — what is done, what is
not, and what to be careful of.

---

## 1. The one-paragraph version

49 papers sat in `raw_papers` carrying **bylines only** — that leg was built for
the people register. None had ever reached `classifications`, `connections`, a
digest, `/api/items` or the dashboard. They now do: each paper's citation page
is fetched, its abstract extracted, and the result landed in `raw_articles` as
one more corpus, scored under its own prompt version `p1` so the 647 existing
announcement classifications were never touched. 47 of 49 scored for **$0.75**.
**The code is merged-ready and the corpus is committed**, so `bitcap-db rebuild`
reproduces the whole thing offline with no API key.

## 2. State when this was written

| | |
|---|---|
| branch | `feature/papers-scoring`, 3 commits, pushed, PR #17 open against `deployment-dev` |
| tests | 1,313 collected, all pass (53 new in `tests/test_papers_scoring.py`) |
| pre-existing failures | 13 alembic errors, **not from this branch** — see §4 |
| corpus | `research/docs/papers_corpus.json`, 47 records, mean 2,008 chars |
| scores | `research/docs/scored_papers_p1.json`, 0 failures |
| `PROMPT_VERSION` | `v9` (announcements) |
| `PAPER_PROMPT_VERSION` | `p1` (`prompts/paper_scoring/p1.md`) |
| cost | $0.7537 corpus run + $0.100 in n=5 probes |

Corpus shape, by lab and by how the text was extracted:

| lab | n | | strategy | n | `text_source` |
|---|---|---|---|---|---|
| google-deepmind | 15 | | `blockquote_abstract` | 15 | `paper_abstract` |
| anthropic | 12 | | `heading_section` | 15 | `paper_abstract` |
| deepseek | 8 | | `lead_section` | 17 | `paper_lead` |
| openai | 7 | | | | |
| meta-ai | 5 | | | | |

Mistral is **deliberately absent** and that is not a bug — see §4a. Zero papers
arrived on a downgraded strategy, which is checked, not assumed: every record
carries both `extraction` and `extraction_configured`.

## 3. What changed, by layer

**New**
- [`research/papers/paper_text.py`](../research/papers/paper_text.py) — three
  extraction strategies with a fallback chain, and `collect()`, which fetches
  each `raw_papers.url` (already the citation) through the existing
  `fetch_cache`. Papers that cannot be extracted go to `unresolved_items` with a
  reason; nothing is dropped silently.
- [`prompts/paper_scoring/p1.md`](../prompts/paper_scoring/p1.md) — generated
  from v9, 52 lines differ. Adds `paper_abstract`/`paper_lead` to "What you are
  reading", re-points the noise warning at research papers, and disambiguates
  `research_result` so a technical report or model card that introduces a model
  is `frontier_model_release`.
- [`research/papers/build_paper_gold_set.py`](../research/papers/build_paper_gold_set.py)
  — stratified sampler over five document types, `SEED = 20260905`. Ten labelled
  papers in [`research/papers/test/papers/`](../research/papers/test/papers/),
  provenance in that directory's `README.md`.
- [`tests/test_papers_scoring.py`](../tests/test_papers_scoring.py) — 53 tests.

**Changed**
- `app/cli.py` — `PAPER_PROMPT_VERSION` and `PROMPT_VERSIONS` beside the
  existing `PROMPT_VERSION`; `cmd_load` loads the papers corpus and calls
  `transform` once per version.
- `research/announcements/score_announcements.py` — a `Variant` NamedTuple and
  lazy `announcements()` / `papers()` resolvers, so the module globals stay
  monkeypatchable.
- `app/pipeline/classify.py` — `include_source_files`, and a `corpus` argument
  that routes to the paper variant.
- `app/connect.py`, `app/digest.py`, `api/queries.py` — `== prompt_version`
  widened to `.in_(versions)`.
- `api/queries.py` — `docType`; `frontend/app/page.js` — an All / Announcements
  / Papers filter beside the lab filter.
- `app/pipeline/worker.py` — papers landing in phase 2, papers classify in
  phase 3, under the same `Budget`.
- `app/pipeline/alerts.py` — `content_max_age_days` bounds both content rules,
  plus a new `extraction_downgraded` **system** rule.
- `config/papers_sources.yaml`, `config/pipeline.yaml`, `config/validate.py`.

## 4. Traps

**a. Some labs' papers *are* announcements.** Mistral has no publications page,
so its papers leg sources candidates from its own announcements corpus (D17) and
the citation is `mistral.ai/news/<slug>` — a URL the announcements leg already
holds at full text. `raw_articles` is keyed on URL, so the first run of this leg
**overwrote two 13k-character Mistral announcements with 2k lead sections**. It
was caught in the insert/update counts (47 inserted, 2 updated), repaired with
`load_articles`, and is now prevented by the `covered` guard in `collect()`. If
you add a lab whose papers and announcements share URLs, that guard is what
stops the same document being scored twice under two prompts.

**b. `connect()` deletes the whole table.** It must be called **once**, spanning
both versions. Calling it once per version leaves only the second version's
rows — silently, with the UI showing papers with an empty impact row. This is
why `cmd_connect` hard-codes the pair rather than taking one version.
`transform()` is the exception: its delete is already scoped by version, so it
is called once per version, and its `no_classification` count then includes the
other corpus. Note that, don't "fix" it.

**c. A missing publication date is a poison pill.** `transform` calls
`date.fromisoformat(p["date"])` inside `_etl`'s single transaction, so one
dateless row fails the run, stays in bronze, and fails every subsequent firing
until someone deletes it by hand. Two of six harvesters can legitimately return
no date, so `collect()` sends those to `unresolved` instead.

**d. The 13 alembic test errors are not from this branch.** Revisions `0010`
(creates `raw_article_embeddings`, not `article_embeddings`) and `0011` live on
`feature/disambiguation`, unmerged, while the shared Postgres already has both
tables. `git merge-tree` shows zero conflicts with this branch; merging it and
running `upgrade head` is a no-op that clears the errors.

**e. New event types are a rescaling hazard.** `max_event_weight` is pinned to
`max(event_weight.values())`, so a new type above weight 5 silently rescales
every existing announcement score downward, and `score_of` does
`.get(event_type, 0)`, so a type in the prompt but missing from
`config/scoring.yaml` scores zero silently. This is why the paper prompt reuses
the existing vocabulary rather than adding to it.

## 5. Known-open

- **The gold set is not wired into `drift.measure`.** `drift` grades
  announcements only, so paper scoring has 53 unit tests but **no ongoing
  agreement metric**. This is the one gap named in D57 and in the PR body, and
  it is the largest remaining risk on this leg, because silent degradation is
  the stated main risk of the whole pipeline.
- **Neither gold set is human ground truth.** The papers set was labelled by
  Fable 5, blind, in a subagent; the announcements set is cross-model
  adjudication (Sonnet 5 classifier, Opus 5 adjudicator) — `gold_human/` was
  deleted on 2026-09-01. Both are defensible proxies and both must be *named*
  as proxies wherever their numbers appear. Spot-checking the disagreements is
  the cheapest thing that would improve either.
- **A figure stated only in a results table or an ablation is invisible to us.**
  That is the accepted cost of scoring abstracts rather than full text (D57),
  and it is a limitation, not a defect.
- **`research_result` still ceilings at 60.0** — exactly the `high` band
  minimum — so a genuine finding reaches the digest only on a high-magnitude,
  high-confidence, quote-backed tag. That asymmetry is deliberate; check it
  still holds if the bands move.

## 6. Where the evidence lives

| | |
|---|---|
| why any of this | [`decisions.md`](decisions.md) §D57 (supersedes D25) |
| the money | [`cost.md`](cost.md), "papers become a scored corpus" |
| what it surfaced | [`insights.md`](insights.md) |
| gold-set provenance | [`research/papers/test/README.md`](../research/papers/test/README.md) |
| the committed corpus | `research/docs/papers_corpus.json`, `research/docs/scored_papers_p1.json` |
