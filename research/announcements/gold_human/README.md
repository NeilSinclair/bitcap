# Gold set — independent human tagging

20 announcements in `articles/`, the **same articles and the same
numbering** as `../gold/`, with nothing filled in. File `07` here is file `07`
there.

**Do not look at `../gold/` before tagging.** That folder holds the system's own
answers. The whole value of this folder is that it was produced without seeing
them — it is the only thing in the project that can serve as ground truth for
the classifier, and one glance at the other folder removes that.

## Filling one in

```json
"gold": {
  "event_type": "compute_commitment",
  "mechanisms": [
    {
      "id": "training_compute_up",
      "sign": "positive",
      "magnitude": "high",
      "confidence": "high",
      "quote": "verbatim sentence from the text that supports this"
    }
  ],
  "categories": [
    {
      "id": "ai_compute_hosting",
      "sign": "positive",
      "confidence": "medium",
      "quote": "verbatim sentence"
    }
  ],
  "notes": "anything that was hard to call"
}
```

- `event_type`: exactly one, from the list below.
- `sign`: `positive` | `negative` | `mixed` — the direction of the **mechanism**,
  not of any company.
- `magnitude`, `confidence`: `high` | `medium` | `low`
- **An empty `mechanisms` list is a valid and expected answer.** Several of these
  articles carry no transmission at all. Empty is a real label, not a skipped one.
- Tag only what the text states. If you cannot quote it, do not tag it.
- `notes` is worth using when a call was close. Those are the cases where a
  disagreement with the system is informative rather than a simple error.

`text_source: rss_summary` means only a title and a short official summary were
available, because openai.com blocks automated fetching. Judge what is there.

## What this can measure

Because these labels are blind, comparing them against `../gold/` gives a real
measure of classifier agreement, including **omissions** — tags you found that
the system missed entirely, which a pre-filled set can never reveal.

It still cannot give corpus-level precision or recall: the sample is stratified,
about three quarters of it scoring above zero against roughly a fifth of the real
corpus, so any headline accuracy figure would be inflated. It also cannot reveal
a kind of signal the pipeline never looks for, because the sample was drawn from
what the pipeline already scored.

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
