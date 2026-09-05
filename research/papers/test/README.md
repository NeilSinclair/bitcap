# Paper gold set — cross-model reference labels

Ten papers in `papers/`, drawn by
[`build_paper_gold_set.py`](../build_paper_gold_set.py). The `gold` block in
each is the reference the paper scorer is graded against.

## What this is, stated plainly

**Not independent human ground truth**, and neither is the announcements set —
see [`../../announcements/test/README.md`](../../announcements/test/README.md).
`gold_human/` was dropped on 2026-09-01 because human labelling of long
documents was not going to happen, and an empty folder implying otherwise is
worse than none. The same is true here. Say it in the design document rather
than letting a reader assume.

**Cross-model labelling.** The scorer is `claude-sonnet-5` under
[`prompts/paper_scoring/p1.md`](../../../prompts/paper_scoring/p1.md). The
labeller is a different model, recorded per file in `gold.labelled_by` — a
required field, so a file cannot reach the metrics without stating who produced
it. Never average these figures into the announcement ones without naming which
model produced each.

**Labelled blind, which the announcements set was not.** The announcements
adjudicator reads the classifier's output plus a second independent run and then
decides each tag. This labeller never sees the scorer's answer: the files carry
`text`, `title`, `url` and `text_source` and nothing else, and the builder is
tested to keep it that way
(`tests/test_papers_scoring.py::test_the_system_answer_never_reaches_a_gold_file`).

That trade runs both ways, and it is worth being clear about. Blind labelling
cannot anchor on the scorer, so agreement means more. It also loses what
adjudication buys: no second run to show where the scorer was unstable, and no
reason recorded for rejecting a tag the scorer produced. The announcements set
measured its adjudicator's bias against five blind items and found it ran high
on ordered fields in 11 of 15 disagreements; nothing equivalent has been
measured here yet.

**Every quote is mechanically verified** to be an exact substring of the
document before a label is accepted, exactly as on the announcements side, so a
disagreement is only ever about whether a quote *supports* a tag — never about
whether it exists.

## Why it is stratified by document type

By type, not by lab. That is the finding this whole leg rests on: a paper's
value tracks what kind of document it is, and the corpus splits into flagship
technical reports, system and model cards, alignment and interpretability work,
safety and social-science papers, and component research. A lab-stratified draw
would put DeepSeek's technical reports and Mistral's product write-ups in one
bucket and learn nothing from either.

The `safety_social` stratum is deliberately over-sampled. Thirty-three of the
forty-seven scored papers score zero for an investor, almost all of them from
that group, and **"correctly scored zero" is the case that fails silently** — a
scorer that starts finding transmission in a study of how people perceive AI
consciousness buries the technical reports and raises no error doing it.

## Consequence: what this set can and cannot support

n = 10, stratified, one label per document. It supports per-item agreement and
error analysis — which documents the scorer and the labeller disagree on, and
why. It does **not** support corpus-level precision and recall, for the same
reason the announcements sample does not: the strata are not drawn in
proportion to the corpus.
