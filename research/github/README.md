# GitHub-derived contributor research

**Status: run on Anthropic and OpenAI. DeepSeek not yet attempted.**

Tests whether GitHub commit history can identify trackable people at a frontier lab —
the question the [papers research](../papers/README.md) answered negatively for
publication bylines.

## The question

The papers work established that publication-derived importance does not generalise
across labs. GitHub was the next source to test, on the theory that lab staff commit
under attributable identities whether or not they publish.

Two parts, as scoped: **(1)** get the people, **(2)** decide who matters and find what
else they are doing.

**Headline: part (1) works, and works far better at OpenAI than at Anthropic. Part (2)
mostly does not work at either.**

## What was built

| Script | Does |
|---|---|
| `harvest_github.py` | REST enumerates org repos; GraphQL walks each default branch for 12 months. Per-repo disk cache, backoff on 403/429/5xx |
| `aggregate_github.py` | Bot filtering, account merging, per-person signal aggregation, mirror exclusion |
| `enrich_github.py` | Profile + personal-repo fetch per person; re-resolves employment with the profile in hand |
| `build_github_page.py` | Renders the register as a filterable HTML page showing evidence, not just verdicts |

Data in `../docs/`; caches (`github_cache/`, `github_profiles/`) make re-runs free.
Outputs: [`github_anthropics.html`](github_anthropics.html),
[`github_openai.html`](github_openai.html). 35 tests in
`tests/test_aggregate_github.py`. Per-lab config (email domain, work-handle suffix) lives
in `LABS` in `aggregate_github.py` — adding a lab is a config change.

## What worked

**Employment is evidenced here, not inferred.** This is the material advance over the
papers work. 61% of human commits in the org carry an `@anthropic.com` address, and a
commit made from a corporate address is direct evidence of employment. Bylines only ever
*asserted* affiliation. Three signals, recorded separately so a guess is never promoted
to evidence:

| Signal | People | Strength |
|---|---|---|
| Commit from `@anthropic.com` | 161 | Direct evidence |
| Profile `company` names the lab | 9 | Self-reported, reliable |
| `-ant` work-handle convention | 6 | Guess; flagged as such |
| **Total identified as staff** | **176** | of 426 contributors |

### The two labs are not alike

Both orgs were harvested identically. The results diverge sharply:

| | Anthropic | OpenAI |
|---|---|---|
| Active repos | 59 | 99 |
| Commits in window | 16,896 | 19,705 |
| **Bot share** | **80%** | **12%** |
| Corporate-email share of human commits | 61% | **82%** |
| Distinct people | 426 | **1,146** |
| **Staff evidenced** | **176** | **456** |
| Staff with ≥50 commits | 13 | **52** |
| Staff with a blog or X handle | 48 (**27%**) | 51 (**11%**) |
| Overlap with that lab's papers register | 3/175 (1.7%) | 43/1,078 (**4.0%**) |

The bot share is the cause of most of the rest. Anthropic's public org is heavily
generated — Stainless-authored SDKs and release automation account for four commits in
five — whereas OpenAI's is largely hand-written. So OpenAI's org exposes 2.6× the staff
and 4× the substantial contributors.

**This inverts the papers finding.** In the papers work OpenAI was the *sparse* lab:
rare publications with author lists in the hundreds, useless for person discovery. On
GitHub it is the densest people source found at any lab so far. Neither source ranks the
labs consistently, which is further evidence against any cross-lab importance score.

OpenAI also runs a visible contractor tier: a `c-openai.com` email domain (5 people, 19
commits). They are not counted as staff here. The distinction is recorded rather than
hidden, since "works at the lab" and "is employed by the lab" are different claims.

**Alias resolution is far cheaper than in papers.** Commit email is a hard key: two
accounts sharing a real address are the same human, no corroboration needed. The papers
work needed a co-authorship graph to guess at this. Two merges applied
(`dtmeadows`/`dtmeadows-ant`, and one other).

**The evaluation habit paid off again.** Every consequential bug was caught by looking at
raw data or by a test, not by the code failing:

- A test caught a real two-way alias cycle — the work-suffix rule re-merged an account the
  email rule had already chosen as canonical.
- `users.noreply.github.com` is issued *per account*; treating it as an identifier would
  have merged strangers. Excluded explicitly, with two tests pinning it.

**Zero marginal cost.** No LLM in this pipeline. The GitHub API is free at 5,000 req/hr
authenticated; the whole run fits inside one window. Contrast $4.51 for the papers work.

## What did not work, or needs care

