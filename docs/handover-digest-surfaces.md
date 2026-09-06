# Handover: the digest window, the Alerts card, and a branch split that hid work

Written 2026-09-06 by an agent session working from
`/Users/neilsinclair/code/myrepo-deployment-dev`. Rationale is in
[`decisions.md`](decisions.md) D72–D75; this is the operational picture — what
is merged, what is open, and the two traps that cost the most time.

**Read §3 first if you are picking this up cold.** The single most expensive
mistake in this session was not a code bug: it was two long-lived branches
diverging, so working code looked missing.

---

## 1. The one-paragraph version

Four pieces of work on the two reader-facing surfaces. The digest window went
from 48h to 7 days and its ordering was split in two (D73). The digest card
gained the dashboard's full detail panel, shared rather than copied, and stopped
keying on an article id that goes stale on every rebuild (D72). `bitcap-db load`
started rebuilding the grouping tables it had been silently wiping (D74). And
the Alerts card got the hover affordance that says it opens, plus a banner for
the one failure that made it silently inert (D75). All are merged into
`deployment-dev`, D75 last, as PR #42 at `1fa43b2`.

## 2. State when this was written

| | |
|---|---|
| `deployment-dev` tip | `1fa43b2` |
| `deployment` tip | `7b44d1f` |
| merged this session | #39 (D72), #40 (D74), #41 (D72's badge), #42 (D75), plus `98c395a` (D73) direct to `deployment-dev` |
| open PRs | none |
| tests | 1,671 pass, 1 skipped; config validate 0 errors |
| JS smoke tests | 4, all passing — `smoke_digest_render.js` is new this session |

## 3. THE TRAP: `deployment` and `deployment-dev` have diverged

**14 commits are on `deployment-dev` and not `deployment`. 6 are on
`deployment` and not `deployment-dev`.** They are not a fast-forward apart in
either direction.

This is not academic. PR #38 was merged into **`deployment`** while every other
PR went to `deployment-dev`. The result was that a feature — the "3 documents ·
one event" fold badge — existed, was merged, and was invisible on the branch the
local dev server runs. It read as the feature having been deleted by whatever
merged most recently, and two rounds of diagnosis went looking for a bug that
did not exist.

It was resolved by re-merging the same branch as PR #41, this time into
`deployment-dev`. **The two branches are still divergent.** Before concluding
that anything is missing:

```bash
gh pr list --state merged --limit 10 --json number,title,baseRefName,mergedAt
git log --oneline origin/deployment..origin/deployment-dev   # 14
git log --oneline origin/deployment-dev..origin/deployment   # 6
```

**Do not check for merged work with `git merge-base --is-ancestor <sha>`.**
GitHub creates a new commit on merge, so the original SHA is never an ancestor
and the check reports "not merged" for work that is. Grep for the code, or ask
`gh`.

## 4. What each piece did

### D73 — the window (`98c395a`, on `deployment-dev`)

`window_hours: 48 → 168`. The 48-hour window was publishing **one item**, and no
post or paper had ever reached a published edition despite 39 being eligible.

**168 is not a measured optimum and `config/digest.yaml` says so at length.**
The first version of that comment claimed a replay showed 120h "cliffing" while
168h held; review established the cliff is each grid crossing 05 Sep, where the
corpus stops. Every width has the same cliff. Re-derive if ingestion resumes.

Ordering is **two sorts either side of the `max_items` cut** — selection on
`rank`, display on date-then-rank. They cannot be one sort: sorting by date
before the cut drops a higher-scoring launch from earlier in the window.

Nothing else reads the shipped `window_hours`; `test_the_shipped_window_is_pinned`
is a tripwire, and its docstring says it pins the value rather than endorsing it.

### D72 — the shared record (#39, #41)

`frontend/app/detail.js` is new and holds the detail panel, `decorateItems`,
`groupAnchors`, `relatedByGroup` and the palette. **Both pages import them.**
`page.js` lost 380 lines. Clicking an Alerts card opens the identical panel.

