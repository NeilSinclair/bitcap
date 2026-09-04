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
