# Papers research — deriving a lab's people from what it publishes

**Pages (D22).** `build_lab_authors_page.py [lab...]` renders DeepMind, Meta and
Mistral from one builder — those three share a register schema (`is_lab_staff`,
`affiliations`), unlike the older Anthropic, OpenAI and DeepSeek registers,
which keep their own builders rather than being forced into a shared one. Each
page carries the paper table with its arXiv resolution method, the people, and a
ranked table of **co-authoring institutions**: the cheapest available shortlist
of sources not yet tracked. Cross-lab view:
[`research/corpus_survey.html`](../corpus_survey.html).

**All six registers cite a resolvable primary source for every paper, but not
under the same key** — checked, not assumed. DeepMind, Meta and Mistral carry
`source_url` (the arXiv HTML) alongside the lab's own page; OpenAI and DeepSeek
put the arXiv abstract URL in `url` and also keep `arxiv_id`; Anthropic's `url`
is the publishing venue itself (`alignment.anthropic.com`,
`transformer-circuits.pub`), which is the primary source for work it never puts
on arXiv. Anything reading across registers has to normalise these three
shapes; `research/build_corpus_survey.py` does it in one place.

**Status: extended to Google DeepMind, Meta AI, and Mistral AI. xAI confirmed to have
no papers findable by any method tried — no code was built for it.**

**`mistral_harvest.py`.** Mistral has no publications listing page at all (every path
tried 404s) and no arXiv query enumerates its output (`all:Mistral` collides with the
"MISTRAL" acronym in unrelated astronomy papers; arXiv has no affiliation-search field
at all -- confirmed live against its own API docs). What does work reliably: Mistral
announces every named model release on its own blog, already fetched for the
announcements leg at zero extra cost. Two of four real 2026 launches checked
("Shieldstral", "Robostral Navigate") were not on the maintained model-name list this
project had compiled earlier in the session, and both turned out to have real papers --
so this harvester sources candidates from Mistral's own in-window announcements instead,
over-generating on purpose (7 of 9 real candidates correctly resolve to nothing) and
letting arXiv resolution be the real filter. Resolution logic (`arxiv_resolve.py`) is
shared with `meta_harvest.py`, extracted once a second caller needed the same matching
logic that had already needed two bug fixes there.

**Real 3-month run: 9 candidates, 2 resolved (100% of the real papers present), 23
distinct authors, $0.09.** A third real `HTML_BUDGET` bug found here, on top of the two
found building Meta's leg: Mistral's papers carry their byline as a "Contributors"
section positioned 70KB-135KB into the document -- not near the top (D16's fix didn't
help) and not near the true end either (references/bibliography follows it, so a blind
head+tail split still missed it, and once landed on the wrong window, produced truncated
invalid JSON). Fixed by locating the heading and windowing around it, stopping at the
next heading rather than a flat budget. A fourth bug surfaced chasing the third: a call
billed by the API but that failed to parse was silently losing its own cost record --
fixed across all three papers harvesters, not just this one. Full account in
[docs/decisions.md](../../docs/decisions.md) D17, cost breakdown in
[docs/cost.md](../../docs/cost.md).

**xAI: re-checked live, still nothing to build.** `x.ai/research` and
`x.ai/sitemap.xml` remain fully Cloudflare-blocked; arXiv searches for the lab collide
with the "eXplainable AI" acronym and "grokking" (an unrelated ML phenomenon), and every
result found is a third-party paper evaluating Grok, never one published by xAI itself.
Matches D7's own register rationale (xAI's register-worthy events are compute contracts,
not papers) and D15's earlier finding -- reconfirmed, not assumed.

---

**Status of the Meta AI extension below: complete, run against the real window.**

**`meta_harvest.py`.** Meta's own publications listing 500s on a plain fetch; the
working alternative (`ai.meta.com/results/?content_types[0]=publication&years[0]=<YEAR>`,
paginated) has detail pages whose arXiv link is JS-populated -- confirmed live, no href
anywhere in server HTML, and no publication date either. So every paper is resolved to
arXiv by title match (exact first, relaxed-with-disambiguation as fallback) before
extraction, rather than by scraping a link that doesn't exist server-side, and the
window filter runs on arXiv's own `published` date, not Meta's page.