Cards resolve to a live row by **url first**, id only on the live preview.
`articles.id` is reassigned on every rebuild and a published payload is frozen:
measured 9/65 resolvable by id, 61/65 by url. The id fallback is confined to the
preview because an archived id is not merely stale but *recycled* — it may name
a different document.

### D74 — `bitcap-db load` restores grouping (#40)

`load_refs` deletes `ArticleGroup` and `ArticleLink` by name (it must; they hold
a plain FK to `articles` with no cascade). Nothing rebuilt them, so a load left
both at zero rows and every surface silently flat. `cmd_load` now calls `_group`
after the tracked block.

**Outside the block, and guarded, and both halves matter.** `dedupe.assign`
commits internally; inside the block it made the ref wipe durable mid-load, and
on a first-ever `bitcap-db rebuild` an injected grouping fault took the database
from 577 articles to **zero**. `adjudicate_pairs=False` keeps a rebuild keyless
and free — verified with both API keys unset.

### D75 — the Alerts card (#42)

The `card` class, so a clickable card looks clickable. Applied only when
`onOpen` is set. Plus a banner when `/api/items` fails, replacing a
`.catch(() => setCorpus([]))` that made every card silently inert.

## 5. Known-open

- **`deployment` is 6 commits from `deployment-dev` and 14 behind.** Someone has
  to decide the direction and reconcile them.
- **The two branches will keep doing this** until PRs consistently target one
  base. Nothing enforces it.
- **A worker that dies between `etl` and `dedupe`** leaves the grouping tables
  empty until 03:00 the next day. The run records `failed`, so it is visible,
  and the next firing self-heals. Closing it needs one transaction spanning both
  phases — deliberately not attempted.
- **A rebuilt database has less grouping than a worked one.** No embedding cache
  means `coverage: 0.0` and the cosine gate is a no-op; no committed releases
  means zero `article_links`. Both resolve on the first worker firing. Stated in
  the test class so the fix is not read as more complete than it is.

## 6. Traps for whoever is next

**Do not diagnose a UI fault from the server side alone.** Three rounds of
measurement said the system was healthy — 643 rows, every preview item
resolving, both smoke tests green — while the cards genuinely looked broken. The
fault was a missing CSS class. Every check had run on the side of the browser
where the fault was not. The question that resolved it was asking the person
looking at the screen what they actually saw.

**`next build` passing is not evidence a page works.** It bundles the component;
it never calls it. That is what `tests/smoke_dashboard_render.js` and
`tests/smoke_digest_render.js` are for, and both are required CI steps. The
first of them went red on the D72 branch and nothing else noticed.

**A test that greps source can read the prose instead of the code.** One written
this session asserted an old silent `catch` was absent and failed against the
comment quoting it while explaining its removal. `_code()` in
`TestAlertsSaysWhenItCannotOpenACard` strips comments before matching.

**The two pages carry different palettes on purpose.** `digest/page.js` folds
`low`/`none` into one band style; the dashboard distinguishes four. Sharing the
digest's version silently restyles every low row on the dashboard. `detail.js`
carries the dashboard's, and a test pins it.

**Verify an auto-merge, do not trust it.** #41 and #42 touch the same region of
`digest/page.js`. It merged with no conflict; the check that mattered was
counting that both `FoldedBadge` (3) and the card class (2) survived.

## 7. Where the evidence lives

`docs/decisions.md` D72–D75 · `config/digest.yaml` (the window argument, in
full) · `tests/test_digest.py` (`TestTheEditionIsChosenOnMeritAndReadByDate`,
`TestAFoldedCardSaysWhatItStandsFor`, `TestTheDigestOpensTheSameRecordAsTheDashboard`,
`TestAlertsSaysWhenItCannotOpenACard`) · `tests/test_pipeline_db.py`
(`TestARebuiltDatabaseHasItsGrouping`, `TestAGroupingFaultCostsTheGroupingAndNothingElse`)
· `tests/smoke_digest_render.js`
