# Next Steps

## App / Website 

### Deployment

- Setup React page/website/app on Render/Railway. 

- Setup Auth

- Setup CI/CD from GH for page

- Ensure regression tests run before any merge with main

### Design

- Incorporate alerts: recent news / data events with high scores

- Add better score filtering in the app (mechanism TBD)

- Better score search in the app - display events that effect a specific category of investments and display events that effect specific positions; same kind of filtering for the AI events.

- Incorporate a model reliability tab. This should have a timeline of results with the LLM classifier run compared to the gold set so we can see model reliability over time. This test should be run every time the pipeline is run (this, specifically would sit outside of design and in the Data collect / Pipeline section below), however there should also be a button to run it manually. Let's discuss which comparison metrics to show.


### Scoring / gold set

- **`capability_result` vs `safety_policy`: a safety document that reports a
  *crossed* threshold is being scored as a voluntary policy.** "Safety overview:
  GPT-6 Astra" states the model is "our first to reach the Critical level of
  cybersecurity capability under our Preparedness Framework" — a crossed
  capability threshold, which `prompts/announcement_scoring/v9.md:205` defines as
  `capability_result` (event weight 4). It is classified `safety_policy` (weight
  1, `config/scoring.yaml`), so it scores **10.0** where the boundary would put
  it near 40, and it sits far down a score-sorted feed while the launch post it
  accompanies scores 100.

  `config/scoring.yaml`'s own header documents the identical v1 failure: "the
  first US export control on a frontier model scored 20 because it was
  classified `safety_policy` (weight 1) rather than as an action taken ON the
  lab." Same shape, new instance — the boundary does not distinguish a lab
  *announcing a policy* from a lab *reporting it crossed a line*.

  Fix is a v10 prompt boundary change plus a full re-classification and a gold
  re-run, so it wants its own branch and its own cost line. Raised 2026-09-05
  while building the duplicate collapse (docs/decisions.md D57), **not
  actioned**. Noted there because the collapse deliberately does *not* fold this
  row into the launch card — merging it would have hidden the mis-score instead
  of surfacing it.

  **Three instances now, and they argue for the full re-score over the cheap
  one.** The duplicate collapse surfaced two more cases of one initiative
  getting two `event_type` labels, both found by Neil's spot-check (D57b):

  | one thing | labelled |
  |---|---|
  | "Introducing Intelligence Age", same day, two URLs | `other` / `safety_policy` |
  | GeneBench-Pro launch and its deep-dive | `capability_result` / `research_result` |
  | Daybreak access expansion, two posts one day | `enterprise_partnership` / `product_launch` |

  These are not the same bug as the `capability_result` boundary above — that
  one is a *wrong* label, these are *inconsistent* ones. But they share a cause
  worth naming: the event vocabulary has no way to say "this post is part of a
  larger announcement", so the model picks whichever label fits the fragment in
  front of it.

  Cost consequence, so it is decided on evidence rather than budget: a targeted
  re-score of `safety_policy` + `capability_result` is ~$1.20 and catches only
  articles moving *out* of those classes. A full non-release re-score is ~$8.70
  (314 articles at the measured $0.0276) and catches movement in both
  directions. Inconsistency across `research_result`, `product_launch` and
  `enterprise_partnership` is movement *into* the affected classes, which the
  cheap option cannot see. **Recommend the full re-score.**

  Deliberately not worked around in the collapse. Two of those pairs are
  refused by the event-type gate, which is the one rule stopping the system
  folding the Astra safety disclosure into the launch card — and Neil's
  spot-check independently confirmed that split is right. Weakening a correct
  gate to compensate for a noisy input would trade a real guarantee for two
  edge cases.

- **Declined: the cross-event-type "story" link.** After the collapse, GPT-6
  Astra is three rows — release, safety, customer stories — because gate 2
  refuses to merge across event types. A "story" link would group them in the
  feed as one thing. Not built: its value in the Astra case is almost entirely
  rescuing the safety row from the mis-scoring above, and building it now would
  paper over the bug rather than fix it. Revisit only once the
  `capability_result` boundary is corrected, and only if that row still fails to
  surface on its own merits. Recorded 2026-09-05 (D57).

- **`accelerator_custom_si` sign disagrees with the gold label** on article `15`
  (Jalapeño). Human says `mixed`, every model version v7/v8/v9 says `positive`.
  Stable across a 53x change in input text, so it is the prompt or
  `config/categories.yaml`, not the article. Full write-up and the reason it
  matters for holding routing: [docs/gold_review.md](gold_review.md), last
  section. Raised 2026-09-05, **not actioned**.

# Data collection / Pipeline

- Collect the names of the leaders of the frontier labs; this has to be inserted as part of the pipeline

- Set up X API to scrape X-pages

- Decide on a rule for selecting researchers based on papers - we want to limit the number, but include important researchers. Smaller is better now for this stage of the build.

- Decide on a rule for selecting contributors to GH for the AI labs - we want to limit the number, but include important researchers. Smaller is better now for this stage of the build

- Try to disambiguate / entity resolve at this stage so we don't collect the same things twice for the same people

- For selected researchers, GH contributors and the leaders of the AI labs, search for their blogs and X pages

- Scrape last 3 months of these researchers, GH contributors and leaders and manually examine results + get Fable to give you an overview of what's in there. When saving this data, these people should always be linked back to the lab they come from.

- We need a way to weight these people... however, for now it may just be fine give them a uniform weight which is to be calibrated later.

- Figure out a way to disambiguate articles - it's likely that a researcher and lab might post on something similar and then recording the effect of both of these articles in the frontend just increases the flood of information, giving an unneccessary weight to something by having multiple (potentially relevant) sources related to it. Disambiguation could be done by embedding all the articles and then doing the semantic similarity between them. For articles with a sufficiently high semantic similarity (this threshold would need to be tweaked) we can then pass these to a language model to check if the articles are the same. A precondition for this check is that the person who posted the article / X post would need to be linked to that lab.

  *Notes added by Claude, 2026-09-04 — from hitting a live instance of this while adding
  OpenAI's second and third discovery channels (docs/decisions.md D46, D47):*

  - *A live case exists now. GPT-6 Astra arrives on two channels as two URLs — its model
    spec page and the forum announcement. Not yet ingested, so the duplicate has not
    reached the digest, but it will once the forum channel's 16 items are scored.*

  - *Consider a free exact pass before the embedding pass. `canonical_url` is already
    populated on forum items (10 of 16), so two sources pointing at the same canonical
    page is an exact match costing nothing. Model launches also carry a hard identifier —
    the model id appears in every URL for the event. Running those first means embeddings
    and the LLM adjudication are only spent on the genuinely ambiguous remainder. This is
    an ordering refinement to the approach above, not an alternative to it: the exact pass
    cannot see a researcher and a lab describing the same thing in different words, which
    is the case that motivated the bullet.*

  - *The precondition may be too narrow. "The person who posted would need to be linked to
    that lab" would not fire on the Astra case: both items are lab-published, no person
    involved. "Same lab" rather than "same person's lab" would cover both.*

  - *Embeddings imply a second provider. Anthropic has no embeddings API, so this step
    needs one — the OpenAI client already in research/announcements/providers.py is the
    path of least resistance, and slots into the existing per-call cost instrumentation
    rather than needing a new one.*

- We need an assessment of the effort required to include a new lab in the pipeline. As far as I can tell, for each lab, we need a specific protocol for interacting with their data and finding a) their articles, b) their research papers and c) their github account. Once these have been found, we have a pipeline that is parameterised by this data. 
