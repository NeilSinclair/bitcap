# Gold set — adjudicated reference labels

20 announcements in `articles/`, plus 10 in `hard_cases/`. The `gold` block in
each is the **adjudicated** answer: every tag judged against the full article,
with the reasoning recorded in [`adjudication.yaml`](adjudication.yaml).

## What this is, stated plainly

**Not independent human ground truth.** Human labelling was considered and
dropped on 2026-09-01 — twenty articles at ~14,000 characters each was not going
to get done, and an empty folder implying it might is worse than none. Say this
in the design document rather than letting a reader assume otherwise.

**Cross-model adjudication.** The classifier is `claude-sonnet-5`. The
adjudicator is `claude-opus-5`, reading the whole article, the classifier's
output, and a *second independent run* of the classifier, then deciding each
tag. Different model, different information, recorded reasons.

Four things make it worth more than a model grading itself:

1. The adjudicator sees two independent runs, so run-to-run disagreement routes
   attention to where the classifier was least stable.
2. Every quote is mechanically verified to be in the document first, so the
   judgement is only ever about whether the quote **supports** the tag — never
   about whether it exists.
3. Every verdict carries a reason, so any one can be spot-checked in a minute
   without re-reading the article.
4. The adjudicator is held to the same rule as the model: a quote it supplies is
   run through the same gate and the build fails if it is not in the document.

**Measured bias, from a blind test ([decisions.md](../../../docs/decisions.md)).**
The adjudicator labelled five unseen articles from scratch before the classifier
touched them. Tag selection agreed completely — all 13 of its tags were also
produced by the classifier — but on ordered fields the adjudicator was **higher
in 11 of 15 disagreements**. Its gold-set changes run the same way, 7 raised
against 1 lowered. Where an adjudicated label differs from the classifier only
on magnitude or confidence, treat the classifier's value as the better prior.
The blind test also found the classifier producing two supported tags the
adjudicator missed, one of them explicitly and wrongly rejected in writing.

**The standing blind spot:** the adjudicator wrote the vocabulary it is judging
against, so it cannot find a kind of signal that both it and the classifier
miss. `vocabulary_gaps` at the foot of `adjudication.yaml` is the partial
answer — four are recorded — but a gap nobody thought of stays invisible.

## What the adjudication changed

Of 20 articles, **7 were confirmed entirely** and 13 carried at least one
departure — 21 tag-level verdicts in all.

