# CLAUDE.md

Frontier Lab Intelligence — a take-home case study for BIT Capital, a tech-focused investment fund.

Full plan: [docs/planning.md](docs/planning.md). Original brief: `docs/AI Engineer Case Study (Frontier Lab Intelligence).pdf`. This file is the working contract; planning.md is the reasoning behind it.

## Behavioral Guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## What we're building

A system that tracks frontier AI labs and the people inside them, ingests what they publish, extracts sourced structured insights, scores them, filters noise, and delivers a digest plus alerts to two audiences.

```
Register → Ingestion → Extraction → Scoring → Signal filter → ┬→ Investment team (implications, tickers, theses)
                                                              └→ AI team (technical: adopt or investigate)
```

**One shared core, two last-mile renderers.** Never two systems.

The bar this is judged against, in order: engineering, product usefulness, design document. The question they'll ask on opening it: *did it surface something we'd genuinely want to know, and did it keep the noise out?*

## Stack

- Python core (ingestion, extraction, scoring) + FastAPI
- React/Next frontend — light, functional, **not polished**; the brief says don't spend time here
- Postgres
- Deployed on Railway/Render: managed Postgres + scheduled worker, reachable by link
- uv is used for environment management

## Non-negotiables

These are graded requirements, not preferences.

1. **Every insight cites a resolvable primary source.** No citation, no insight. Unresolvable citations fail loudly.
2. **All prompts live in the repo** as versioned files, never inline string literals buried in logic.
3. **Cost is instrumented at the call site** — tokens and € per workflow, recorded as we go. This is a required design-doc section and cannot be reconstructed later.
4. **Clone-to-running in a few commands**, with schema and real data included. Check the README still holds whenever setup changes.
5. **Engineered like a production system you'd be on call for**: retries, backoff, per-source rate limits, persisted run state, idempotent re-runs, and **system-failure alerting that is distinct from content alerts**.
6. **Config, not code**, for what's tracked. Adding a lab, person, or source is a config change.

## Design principles

- **Explainability** — when a model makes a decision, it emits a reason and a citation alongside the output. The UI shows *why* something was flagged, not just that it was.
- **Modularity** — components reusable and independently testable; new sources plug in without surgery.
- **Validation per module** — each module has a test that would catch it silently degrading. Silent degradation is the main risk in an LLM pipeline.
- **Google-style docstrings.**
- **Typed boundaries** — structured schemas at every LLM boundary; validate and handle malformed output rather than trusting it.

## Expected failure modes — handle deliberately

Malformed LLM output · hallucinated or unresolvable citations · source fetch failure and partial runs · duplicate content across sources · entity-resolution collisions (same name, different person) · cost runaway · silent quality drift.

## Evaluation

Two distinct problems. Do not conflate them.

- **Extraction quality / hallucination** — labelled source documents with expected extractions. Are the claims in the source, do citations resolve, is attribution correct?
- **Scoring validation** — gold set of human-scored items; examine the disagreements rather than averaging them away. Calibrate against history where possible (e.g. DeepSeek's effect on NVIDIA and the energy complex).

Where honest ground truth isn't reachable, say so plainly and use a defensible proxy. A system they can't trust fails regardless of its engineering.

- Write unit tests for everything you do. Run these before commiting. 

## How we work together

- **Incremental.** Prove at n=1 before building at scale. Read the raw data manually before automating judgment over it.
- **Neil authors the design document.** Assist with it; never draft it wholesale. They will not read a document that reads as AI-written.
- **Record decisions as they're made** — especially every bet on an agent or LLM *versus* deterministic code, with the rationale. The on-site will probe where corners were cut and why.
- **Flag uncertainty rather than inventing an answer**, particularly about BIT's holdings or a lab's activity. Fabricated domain facts are worse than gaps here.
- **Concise over verbose, always.** In code, in docs, in commit messages.

## Running logs — keep current, they're unrecoverable later

- `docs/decisions.md` — decision, alternatives rejected, rationale, consequence
- `docs/cost.md` — tokens and € per workflow, plus receipts (€100 budget, reimbursed)
- `docs/agentic-log.md` — our own setup, what agents verified themselves, where the loop broke
- `docs/insights.md` — the most interesting real things the system surfaces, captured as they appear (3–5 go in the final doc)
- `docs/handover*.md` — per-workstream operational handovers: state when written, traps, known-open. Start here when picking the project up cold.

## Current state

The pipeline runs end to end: register, ingestion, extraction, dual-axis scoring, signal filter, digest and alerts, an API and a dashboard, on Postgres with a scheduled worker. Two scored corpora — announcements under `v9`, papers under `p1` (`docs/decisions.md` D57). `bitcap-db rebuild` reproduces the whole database from committed artifacts with no API key.

For what was last worked on and what it left open, read the newest entries in `docs/decisions.md` and `docs/agentic-log.md`, then the matching `docs/handover*.md`.

Open questions live in [docs/planning.md](docs/planning.md) §7 — positions coverage, whether X/Twitter earns its cost, digest cadence, register breadth and depth. Each is decided on evidence and the reasoning recorded, not settled by assumption.

**Parked backlog: [docs/planning.md](docs/planning.md) §13.** Recorded so it is not
lost, *not* queued. Never pick up a §13 item as part of unrelated work, never treat
one as implied by a nearby task, and never do a small piece of one while in the area.
Each is actioned only when Neil names it directly. Noticing that an item is still
outstanding is not an instruction to do it — flag it and move on.
