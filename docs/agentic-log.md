# Agentic log

**Reconstructed 2026-09-04, after the fact.** This log was specified on day one and
never kept — the first honest entry in it. Everything below is recoverable from git
history, `decisions.md` and the run table; the judgement calls at the end are marked
`[NEIL]` and are yours to fill or cut. Where a claim comes from a record rather than
memory, the record is named.

Feeds design-doc §6: "your own agentic setup, what your agents verified themselves,
where the loop broke down."

---

## The setup

**Claude Code, one repo, a written contract.** `.claude/CLAUDE.md` is the working
contract — non-negotiables, design principles, expected failure modes, and an explicit
"parked backlog, do not action without instruction" section. `docs/planning.md` holds the
reasoning behind it. The split matters: the contract is what an agent must obey, the plan
is what it should understand.

Two mechanisms in that contract did real work:

- **Running logs, written as things happened** — `decisions.md`, `cost.md`, `insights.md`.
  46 decision entries across 12 days. The instruction was "cheap now, unrecoverable
  later", and this file is the counter-example that proves it: the one log not kept is the
  one that had to be reconstructed.
- **A parked backlog that agents are forbidden to touch** (planning §13, five items).
  Added because agents kept doing adjacent work — "noticing that an item is still
  outstanding is not an instruction to do it."

**A custom review subagent.** `.claude/agents/bitcap-reviewer.md`, running Opus, read-only
(`tools: Read, Grep, Glob, Bash, ReportFindings` — never Edit or Write). Briefed to be
adversarial, to review the branch as a PR rather than the files it was pointed at, and to
check the repo's own contract — sourced citations, versioned prompts, cost recorded at the
call site, config-not-code, tests that would catch silent degradation — not just general
engineering standards.

**Git worktrees for parallel sessions.** A second agent worked a separate branch in
`.claude/worktrees/` on the GitHub repo-signals spike while the main line continued.

**Timeline.** 41 commits, 2026-08-24 to 2026-09-04, heavily back-loaded: 18 commits on
09-03 alone, 7 on 09-04, against 1–3/day for the first week. The first week was research
and manual reading; the pipeline was built in three days on top of it.

---

## What the agents verified themselves

**947 tests, 1 skipped.** Not incidental: the contract required "a test that would catch
it silently degrading" per module, and the review agent was told test quality mattered
more than anything else it looked at.

**Three independent review passes, and they found real defects.** In each case one agent
reviewed another agent's work, findings were reproduced before being accepted, and the
fixes landed with the review:

| Pass | Scope | Findings |
|---|---|---|
| `decisions.md:1693` | First code review, DB layer | Changed the schema design |
| **D21** | Register extension across seven labs | 9, all fixed |
| **D29** | The scheduled pipeline, two passes | **1 CRITICAL, 11 MAJOR**, all fixed |
| **D43** | Pipeline tab and login | 5 real defects, all reproduced first |

The three findings most worth citing, because they are the kind a human review misses:

1. **`ensure_schema` failed on exactly the case its docstring claimed to handle** (D29,
   CRITICAL). It called `create_all` before `command.upgrade`; `op.create_table` has no
   `checkfirst`, so pre-creating tables a pending migration was about to add killed that
   migration on every firing until a human stamped the database by hand. The existing test
   covered the never-migrated and at-head cases only, so it stayed green. The reviewer
   reproduced it against a throwaway sqlite file.

2. **Cost instrumentation had three holes, and cost is a graded section** (D29). Double
   counting (a 20-article firing would have reported $1.16 against $0.58 of real spend);
   drift spend — ~$5/month, nightly — recorded nowhere, so the monthly ceiling could not
   see it; and the papers leg outside the budget entirely.

3. **A test that was blind to the routes it was written for** (D43). It claimed to walk
   the app's own route table so a new route is covered the day it lands. A router mounted
   with `include_router` appears as one entry whose `path` is `None`, and `if not path:
   continue` skipped the entire `/api/pipeline/*` subtree — it probed six routes and zero
   pipeline routes. The routes were gated only because a *second* test hardcoded them:
   exactly the hand-maintained list this test existed to replace.

