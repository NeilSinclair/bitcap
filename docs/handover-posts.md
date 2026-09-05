# Handover — the X posts leg

## The one-paragraph version

`config/people.yaml` lists 36 senior people at seven labs and, until this leg,
nothing in the pipeline read it. The posts leg turns 28 of those handles (27
after excluding @elonmusk on volume) into a fourth scored corpus: 90 days of
original posts, pulled from the official X API, deterministically prefiltered,
scored under prompt version `t1`, and shown in the dashboard behind the existing
"Source" filter. It is deliberately **not** in the digest and raises **no**
content alerts. Full reasoning in `docs/decisions.md` D62.

## State when this was written (2026-09-05, branch `feature/lab-leadership`)

- 1,481 tests pass, `config/validate.py` reports 0 errors.
- Evidence tiers in the corpus after D63: 139 `self_post`, 73 `api_profile`,
  26 `own_site`, **0 `search_index`**.
- Corpus: 473 posts pulled, **238 kept**, 235 prefiltered out. Bands under `t1`:
  1 high, 4 medium, 23 low, **210 none**.
- Spend: **$2.74** X + **$2.61** Anthropic = $5.35. Receipts in `docs/cost.md`.
- 19 of 27 handles returned anything. Eight are silent: `DanielaAmodei`,
  `ch402`, `samsamoa`, `janleike`, `nickevanjoseph`, `8enmann`, `zdaxie`,
  `Guodaya`.
- Committed artifacts (all four are inputs to a reproducible rebuild):
  `research/docs/x_handles.json`, `x_rate_probe.json`, `x_posts_raw.json`,
  `posts_corpus.json`, plus `x_posts_filtered.json` for what was dropped and
  why, and 238 cached scores under `research/docs/post_scores/t1/`.

## How to run it

```bash
uv run python research/posts/harvest_x.py verify   # $0.27, only when handles change
uv run python research/posts/harvest_x.py probe    # $0.10, sets the per-handle caps
uv run python research/posts/harvest_x.py pull     # the actual pull, ~$2.40
uv run python research/posts/harvest_x.py filter   # free, deterministic
```

Every stage takes `--dry-run`, which prints the projected cost and calls
nothing. **Use it.** The scheduled path (`fetch_posts`) runs `pull` + `filter`
only; `verify` and `probe` are manual because re-running them weekly would repay
a measurement that does not change.

## Traps

**a. `max_results` is not a page size, it is a bill.** X charges per post
returned. Nothing in `x_client.py` follows `next_token`, and that is the whole
cost guarantee — not an oversight. If you add pagination, add a ceiling first.

**b. "Fewer posts than we asked for" does not mean "that is all there is."**
X applies `exclude=[replies,retweets]` *after* assembling a page, so a prolific
replier returns few originals from a page of five. This produced a live wrong
number: @sama measured as 4 posts in 90 days against a true ~223. Read the age
of the **oldest** post, never the count. `projected_count` returns `None` for an
empty probe for the same reason — unknown, not zero.

**c. Billing is on returns, so generosity to quiet handles is free.** The
allocator gives an unknown handle a full 100-post page and budgets it at zero.
Reverting that to "reserve what you ask for" makes eight silent accounts consume
the entire balance and starves the handles that carry the volume.

**d. `classify_new(corpus=...)` was a ternary and is now a lookup.** It read
`== "papers"` and let every other name fall through to the announcement prompt —
silently, at full price, producing plausible output. It now raises on an unknown
name *before* touching the database. Do not turn it back into a default.

**e. `connect()` deletes the whole connections table.** It must be called once
spanning all three versions (`v9`, `p1`, `t1`), never once per version. Same
trap as the papers leg; `app/cli.py` and `app/pipeline/worker.py` both do it
correctly and both carry the comment.

**f. Posts are muted in two places, not one.** `DIGEST_VERSIONS` in
`app/cli.py` keeps them out of the digest; `alerts.content_mute_prompt_versions`
in `config/pipeline.yaml` keeps them out of content alerts. `high_band_items`
has no version filter of its own, so removing only the config line turns alerts
on. Both are pinned by tests in `tests/test_posts_spine.py`.

**g. The corpus does not own its attribution.** `x_evidence`, `author_role` and
`role_contested` are re-derived from `config/people.yaml` by the `filter` stage,
not trusted from what the pull stamped. Fixing the register therefore reaches
the dashboard by re-running `filter` (free) rather than repaying for a pull. If
you change how attribution is stored, keep that split: the pull records what X
returned, the register records what we know about the person.

**h. Cost is written to `research/docs/announcement_cost.json`**, shared with
every other corpus, by unlocked read-modify-write. This has already corrupted
that file once (see `docs/agentic-log.md`). Do not run a classification pass
while another agent is running one.

## Known-open

- **The announcements leg has a coverage hole this leg found.**
  `config/sources.yaml` watches `ai.meta.com/blog/` and `deepmind.google/blog/`;
  the register holds **0 rows** from `research.meta.ai` and `blog.google`, where
  Meta's Muse model line and at least one Gemini release live. Deliberately not
  fixed here — it is an announcements-leg change with its own cost, and burying
  it inside this one would hide it.
- **Threads are truncated to their opening post.** `exclude=replies` is
  server-side and a self-thread's posts 2..N are replies. Reconstructing them
  means paying for every conversational reply first.
- **`@samsamoa` did not verify.** The account resolves but is named "sam",
  unverified, with an empty bio — nothing corroborates Sam McCandlish. It stays
  on `x_evidence: search_index`, which `config/people.yaml` calls "a lead, not a
  fact". `@8enmann` likewise ("Make AI safe again" asserts no affiliation).
- **`@Guodaya` may have left DeepSeek.** His bio ends "Previously
  @deepseek_ai"; the register lists him active. Flagged `role_contested`, not
  moved to `departed` — an unverified profile is a lead, not the dated source a
  departure needs. He posted nothing in the window, so nothing rests on it yet.
  **Settling this needs a dated report** (D63).
- **`@janleike`'s role stays contested.** His bio reads "AI research
  @AnthropicAI", not the alignment-leadership title his own site states, which
  corroborates the CONTESTED note rather than resolving it. Not decided.
- The `duplicates_announcement` figure is weaker than it looks: 352 of 409 links
  are quote-links to other posts, so it measures link behaviour more than
  novelty. The narrower claim about the 17 lab-document links is the defensible
  one.

## Where the evidence lives

`docs/decisions.md` D62 · `docs/cost.md` "X posts leg" · `docs/planning.md` §7
(resolved) · `tests/test_posts_harvest.py` (budget, rate maths, prefilter) ·
`tests/test_posts_spine.py` (prompt dispatch, digest/alert muting, registry).