Proving run (5 papers) found 4/5 resolved on an exact title match once Meta's own site
suffix is stripped; the 5th needed the relaxed fallback, accepted only when the top
candidate's abstract shares enough vocabulary with Meta's description to rule out a
title collision (calibrated live: correct match 1.00 overlap, closest false positive
0.19; threshold 0.3). **Real 3-month run: 27 candidates, 6 resolved (100%), 5 in-window,
41 distinct authors, $0.43.** Two real bugs found by hand-checking output against the
live arXiv HTML, both in shared extraction code, not Meta-specific: `HTML_BUDGET`
truncating before the byline on a page with ~66KB of arXiv site chrome ahead of
`<article>` (checked live that this did not affect DeepMind's existing cached results);
and the relaxed-match fallback checking only the top-ranked candidate when the correct
paper was sitting second, behind an unrelated result that shared only an acronym. Both
fixed; full account in [docs/decisions.md](../../docs/decisions.md) D16.

---

**Status of the DeepMind extension below: complete, run against the real window.**
The original three-lab spike further below is complete and its conclusion stands.
D7 (docs/decisions.md) later put DeepMind in the
six-lab deep-coverage register, and the user asked to run this leg on it anyway,
explicitly reframing the goal: not a cross-lab person-importance score (already found
not to generalise), but org-level aggregate signal -- cadence, who's actually
publishing, collaborator organisations -- which is real regardless.

**`deepmind_harvest.py`.** No deterministic parser is feasible for DeepMind: confirmed
live that its arXiv HTML carries no reliable affiliation markup (sometimes "Google", not
"Google DeepMind"; often nothing at all). Every paper goes through the LLM
(`prompts/byline_extraction/v2.md`, generalized from v1's Anthropic-only prompt --
`is_anthropic` → `is_lab_staff`, default affiliation interpolated from a `lab_label`
instead of hardcoded). Corpus enumeration reuses `deepmind.google/sitemap.xml` (already
fetched for the announcements leg): it lists all 262 current `/research/publications/<id>/`
pages with a `<lastmod>`, sidestepping the publications *listing* page being JS-rendered.

**Run against the real 3-month window: 17 candidate papers, 15 processed, 65 distinct
authors, $0.86.** Two skipped, both source-fetch failures recorded honestly rather than
silently dropped or crashed past: one publication links to PLOS ONE, not arXiv or
OpenReview (out of this script's current scope -- it only follows arXiv/OpenReview
links); one has an arXiv id whose `/html/` rendering 404s (only the abstract/PDF exist).
Hand-checked the largest result (a 21-author white paper): only one author was actually
tagged DeepMind staff, correctly -- the rest are named collaborators from AIST, Oxford's
VGG, OpenAI, Cambridge and eight other institutions, and the model got every one of
those affiliations right rather than assuming DeepMind authorship because the paper
appears on DeepMind's own publications page.

**Two real bugs found and fixed by hand-checking the first live run, not caught by any
test beforehand.** (1) DeepMind's HTML renders attributes unquoted wherever the value has
no whitespace (`href=https://...`, not `href="..."`) -- a quote-only regex silently
matched nothing on every page, and the first proving run returned zero authors on 3/3
papers before this was caught. Fixed by also trying the page's own JSON-LD `sameAs`
field, which is more robust than href-scraping regardless. (2) `fetch()` had no
retry/backoff, so a single transient 403 from arXiv mid-batch crashed the entire run
instead of being one paper's problem -- fixed to match `fetch_announcements.py`'s
existing pattern, with source-fetch failures now recorded and skipped like LLM
extraction failures already were.

---

**Status of the original three-lab spike below: complete, not carried forward.** The
question that line of work asked was answered, and the answer was mostly negative. Kept
because the negative result is a finding worth reporting and the evaluation harness is
reusable.

Full reasoning is in [docs/decisions.md](../../docs/decisions.md); the procedure it
produced is [planning.md](../../docs/planning.md) §11, §11a, §11b.

## The question

Can a frontier lab's staff — and which of them matter — be derived from the author
lists on what the lab publishes? The specific hope was to reach the layer *below* the
media spotlight without LinkedIn or press coverage.

## What was built

| File | Purpose |
| --- | --- |
| `byline.py` | Nesting-aware byline extraction for Anthropic's three markup forms |
| `harvest_contributors.py` | Anthropic register: channels → articles → people, with LLM fallback |
| `llm_byline.py` | LLM extractor, cost recorded per call |
| `eval_byline.py` | Scores any extractor against the hand-checked reference |
| `alias_candidates.py` | Proposes person merges from the co-authorship graph |
| `deepseek_harvest.py`, `eval_deepseek.py` | Same, for DeepSeek's arXiv author lists |
| `openai_harvest.py`, `eval_openai.py` | Same, for OpenAI's credit sections |
| `build_*_page.py` | The three HTML contributor pages |

Prompts are versioned in `prompts/byline_extraction/`. Data lands in `../docs/`.
Total LLM spend across all three labs: **$4.51**, logged in [docs/cost.md](../../docs/cost.md).

## What worked

**Extraction, everywhere.** Both extractors performed well on all three labs, and after
adjudicating every disagreement by hand, **neither hallucinated a person**:

| Lab | Corpus | People | LLM F1 vs deterministic |
| --- | --- | --- | --- |
| Anthropic | 54 articles / 12 months | 175 | 0.978 |
| DeepSeek | 8 papers | 415 | 0.996 |
| OpenAI | 7 papers | 1,079 | 0.879 |

**Deterministic parsing beat the LLM where markup is known** — free, instant,
reproducible, and its failures are visible rather than plausible. The LLM earned a
narrow role as a fallback on pages the parser cannot read; it recovered 8 of 8 such
pages at Anthropic.

**The evaluation harness is the durable output.** It scores any extractor against a
hand-checked reference and prints every disagreement rather than averaging them. It
found more bugs in the deterministic parsers than in the LLM, and every catastrophic
score it ever produced turned out to be a harness fault, never a model fault.

**Roster aggregates are real signal.** Author counts across releases give headcount and
churn for private labs from primary sources: OpenAI 281 → 420 → 486, DeepSeek
197 → 264 → 328, with 49 people absent between two DeepSeek releases and 20 marked
departed by DeepSeek itself.

## What did not work

**Publication-derived importance does not generalise.** It needs frequent publication
*and* small author lists. Only Anthropic has both.

| | articles/yr | authors/paper | does frequency discriminate? |
| --- | --- | --- | --- |
| Anthropic | ~54 | 3–20 | yes |
| DeepSeek | ~3 | 200–330 | no — it measures tenure |
| OpenAI | ~2 | 300–486 | no |

Appearing on OpenAI's GPT-5 card places a person among 486. No threshold creates signal
that is not in the data.

**The one success case is narrower than it looks.** Anthropic's output is overwhelmingly
safety, alignment and interpretability, so the register is *"people who publish safety
research at Anthropic"*, not *"important people at Anthropic"*. Pretraining, RL,
inference and product engineering are largely invisible to it.

**Role labels do not transfer between labs.** "Core contributor" covers ~219 people at
OpenAI, 18 at DeepSeek, ~12 of 117 at Anthropic. Same string, elite at one lab and most
of the staff at another.

**Role structure is disappearing.** DeepSeek stopped marking core contributors after R1
(Jan 2025); OpenAI after o1 (Dec 2024). Two of three labs now publish author names with
no contribution structure at all.

**Corpus enumeration was manual at every lab.** No single query returns a lab's papers;
labs file under assorted author strings and have no index page. This turned out to be
harder than the extraction it feeds.

**`openai.com` is unreachable** — its CDN returns 403 to non-browser clients although
robots.txt permits crawling. arXiv was the only usable channel for OpenAI.

## What follows

- Person-level tracking is viable at Anthropic-shaped labs only. Coverage across the
  register is therefore deliberately uneven, and the design doc says so.
- No cross-lab importance score. Any such score would be dominated by publication
  cadence and would rank a mid-tier Anthropic researcher above OpenAI's chief scientist.
- Importance is sourced, not computed: the leadership register (planning §12) and
  press-reported moves, where the press does the filtering.
- Reaching non-publishing staff needs different sources. GitHub commit authorship is the
  next thing being tested (`../github/`).