---

## Where the loop broke down

**The agent's own tooling killed the agent's own runs.** Pipeline runs 16 and 17 both
failed — `error: server restarted mid-run (uvicorn --reload)` and `killed by uvicorn
--reload while Claude edited files`. Two firings, ~35 minutes, lost to the dev server
reloading on edits made by the process being observed. Still visible in `bitcap-db status`.
It then caused a *second*, worse failure: a killed run leaves a `running` row, and the
concurrency guard asks "is there a running row", so one corpse blocked every future run
until D43 added reaping.

**A review run in the wrong order, recorded as such.** D43 opens: "`bitcap-reviewer` was
run against `2783c70..44dc5c6` *after* the commit, which is the wrong order and is why
this entry exists." Five defects were already committed.

**A fix reintroduced next to itself.** The budget guard's fan-out bug (D24) was found,
fixed, documented in `Budget`'s own docstring — and then reintroduced in `drift`, which
called `exhausted`/`spend` instead of the `begin_call`/`end_call` API written to prevent
it. Measured at **$13.00 against a $2.00 ceiling, 6.5× over**. D43: "the bug was not new,
it was reintroduced next to the fix."

**A test that passed against a 6.5× overrun.** The test written for that guard asserted
only `skipped > 0`. It now asserts the money. And the first attempt to reproduce the bug
was wrong in the other direction — an instant stub serialised the workers by accident and
showed the ceiling holding.

**A safety switch that became a coverage gap.** D10 deactivated scoring wholesale as a
guard against an uncontrolled LLM run. Agents respected it faithfully for weeks. The
consequence: xAI, Google DeepMind, Mistral and Meta AI had 61 articles sitting in the
corpus, fetched and never classified. The register said seven labs; the product could see
three. Classifying them cost $1.08 and moved connections from 612 to 809. An agent will
honour a constraint long past the point a human would have questioned it.

**Confabulated structure across labs.** D22 — the research pages carried
"Anthropic-shaped prose … asserting evidence that does not exist", generalised to every
lab because the first lab's page had that shape. The failure mode is not inventing facts;
it is inventing *parallelism*.

**A hypothesis that was wrong while the fix worked.** Removing `is_signal` (prompt v3→v4)
was adopted on a measured improvement in score reproducibility. The stated reason — that
the flag was an escape hatch discarding evidence — was falsified by the same experiment:
empty-tag runs went 39% → 47%, slightly *worse*. Recorded because an agent that reports
"it worked" without testing why it worked will get the next one wrong.

**The log that was not kept.** This one. Everything above was recoverable because
`decisions.md` was disciplined; nothing here is *new* information, but reconstructing it
took an hour that writing it as it happened would not have.


---

## 2026-09-05 — The corpus that quietly stopped being the corpus

**Where the loop broke, and it was not the model.** A backfill recovered 141
OpenAI articles from the Internet Archive on 09-02. The next fetch, one day
later, overwrote every one of them — the fetcher rebuilds its output file from
scratch, and the backfill was a separate script editing that same file
afterwards. Nothing raised, nothing logged, 1,194 tests stayed green, and the
lab that is 59% of the corpus was scored on its own meta descriptions for two
days. **No agent did anything wrong; the two scripts were each correct alone.**

**The tell was in the wrong direction.** The investigation started from "the v8
prompt halved our alert volume, v8 is a regression". Checking rather than
accepting it inverted the finding twice: first that v7's quotes did not resolve
against the corpus (50% of them), which looked like mass hallucination; then
that they *did* resolve against the archived text, so v7 was right and the
corpus had degraded under it. Two wrong conclusions were stated to Neil before
the third one held. Both were wrong in the confident direction.

