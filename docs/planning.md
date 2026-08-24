# Planning: Frontier Lab Intelligence (BIT Capital case study)

Working plan. Expected to be rewritten as the project teaches me things.

## 1. The task in one paragraph

Build a system that tracks the frontier AI labs and the people inside them, ingests what they publish, extracts structured and sourced insights, scores them, separates signal from noise, and delivers a digest plus alerts tailored to two internal audiences. Judged, in order: (1) engineering, (2) product usefulness, (3) the design document. Data science and domain knowledge must clear the bar but are not where the marginal hour goes.

The single question they will ask on opening the submission: **did it surface something we'd genuinely want to know, and did it keep the noise out?**

## 2. Constraints

| Constraint | Value |
| --- | --- |
| Time | 1–2 weeks part-time (~30–50h) |
| API budget | €100, receipts kept, spend instrumented per workflow |
| Stack | Python core + FastAPI, React/Next frontend, Postgres |
| Deployment | Railway or Render — managed Postgres + scheduled worker, live link |
| Register scope | Breadth and depth decided empirically after enumeration; rationale recorded |
| Digest cadence | 48h default; cadence and volume threshold are **config, decided empirically** |
| Deliverables | Git repo (not a zip), README clone-to-running in a few commands, schema + real data included, all prompts in repo, design doc ≤10 pages, optional video |

Two rules that shape daily work:

- **Cost is instrumented from day one.** Token and € per workflow is a required design-doc section; it cannot be reconstructed at the end.
- **I am the author of the design document.** It gets written incrementally alongside the build. AI assists; it does not draft it. They will not read a document that reads as AI-written.

## 3. Architecture

One shared core, two last-mile renderers. The brief is explicit: not two systems.

```
Register → Ingestion → Extraction → Scoring → Signal filter → ┬→ Investment-team output
                                                              └→ AI-team output
```

**Register.** Labs as first-class entities (research blog, org X account, publications index, GitHub org, model/system cards) alongside key individuals — founders and research leads, plus the layer below, where much of the real signal sits. Requires an explicit, defensible answer to "who counts as key and why". Entity resolution across X handle / arXiv author / GitHub account, with a personal post typed differently from an official lab post. Config in files, no UI.

**Ingestion.** Heterogeneous sources on a schedule, with source selection, rate limits, dedup, freshness, and incremental watermarks. A thoughtfully scoped set done well beats a broad set done badly — coverage must be justified.

**Extraction.** Per entity, key contributions over a rolling ~3-month window. Raw content → structured insights, attributed to person or lab, LLM doing the heavy lifting. **Every insight cites its primary source.** Explicit extraction targets include researcher moves and affiliation changes — the brief names departures and stealth-startup formation as top-tier signal, and they will not fall out of publication parsing on their own.

**Mapping layer (lab → public equity).** Most labs are private. The judgment being tested is connecting a lab development to where it lands for a public-equity investor: BIT's holdings, and the semiconductor and energy supply chains around them. This is a named component, not a prompt afterthought.

**Scoring.** Contributors and insights. Lean and defensible — inputs, weights, and evidence that it measures what it claims. An arbitrary weighted sum dressed up as a score is worthless; a simple model I can defend and have tested is the target.

**Signal vs noise.** Where taste shows. Suppress aggressively.

**Reports and alerts.** 48h digest readable in the app, plus alerts when something material appears. The judgment is the alerting logic: material to whom, and why. Both cite sources, both tailored per audience. **"Actionable" means the reader knows what it means and what to do, not just what happened.**

**Web interface.** Browse the register, see scored insights and *why they were flagged*, read past reports. Light. Not polished.

## 4. Operations

The stated bar: *engineer it like a production system you would be on call for.*

- Scheduled ingestion runs with retries, backoff, and per-source rate limiting
- Run state and watermarks persisted; re-runs are idempotent
- Health and run-history visible
- **System-failure alerting, distinct from content alerts** — the pipeline tells me when it breaks
- Expected agent failure modes handled deliberately: malformed output, hallucinated citations, source fetch failure, cost runaway
- Model choice per task with fallbacks, all recorded

## 5. Audiences