| Article | Change | Effect |
|---|---|---|
| `10` US government directive | `export_controls` low → **high/high**, plus a missed `inference_volume_up` negative | **13.3 → 80.0** |
| `04` GPT-5.6 pricing | `inference_cost_down` confidence → high; dropped a weak `agentic_workflows` | 33.3 → 66.7 |
| `20` OpenAI on AWS | restored `inference_volume_up` at medium/**high** | **0.0 → 26.7** |
| `30` Copilot | dropped `inference_cost_down` (marketing, no figure); volume → medium/high | 0.0 → 26.7 |
| `16` GPT-5.6 Sol | dropped `custom_silicon_substitution` — Cerebras is not OpenAI's own silicon | unchanged |
| `15`, `16` | `accelerator_custom_si` → **mixed** — a non-NVIDIA deployment inside an NVIDIA bucket | unchanged |

The two largest corrections are both under-scoring, and both on articles a fund
would most want to get right: a government forcing a frontier lab to disable its
two best models, and OpenAI's models reaching general availability on AWS when
Amazon is 11.03% of the portfolio.

## Measuring a run against this

```
python research/announcements/run_gold.py --fresh --tag <name>
python research/announcements/gold_metrics.py research/test_results/<run>.json
```

Reports event-type agreement and Cohen's kappa, per-axis precision/recall/F1,
attribute agreement on shared tags, score MAE/RMSE/correlation, and the citation
gate's drop rate.

**One bias must travel with every number:** the adjudication was built from a
run, so *that* run scores against it optimistically. These are a baseline for
the **next** run — a changed prompt, a cheaper model, a new vocabulary — not a
report card on the run that produced them.

## `hard_cases/`

Ten articles that fell outside the three-month window when it was cut. Kept
because they are genuinely hard and important — an open-weights frontier
release, a government recall, a 5GW capacity deal, a $65bn raise. Useful for
error analysis and **excluded from any accuracy figure**, because they are not
part of what the pipeline scores. Do not average them in.

## How the sample was drawn

Rebuilt 2026-09-01. Ten articles retained from the previous set; ten drawn
**at random within lab strata** (`rebuild_gold.py`, seeded). Random, because a
draw blind to the system's own opinion is the only part of the set that can
reveal what it misses — five of those ten score zero on both axes, and the
previous set contained no such article at all. Stratified by lab, because an
unstratified draw came back five-five and left the set at 45% OpenAI against a
79.6% OpenAI corpus.

| | gold | corpus |
|---|---|---|
| OpenAI | 70% | 79.6% |
| Anthropic | 25% | 19.4% |
| DeepSeek | 5% | 1.0% |

Anthropic stays over-weighted because five of the retained ten are Anthropic. A
known bias, not a claim of representativeness.

## What this sample can and cannot measure

It supports **per-item error analysis** and **run-to-run comparison**. It does
**not** support a corpus-level precision or recall figure: n=20, the two halves
were drawn on different principles, and the reference is adjudication rather
than external truth. Any headline accuracy number from it would be misleading.

## What is in each file

| field | meaning |
|---|---|
| `text` | exactly what the model was given |
| `gold` | the adjudicated answer — regenerate with `apply_adjudication.py` |
| `review.adjudicated` | true, with the adjudicator, classifier and runs compared |
| `review.departures` | how many tags the adjudicator changed |
| `system` | what the pipeline said at pre-fill time, kept for comparison |
| `archive_snapshot` | present when the text came from the Internet Archive |

To change a label, edit `adjudication.yaml` and re-run
`apply_adjudication.py` — never hand-edit a `gold` block, or the reasoning and
the labels drift apart.

## Vocabulary

Generated from `config/` by `gold_readme_vocab.py`. Do not
hand-edit: a test asserts it matches the config the pipeline uses.

### Event types (choose exactly one)

- `frontier_model_release`
- `incremental_model_release`
- `capability_result`
- `pricing_change`
- `compute_commitment`
- `open_weights`
- `research_result`
- `product_launch`
- `developer_tooling`
- `enterprise_partnership`
- `corporate_finance`
- `regulatory_action`
- `safety_policy`
- `personnel`
- `other`

### Mechanisms — the investment axis

- `training_compute_up` — Training compute demand rises. Frontier labs commit more accelerators, power and capital to training runs. Triggered by scaling-law results, new frontier model announcements, or disclosed cluster build-outs.
- `inference_cost_down` — Compute and memory needed per token falls. Efficiency: the same output takes less hardware. Distillation, quantisation, sparsity, grouped-query attention and better serving stacks reduce the FLOPs and the resident bytes required per token. The DeepSeek-R1 shock of January 2025 is the canonical event. Tag this only when the *work* per token falls, not whenever a price falls. A cheaper accelerator, a discount, or margin competition lowers the price of a token without changing the hardware content of one; that is a pricing_change event, not this mechanism.
- `inference_volume_up` — Inference volume rises. More tokens served in aggregate: consumer adoption, agentic workflows that issue many calls per task, or reasoning models that spend more compute at test time. Often the second-order offset to inference_cost_down.
- `memory_intensity_up` — Memory content per accelerator rises. Longer context windows, KV-cache pressure, mixture-of-experts weights and test-time reasoning increase HBM and NAND content per system, independently of unit accelerator volume.
- `datacenter_power_up` — Datacentre power demand rises. Accelerator deployment converts into megawatts: grid interconnect queues, power purchase agreements, cooling, and siting near cheap generation.
- `networking_bandwidth_up` — Interconnect and optical bandwidth demand rises. Scale-up and scale-out fabrics, optical transceivers, DCI between campuses, and the shift of bottleneck from FLOPs to bandwidth.
- `capability_jump` — Step change in model capability. A capability threshold is crossed that puts new tasks in reach -- long-horizon agents, reliable tool use, domain expert performance.
- `agentic_workflows` — Agentic and tool-using systems displace seat-based software. Work moves from humans operating software to agents calling APIs, pressuring per-seat pricing and shifting value to systems of record and data owners.
- `on_device_inference` — Inference moves to edge and client devices. Smaller models and NPUs push a share of inference off datacentre GPUs and onto phones, PCs and embedded devices.
- `export_controls` — Export controls and trade policy tighten. Restrictions on advanced accelerators, HBM, or semiconductor capital equipment reaching particular jurisdictions; tariffs on hardware.
- `ai_capex_investments` — AI infrastructure capital expenditure. Capital committed to AI infrastructure -- datacentre build-outs, accelerator and chip purchases, power and energy supply. A positive sign is spending being committed or accelerated; negative is spending slowed or withdrawn because of financing cost, depreciation, utilisation or weak returns.
- `custom_silicon_substitution` — Labs move to in-house and custom accelerators. A frontier lab designs, co-designs or deploys its own inference or training silicon, displacing merchant accelerators.
- `lab_capital_access` — A lab's access to capital changes. Funding rounds, IPO filings, credit facilities and other events that change how much a lab can commit to future compute. The transmission is indirect but real: capital raised is the precondition for the cluster and power commitments that reach suppliers.

### Categories

- `memory_storage` — Memory and storage. Makers of DRAM, HBM, NAND and the storage devices built from them. The binding constraint on AI serving, and the fund's largest category.
- `foundry_logic` — Foundry and logic. Contract manufacture of leading-edge logic, and merchant logic makers.
- `semicap_equipment` — Semiconductor capital equipment. Tools and assembly equipment sold to fabs and packaging houses.
- `accelerator_custom_si` — Accelerators and custom silicon. AI accelerators, custom ASICs, and the controller and interconnect silicon that surrounds them.
- `analog_power_semi` — Analog, power and embedded semiconductors. Power delivery, analog, and embedded silicon for datacentre and edge.
- `networking_optical` — Networking and optical. Optical transport, transceivers and datacentre interconnect.
- `ai_compute_hosting` — AI compute hosting. Operators that host AI compute for third parties - datacentre capacity, power and GPU fleets sold under contract.
- `hyperscale_platform` — Hyperscale platforms. Cloud and platform businesses that both build and buy frontier AI.
- `fintech_consumer_fin` — Fintech and consumer finance. Consumer brokerage, lending and financial infrastructure.
- `health_insurance` — Health and insurance. Digital health, health insurance and clinical-stage businesses.
- `consumer_marketplace` — Consumer marketplaces and apps. Marketplaces and booking or transaction platforms.
- `materials_mining` — Materials and mining. Copper, lithium and specialty chemicals.

### Practices — the AI-team axis

Tagged independently of the mechanisms. An article can carry no
transmission to an investor and still be the most useful thing an
engineer reads this week, and the reverse is equally valid.

Each practice tag takes `action` (`adopt` / `investigate` / `watch`),
`impact`, `confidence`, a `reason` and a verbatim `quote`.

- `model_capability` — A model got better at something we care about. A released or updated model changes what is worth putting in front of our users: better at a task, faster, cheaper per unit of work, broader in modality, longer in context, more reliable, or newly self-hostable. This is a property of a model, not a technique. Always record which dimensions moved -- an undifferentiated "it got better" cannot be ranked or filtered, and "better at agentic coding" and "better at vision" are different decisions for different teams. Record at most three, and record the ones the document leads with. A frontier launch plausibly improves nearly every dimension; on a first run the three big launches in the gold set drew 8, 7 and 7 of 9, which says only "this is a big launch" and destroys the filtering this field exists to provide. Small releases were tagged tightly at 1-2, so the cap costs nothing there. One dimension with a number behind it beats four without.
    - `coding` — Software engineering, code generation, debugging, repo-scale work.
    - `reasoning` — Multi-step problem solving, maths, planning, knowledge work.
    - `agentic_tool_use` — Tool calling, longer task horizons, autonomy, memory in production.
    - `vision_multimodal` — Image, video, audio or document understanding.
    - `long_context` — Larger usable context, better retrieval or recall within it.
    - `latency` — Faster response at comparable quality. Distinct from cost -- it changes what is buildable interactively, not what it costs.
    - `reliability` — Fewer failures, better instruction following, steerability, refusal calibration.
    - `cost` — Lower price or lower compute per unit of output for the same quality.
    - `open_weights` — Weights released or licence changed such that we could self-host or fine-tune.
- `orchestration` — How you would build the pipeline. Techniques for structuring work around a model rather than inside it: agent scaffolding, planning and routing loops, multi-agent decomposition, context and memory management, tool-use patterns, retrieval design, caching strategy, prompt structure that materially changes behaviour.
- `evaluation` — How you would measure or trust it. Benchmarks, eval methodology, judging and grading approaches, red-teaming and adversarial testing methods, safety evaluation technique, monitoring and drift detection, and honest accounts of what an eval does not measure. Safety technique is folded in here on purpose rather than given its own tag. A jailbreak defence, a cyber safeguard and a coding benchmark are all answers to "how would we know whether this is good enough to ship". Split it out only if the human-versus-system disagreements justify it.
- `serving_efficiency` — How you would run it cheaper or faster. Implementation-level techniques that reduce cost or latency for the same output: quantisation and distillation recipes, attention and KV-cache optimisation, batching and scheduling, speculative decoding, kernel and serving-stack improvements, hardware-specific tuning. Related to but distinct from the `inference_cost_down` mechanism. That one asks whether the industry's cost per token fell, which transmits to suppliers. This one asks whether there is a technique WE could apply. A lab can move the industry curve without telling us how; it can also hand us a recipe that changes nothing at industry scale.
- `integration` — What you would have to change to use it. Anything that creates or removes engineering work at the interface: SDK and API changes, new protocols and tool interfaces, model deprecations and retirements, breaking changes, migration requirements, rate limit and quota changes, region availability, data retention and licensing terms that gate production use. Deprecations are the reason this tag exists. They are the one category an AI team is genuinely angry to have missed, they are cheap to detect, and no other tag in either vocabulary carries them.

`model_capability` requires between 1 and 3 dimensions. Naming most of
the list says only "this is a big launch" and cannot be ranked.