**The eval could not have caught it.** The gold set carries its own copy of each
document, and 11 of the 20 were 2×–98× richer than what the pipeline actually
scored — 13,283 characters against 249 on the same article. Gold agreement was
measured on text production never sees, so the drift check would have kept
reporting health indefinitely. **An eval that reads different bytes than the
product is not an eval**, and nothing in the design said so out loud.

**A defect introduced and caught inside the same session.** The first version of
the fix trusted the archive's wildcard index. It missed `path-to-astra` — the
substantive half of a frontier launch — which an exact lookup finds instantly.
It surfaced only because Neil asked a question about a *different* thing (which
channel the Astra articles arrive on) and checking it properly meant querying
each URL directly. Nothing in the test suite would have found it: the fix was
green, live-verified at n=1 on two articles, and wrong on a third.

**What that says about verification at n=1.** Two articles recovered, both
correct, was taken as proof the mechanism worked. It proved the mechanism worked
*for articles the bulk query already found*. The n=1 rule catches "does this work
at all" and not "does this work for the cases I did not sample" — and the second
is where this project's failures have actually lived.

---

## `[NEIL]` — only you can answer these

Placeholders, not suggestions. Cut any that don't ring true.

- Where did you stop an agent and take over by hand, and why that point?
- Did the worktree parallelism pay, or did it cost more in reconciliation than it saved?
  (The GitHub spike is still uncommitted and unmerged — that's evidence either way.)
- How much of `planning.md` survived contact? §7's open questions were meant to be
  resolved on evidence; X never got its spike.
- What did you stop trusting the agents with over the twelve days?
- The review agent found defects in three passes running. Would you have caught them by
  reading the diff yourself, and how long would it have taken?

---

## 2026-09-05 — papers: three wrong answers, each caught by being asked to show the number

Operational picture of this leg: [`handover-papers.md`](handover-papers.md).
Rationale: [`decisions.md`](decisions.md) §D57.

**An estimate stated with the confidence of a measurement.** The plan costed
full-text paper classification at ~$4 and recommended against it on other
grounds. Neil's question was *"why do you think it will only cost ~$4?"* — and
the honest answer was that the figure assumed papers fit inside the existing
60,000-character budget, which nothing had checked. Measured, arXiv `/html/`
full texts average 182,974 visible characters, ~59,200 tokens, about **43x the
mean article**. Real cost $11.30. The recommendation happened to survive, but
the argument it rested on did not, and it was reversed for a different reason.
**The tell was not that the number looked wrong; it was that nobody had made it.**

**A defect the tests could not have found, caught in a count.** Mistral's paper
citations *are* its announcement URLs, and `raw_articles` is keyed on URL. The
first landing overwrote two 13,000-character announcements with 2,000-character
lead sections. No test failed, no exception raised — the only visible trace was
`47 inserted, 2 updated` where 49 inserted was expected. This is the same class
of failure as the OpenAI corpus overwrite above: two correct scripts, one shared
output key, silent loss. **Twice now, in one project, and the second time was
caught only because the first one taught us to read the counts.**

**A claim about our own evaluation that was simply false.** Neil was told the
announcement gold set was human-adjudicated. It is not — `gold_human/` was
deleted on 2026-09-01, and the set is cross-model adjudication (Sonnet 5
classifier, Opus 5 adjudicator). The error mattered because the papers set was
about to be labelled by Fable 5, and the whole honesty constraint was *don't mix
a model proxy with human ground truth without saying so*. The constraint was
being applied against a baseline that was itself a proxy. Corrected in D57, in
the builder docstring, and in a new `research/papers/test/README.md`.

**The review agent found eleven defects and three of them were real.** Each of
the three majors was verified the only way that counts — revert the fix, watch
the test go red, reapply. The eight minors were mostly comments that had drifted
from the code they described. `[NEIL]` — worth noting whether that ratio held
across the other review passes.

**What worked.** Committing the corpus as an artifact means `bitcap-db rebuild`
reproduces 306 articles, 47 papers and 126 paper connections offline with no API
key, which is the clone-to-running requirement and also the thing that made
every one of the above recoverable in seconds rather than dollars.

---

## 2026-09-05 — releases: a feature that measured itself into the wrong answer

Operational picture: [`handover-releases.md`](handover-releases.md).
Rationale: [`decisions.md`](decisions.md) §D61.

**The agent shipped a join that silently did nothing on its own headline
example, and every number it produced looked right.** Pairing releases to launch
posts was built with exact identifiers on the release side, on the stated
reasoning that an exact key at one end bounds the join. It produced 20 links.
They were all correct. The measurement was quoted in the plan, in the config
comment and in the decision record.

It was still wrong. The identifier pattern does not span a space, so "GPT-6
Astra: A new generation of intelligence" — the highest-scoring announcement in
the corpus, and the specific example the feature was written for — yields
`gpt-6`, while the release body yields `gpt-6-astra`. They never intersect. The
only announcements that linked were the ones that happened to hyphenate. The
20 links were a *biased sample presented as a result*, and nothing in the output
said so: no test failed, no count looked odd, and the sample happened to contain
the visually convincing cases.

**What makes this the interesting failure, rather than an ordinary bug:** the
agent had already run the exact query that would have exposed it. It listed the
Astra-window announcements and their identifiers while sizing the work, and did
not notice that the launch post it kept quoting was absent from its own link
output. **The check was performed and the answer was not read.**

**The review agent caught it, and the fix inverted the design.** Asked to verify,
the reviewer executed `release_links` on the two real rows and got `[]`. Chasing
that produced a second finding the agent had also missed: on identifiers alone
the launch post and "Legora reviewed 41 documents in minutes with GPT-6 Astra"
are *identical* — both exactly `{gpt-6}`. So the original rationale was wrong
twice over: exact matching did not avoid the customer stories, it avoided the
launch post. No identifier rule could ever have separated them; `event_type`
could, and gate 2 already trusted that axis. Precision and recall both improved:
20 links over 11 announcements with 2 customer stories became 25 over 5 with
none.

**A test that could not fail, again.** `test_the_denied_collision_never_extracts`
asserted that "Sonnet v2.0.1 release notes" extracts nothing — which is true with
*or without* the denylist it was written to defend, because `max_version_parts:
2` rejects three-part versions. It would have passed with the `sonnet-2` entry
deleted. Same shape as the CI defect in D54 and the `is_signal` test before it.
**Three times now.** The pattern each time: the assertion was written from the
intent rather than from a failing state, so nobody watched it go red.

**On the review loop.** The reviewer's two passes returned 9 and 8 findings. One
was a genuine correctness bug, one a real determinism bug (`max` over a set
breaks ties on hash order, so the stored evidence string changed between
processes), and the rest were comments that had drifted from the code — including
three comments still arguing *for the design that had just been replaced*. That
ratio is close to the papers leg's 3-of-11. The second pass verified its own
conclusions by reverting each fix and watching the tests go red, which is the
only form of this that is worth anything.

**One finding was declined, on purpose.** The reviewer noted `stats["links"]` has
no alert reading it, unlike the `coverage` the code cited as precedent — correct.
The agent chose not to add an alert, because zero links is the normal state of
most windows and there is no labelled set to calibrate a healthy count from, and
dropped the false analogy instead. **Recording that a gap exists is not the same
as closing it**, and §5 of the handover says so in those words rather than
letting a green dashboard imply otherwise.

**What worked.** Every claim in this feature was checked against the live
database rather than against the code, and that is what found all three original
problems — the doc-type mislabelling, the 186 pre-window releases and the
unconnected rows were invisible in the source and obvious in a query. It is also
what made the review's finding fixable in minutes: the corpus was right there to
re-measure against.

## `[NEIL]` — only you can answer these

- The "test written from intent, never watched fail" pattern has now produced
  three defects across three legs. Worth a standing rule that a new test must be
  demonstrated red before it counts?
