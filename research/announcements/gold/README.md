# Gold set — human review

20 announcements in `articles/`, one file each. The `gold` block in
every file is **pre-filled with the system's own consensus tags**. Your job is to
correct them, not to write them from scratch.

## Read this first: what this set is and is not

Pre-filling was chosen because labelling twenty articles cold — reading each one
end to end and hunting for supporting quotes — is slow enough that it does not
get done. The cost of that choice is **anchoring**: a reviewer accepts a
plausible wrong tag more readily than they invent a right one.

So this is **human-reviewed system output, not independent ground truth**, and it
should be described that way. It will catch tags that are wrong on their face. It
is weaker at catching tags that are wrong but plausible, and weakest of all at
catching things the system never tagged, because there is nothing on the page to
prompt you.

Two habits that recover most of the value:

1. **Start with the `needs_review: true` files.** Those are the ones where the
   three runs disagreed, so the model was least certain and your judgement is
   worth most.
2. **Check for what is missing, not only what is wrong.** Ask "what did it fail
   to tag" on each article, deliberately, because the layout will not prompt you
   to.

## What is in each file

| field | meaning |
|---|---|
| `text` | exactly what the model was given |
| `gold` | **edit this.** Pre-filled with consensus tags |
| `review.needs_review` | true when the three runs disagreed |
| `review.why` | what they disagreed about |
| `review.tag_votes` | how many of 3 runs produced each tag, e.g. `2/3` |
| `system` | the system's score and summary, kept for comparison. Do not edit |

A tag marked `2/3` was produced by two runs and not the third. Those are the
first things to look at.

- `sign`: `positive` | `negative` | `mixed` — direction of the **mechanism**, not
  of any company.
- `magnitude`, `confidence`: `high` | `medium` | `low`
- **An empty `mechanisms` list is a valid answer.** Several of these articles
  carry no transmission at all. If the system tagged one that should be empty,
  emptying it is a real correction.
- Every tag needs a quote from the text. If you add a tag, add its quote.
- `text_source: rss_summary` means only a title and short official summary were
  available, because openai.com blocks automated fetching.

## What this sample can and cannot measure

Stratified, not random: roughly three quarters of it scored above zero, against
roughly a fifth of the real corpus. Deliberate — a random draw would have been
mostly noise. It supports **per-item agreement and error analysis**, and does
**not** support corpus-level precision or recall. Any headline accuracy figure
from it would be inflated.

It was also drawn using the pipeline's own scores, so it cannot reveal a kind of
signal the system misses entirely. It tests whether the system tags correctly,
not whether it looks in the right places.

## Vocabulary

### Event types (choose exactly one)

- `frontier_model_release`
- `compute_commitment`
- `open_weights`
- `pricing_change`
- `capability_result`
- `corporate_finance`
- `regulatory_action`
- `research_result`
- `incremental_model_release`
- `developer_tooling`
- `product_launch`
- `enterprise_partnership`
- `safety_policy`
- `personnel`
- `other`

### Mechanisms

- `training_compute_up` — Training compute demand rises. Frontier labs commit more accelerators, power and capital to training runs. Triggered by scaling-law results, new frontier model announcements, or disclosed cluster build-outs.
- `inference_cost_down` — Cost per unit of inference falls. Distillation, quantisation, sparsity, better serving stacks or cheaper hardware reduce the cost of serving a token. The DeepSeek-R1 shock of January 2025 is the canonical event.
- `inference_volume_up` — Inference volume rises. More tokens served in aggregate: consumer adoption, agentic workflows that issue many calls per task, or reasoning models that spend more compute at test time. Often the second-order offset to inference_cost_down.
- `memory_intensity_up` — Memory content per accelerator rises. Longer context windows, KV-cache pressure, mixture-of-experts weights and test-time reasoning increase HBM and NAND content per system, independently of unit accelerator volume.
- `datacenter_power_up` — Datacentre power demand rises. Accelerator deployment converts into megawatts: grid interconnect queues, power purchase agreements, cooling, and siting near cheap generation.
- `networking_bandwidth_up` — Interconnect and optical bandwidth demand rises. Scale-up and scale-out fabrics, optical transceivers, DCI between campuses, and the shift of bottleneck from FLOPs to bandwidth.
- `open_weights_release` — Capable open-weight models released. A lab publishes weights competitive with closed frontier models, enabling self-hosting and undercutting per-token API pricing.
- `capability_jump` — Step change in model capability. A capability threshold is crossed that puts new tasks in reach — long-horizon agents, reliable tool use, domain expert performance.
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