**Investment team (PMs, analysts).** What does this mean for our positions? A star researcher leaving to found a startup; a capability jump threatening a holding's moat or validating a thesis; shifts in the competitive map of public companies exposed to AI. Framed in implications, tickers, and theses — including companies BIT could take a position in, not only those it holds.

**AI team.** What should we adopt or investigate? A new orchestration or evaluation technique, a cheaper or better open model, a paper that changes how we'd build a pipeline. Framed technically.

Same core, different last mile.

## 6. Evaluation

Two separate evaluation problems. Both are required; conflating them is a trap.

**Extraction quality and hallucination control.** A labelled set of source documents with expected extractions. Measure: are the claims present in the source, does every citation resolve, does the attribution point at the right entity.

**Scoring validation.** A gold set of items with human-assigned importance scores, compared against system output, with the disagreements examined rather than averaged away. Build this early and don't skimp — it makes the difference downstream. Calibrate against history where possible: look at past lab outputs with known market consequences (DeepSeek's effect on NVIDIA and the energy complex is the obvious case) and check whether the scorer would have flagged them.

Where a fully honest ground truth isn't reachable, say so plainly and use a defensible proxy.

## 7. Open questions

- **BIT's positions.** Better than assumed: at least one fund document discloses positions summing to ~95% of the portfolio. Open: which funds publish at this level, how current, and how many funds to cover. Resolve by reading the documents, not by guessing. Fallback if coverage turns out thin: a hand-curated proxy portfolio, labelled as a proxy in the doc.

- **Register breadth and depth.** How many frontier labs there actually are is itself an open question — hence the enumeration step. Which labs get full individual coverage versus org-channel-only gets decided once I can see the real volume and quality per lab, and the reasoning behind the cut goes in the design doc. The brief's own guidance applies: depth beats coverage.
- **X/Twitter.** Gated and expensive; the Basic tier is roughly the entire budget. Decision deferred pending a spike measuring how much unique signal X adds across 2–3 labs. Default assumption if the spike is unconvincing: drop it and justify the coverage gap.
- **Digest cadence and volume.** 48h and a target item count, both config-driven, decided once real volume data exists.
- **Agent vs deterministic code.** Track every such bet and its rationale as it's made — the doc requires it and the on-site will probe it.

## 8. Sequence

Incremental. Nothing is built at scale before it's been proven at n=1.

1. **Research BIT Capital.** Goal, strategy, disclosed positions, exposure. What signals or events plausibly move each holding. Do this before writing pipeline code — the brief says so, and the mapping layer depends on it.
2. **Enumerate the labs.** Find out how many there plausibly are, then crude LLM prioritisation to pick the first 1–3 to work on. This score is disposable; its only job is choosing where to start. Register breadth is set later, on evidence.
3. **Build the register and schema** for those labs: lab → key people → layer below → publications (blog, social, GitHub, papers, model/system cards, talks, technical blogs). Get the schema right here; everything downstream sits on it.
4. **Ingest and eyeball the data.** Read it manually. Form opinions about what's actually valuable before automating the judgment.
5. **Score one entity by hand, then build the gold set.** Iterate the scoring model against it. Keep it simple and justifiable.
6. **Connect insights to 2–3 selected BIT positions.** Data → signal → action, end to end, on a small case.
7. **Crude app** to browse insights.
8. **Scale**: more positions, more labs, more sources — modularly, one axis at a time.

## 9. Design principles

- **Sources always.** Every insight resolves to a primary source.
- **Explainability.** When a model makes a decision, it produces an explanation with a citation.
- **Modularity.** Components are reusable and independently testable; adding a source or a lab is configuration, not surgery.
- **Validation per module.** Every module has a test that would catch it silently degrading.
- **Google-style docstrings.**
- **Audit trail.** The engineering path is recorded as it happens: decisions, rejected alternatives, agent-vs-code bets, cost, and where the agentic loop broke down. This feeds the design doc and the on-site, where they will ask where I cut corners and why.

## 10. Running logs (keep from day one)

Cheap now, unrecoverable later.

- **Decision log** — decision, alternatives rejected, rationale, consequence
- **Cost log** — tokens and € per workflow, plus receipts for reimbursement
- **Agentic-workflow log** — what my agents verified themselves, where the loop broke
- **Insight log** — the 3–5 most interesting real things the system surfaces, captured as they appear