**Bots dominate absolutely.** 13,439 of 16,896 commits (80%) are release automation and
code generators. `actions-user` and `stainless-app[bot]` outrank every human in the org. A
naive commit ranking is not merely noisy, it is meaningless.

**GitHub's `fork` flag does not detect mirrors.** `OpenROAD-flow-scripts` reports
`fork=false` and `mirror_url=null`, yet is a declared read-only mirror of an upstream
chip-design project. It contributed 2,441 commits by 43 external maintainers and **topped
the raw ranking** — the apparent top five contributors to Anthropic were EDA engineers
with no connection to the lab. Detected from the description instead, and the exclusion is
printed on the output rather than applied silently.

**The obvious fix for that was wrong.** Excluding repos by low corporate-email share would
have dropped `buffa` (2% corp email), a genuine first-party project whose top committer's
profile names Anthropic as employer. Staff commit under personal addresses often enough
that repo-level thresholds are unsafe. Caught only by checking a profile by hand before
implementing.

**The population is disjoint from the papers register — measured, not assumed.** 3 of 175
papers-register people appear here (1.7%), all marginal committers (1, 1 and 9 commits).
GitHub surfaces who ships public product code; papers surface who publishes safety
research. GitHub does not repair the papers coverage gap; it opens a different one.

**Contribution is long-tailed at both labs, so "important" stays a small set.** Anthropic:
median 5 commits, 43 of 176 staff have exactly one, 30 have ≥20. OpenAI: median 4, 124 of
456 have exactly one, 84 have ≥20. Whatever the importance rule, it selects a few dozen
people per lab and the rest is a roster — the same shape the DeepSeek papers work reached.

**The enrichment yield is the weak part, and it got worse with scale.**

| | Anthropic | OpenAI |
|---|---|---|
| Staff | 176 | 456 |
| ...with a blog or X handle | 48 (27%) | 51 (**11%**) |
| ...with a bio at all | 46 | 72 |
| ...with a personal repo ≥50 stars | 20 | 21 |

Note the absolute numbers barely move — ~50 people with a public channel at either lab —
while the staff count grows 2.6×. Finding more staff does **not** find more channels.
GitHub answers *who works here* better than any source tried so far, and answers *what
else are they doing* for a shrinking minority of them.

**Public repos are the application layer, not the research org — at both labs.** Anthropic:
Claude Code, plugins, SDKs, cookbooks. OpenAI: `codex`, agents SDKs, language SDKs,
cookbook, chatkit. Neither exposes the people building models. That is a ceiling on the
method, not a defect in it, and it decides who the output is *for*: this is AI-team
signal about the application layer, not investment signal about model capability.

A handful of OpenAI repos are exceptions worth watching in their own right, as product
direction rather than as people: `codex-security` (10k stars), `tunnel-client` (a "Secure
MCP Tunnel" — enterprise plumbing), `snap-o` (Android capture tooling, implying mobile
investment), and `parameter-golf` (smallest LM fitting in 16MB).

**Per-person importance is still not computed.** Commits, repo breadth and recency are
recorded side by side and deliberately not collapsed into one score, consistent with the
papers finding. Commit count measures who maintains public code, not seniority.

## Caveats

- Default branch only; work merged by squash attributes to the merger in some repos.
- The 246 "unknown" contributors are mostly genuine external open-source contributors
  (e.g. `stephentoub`, Microsoft, on the C# SDK), but the bucket certainly contains some
  staff committing under private addresses. The classification is deliberately
  conservative — it under-claims rather than over-claims.
- At OpenAI, profiles were fetched for the 675 contributors who are staff or have ≥2
  commits; the 471 single-commit contributors below that bar are in the register but
  unenriched, flagged `profile_fetched: false`.
- `mockturtle` appears to be an undeclared mirror of the EPFL logic-synthesis library. Its
  contributors fall out as "unknown" correctly, so it was left in rather than special-cased.

## What follows

**Where this lands in the product.** The population GitHub reaches is application-layer
engineering. That is genuine signal for the AI-team renderer ("is this SDK maturing,
should we adopt this pattern") and weak signal for the investment renderer, since public
repo activity does not move a thesis on compute or the energy complex. That matches the
two-audience split already in the brief — one shared register, two last-mile renderers —
rather than requiring anything new.

**Open.** Whether to run DeepSeek; whether the ~50-per-lab channel yield justifies the
ingestion work, or whether GitHub should be a *roster and employment-evidence* source
only, with channels sourced elsewhere.

See [`docs/decisions.md`](../../docs/decisions.md) for the decision record and rejected
alternatives.
