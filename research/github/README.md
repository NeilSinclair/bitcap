# GitHub-derived contributor research

**Status: run on all seven deep-coverage labs — Anthropic, OpenAI, DeepSeek,
Google DeepMind, Mistral, xAI, and Meta AI (D19 built DeepSeek; the other six
were re-checked live against fresh data, not just the original run).**
Per-lab config lives in
[`config/github_sources.yaml`](../../config/github_sources.yaml), not a hardcoded dict —
see that file's own header for the schema, `domain_shared`, and (as of Meta AI) support
for a lab whose commit evidence spans more than one domain.

**DeepMind, Mistral, xAI, in one line each:** DeepMind's public org (`google-deepmind`)
is easily the busiest single-org source found so far — 168 repos, 9,883 commits, 632
people — but every one of its 221 evidenced staff is tagged `confirmed_org_wide`, not
`confirmed`: the evidence is an `@google.com` commit, which is Alphabet-wide, not
DeepMind-specific, and conflating the two would overstate what the data actually shows.
Mistral (`mistralai`) evidences **zero** people via email — recent commits are almost
universally GitHub's privacy-relay noreply addresses — a real finding about that org, not
a harvesting gap; re-checked live (D18) after clearing its cache, one day on: same 12
repos, 1366 commits, still zero. No second Mistral GitHub org exists to add (the Meta AI
two-org pattern doesn't apply here) — `mistral-ai` exists but is dead/squatted, 0 public
repos, same as `meta-ai` was for Meta. xAI (`xai-org`) is the cleanest of the three: a genuinely lab-specific
`@x.ai` domain, still in active use as of September 2026 despite the February 2026 SpaceX
acquisition, evidencing 12 of 33 people directly.

**Meta AI (D14) needed two orgs and a two-domain evidence rule.** `facebookresearch`
(general FAIR/research, 395 in-window repos, 10,944 commits, 734 people) and `meta-llama`
(Llama-specific, 8 repos, 351 commits, 28 people) are both tracked under one `lab: meta-ai`
id — a third candidate, `meta-ai` itself, was checked and confirmed dead/squatted (0
repos) and is excluded. A fourth candidate, `facebookincubator` ("Meta Incubator," 104
repos, genuinely real), was checked and excluded on re-verification (see below) — general
systems engineering, not AI-research-specific. Both `@meta.com` and `@fb.com` are active
corporate domains in real recent commits (fb.com is the legacy domain, not yet retired),
so `aggregate()`'s `org_domain` parameter now accepts a list, not just one string —
checked with a set membership test rather than equality. Both domains are
Meta-corporation-wide, not AI-org-specific, so both tag `confirmed_org_wide`, same
reasoning as DeepMind: 347 of 734 at `facebookresearch`, 16 of 28 at `meta-llama`. Both
orgs re-checked live: numbers essentially unchanged, no new bot leaks beyond the
already-caught `facebook-github-bot` (full account in `docs/decisions.md` D19).

**DeepSeek (D19), built last, is the cleanest register of any lab checked.** `deepseek-ai`
— 19 repos, 15,359 commits, 129 people, 12 evidenced via a genuine lab-owned
`deepseek.com` domain (no shared-parent-company ambiguity), zero new bot leaks on a full
commit-level scan. One repo, `deepseek-harness`, is 97.5% of the entire corpus (14,981
commits, three weeks old, 210K stars) and reads exactly like the `OpenROAD-flow-scripts`
mirror problem below on the surface (`fork: false`, `mirror_url: null` either way) — but
its actual committers use `@deepseek.com` and Chinese personal-email providers consistent
with an internal team, not the sprawling unrelated-external-maintainer population a real
mirror has. Recorded in `config/github_sources.yaml`'s own notes rather than left for the
next reader to re-investigate from scratch.

**Six bots slipped through on real live runs, each fixed at the source.**
`copybara-github` (Google's internal source-sync tool, found via the DeepMind run: 199
commits, 11 repos, display name "Copybara-Service") and `copilot` (GitHub's own Copilot
coding agent, found via the facebookresearch run: 43 commits, 3 repos, resolved login
"Copilot" even though one of its own commit `name` fields is literally
"copilot-swe-agent[bot]"). Neither was caught by the existing `[bot]`/`-bot`/`stainless`
patterns — `copybara-github`'s login ends in `-github`, not `-bot`; `copilot`'s login has
no hyphen before "bot" at all. Both would have ranked in the top 10 committers by volume,
ahead of real staff, until removed.

The Mistral re-run (D18) found two more, one of a genuinely new kind: `speakeasybot`
(Speakeasy's SDK-generation automation, 20 commits/2 repos, login ends in "bot" with no
hyphen — same class of miss as the two above, confirmed live via its own profile) and
`maiengineering` (Mistral's own CI automation, 50 commits/4 repos) — but `maiengineering`
was caught by a completely different signal, since its login gives no hint of being a bot
at all: a blank profile, a role-based team mailbox (`engineering@mistral.ai`, not
personal), and half its commits carrying the raw commit `name` "Buildkite CI" instead of
a person's name. No login-pattern generalization would have caught it — only checking the
commit-level `name`/`email` fields directly did.

A sixth, `goreleaserbot` (release automation, 1 commit, found via the OpenAI re-check),
was the same class of miss as `speakeasybot` — no hyphen before "bot". Re-checking
Anthropic, DeepMind, xAI and DeepSeek at the same commit level (login/name/email, not just
the top-30 display) found no further leaks — every flagged account in those four turned
out to be either an already-caught bot, a real external contributor, or a real staff
member using an unusually named personal account, checked and left alone in each case
(full reasoning in `docs/decisions.md` D18/D19).

All six added to `BOT_LOGINS` explicitly by name rather than generalizing the pattern
each time, since none implies a rule that should catch others by resemblance.

Tests whether GitHub commit history can identify trackable people at a frontier lab —
the question the [papers research](../papers/README.md) answered negatively for
publication bylines.

**A note on the detailed tables below.** The Anthropic/OpenAI comparison in "What
worked"/"What did not work" (bot share, enrichment yield, medians) is the original
spike's point-in-time run and was not re-run as part of D18/D19's freshness checks —
only commit counts, bot classification, and employment totals were. The headline
figures at the top of this file are current as of D18/D19; the detailed percentages
further down should be read as "true when measured," not "true today."

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
