<!--
The labelling rubric for the repo relevance eval. This is the ground-truth
question; `r1.md` is the production attempt to get a cheap model to reproduce
the same answer.

Two rules govern how this file is written, and both come from D59c:

  1. **It names no repository.** The v2 dedupe rubric carried seven worked
     examples, and seven of the twenty pairs in the human spot-check were those
     same examples — so the headline 0.90 was a training score, and out of
     sample v2 was not better than v1. Category rules only, so every repository
     in the population is out of sample by construction.
  2. **It is judged blind.** The labeller never sees the production filter's
     answer, or agreement measures anchoring rather than agreement.

The line the rubric draws is *topic*, not quality, activity, or newsworthiness.
A repository can be excellent, busy and famous and still be about a different
field. Vendor SDKs are kept deliberately: a client library bump is often the
first public appearance of a new model identifier, and the release-to-launch
pairing added in D61 runs on exactly those releases.
-->

You are classifying a GitHub repository for an intelligence product that tracks
**large language models and the frontier labs that build them**.

Decide whether releases from this repository are on-topic for that product.

You are given the organisation, repository name, star count, primary language,
topics and description. Judge the repository as a whole, not any single release.

## Relevant

Mark `relevant` if the repository's subject is language models or the systems
that build, run, evaluate or connect to them:

- `model` — model weights, model implementations, or a model's reference code.
- `training_or_serving` — training, fine-tuning, inference, serving, quantisation,
  attention or transformer kernels, tokenisers.
- `agent_or_tooling` — agent frameworks, coding agents, prompt tooling, developer
  tools whose subject is a language model.
- `eval_or_safety` — benchmarks, evaluation harnesses, red-teaming, watermarking,
  interpretability, alignment tooling.
- `vendor_sdk` — an official client library, CLI, API definition or worked-example
  collection for a lab's own model API. **Always relevant.** These are thin and routine, but they
  are where a new model identifier first appears in public.
- `ml_infrastructure` — general numerical, array or deep-learning infrastructure
  that language-model work is built on: autodiff and optimiser libraries, neural
  network frameworks, vector search and retrieval, distributed training.

## Off-topic

Mark `off_topic` if the repository's subject is a different field, however good
the work:

- `other_ai_research` — AI or machine learning in another domain: computer
  vision, image or video generation and segmentation, speech and audio, physics
  simulation, robotics, reinforcement-learning environments and agents, game
  playing, protein structure and genomics, materials, weather and climate,
  mathematics and theorem proving.
- `non_ai` — anything not about AI: web infrastructure, marketplaces, data
  processing, corporate or programme pages.

## How to decide the hard cases

**Ask what the repository is *about*, not what it could be used for.** Almost any
numerical library can be used to train a language model. Choose
`ml_infrastructure` only when the repository is general-purpose infrastructure a
language-model stack is genuinely built out of. If the repository is built for a
specific non-language domain, it is `other_ai_research` — even when the technique
is shared.

**Multimodal work follows its centre of gravity.** A model whose subject is
language, and which also accepts images, is relevant. A model whose subject is
images, video or audio is `other_ai_research`, even if it has a text interface.

**A grab-bag repository is judged on its stated purpose.** If the description
says it collects work across many projects and no single subject dominates, it is
`other_ai_research`.

**When the description is empty**, judge from the organisation, the repository
name, the language and the topics. Say so in your reason, and set `confidence`
to `low`.

Set `confidence` to `low` whenever you would not defend the call to a colleague.
Those are the ones a human will check, so flagging them is more useful than
guessing well.

Give `reason` as one sentence naming the evidence you used.
