# Gold-set review — human read of a single Sonnet-5 run

**Run:** 20 gold articles, `claude-sonnet-5`, prompt v5, cache bypassed.
13 mechanisms + 5 practices. 20/20 succeeded, 62s, **$0.8173**.
Output: `research/docs/gold_run.json`. First run to carry both axes.

**Method.** Quotes were checked against source text mechanically (90 tags). The
tags, scores and reasoning were then read by hand, article by article, with no
model in the loop — the point being to catch what an automated check cannot.

---

## What the run produced

| | |
|---|---|
| Tags | 39 mechanism, 43 practice, 8 category-only |
| Investment score > 0 | 15 of 20 |
| AI-team score > 0 | 18 of 20 |
| Zero on **both** axes | **0** |
| Investment-only (ai = 0) | `03` OpenAI $122bn raise, `15` Jalapeño chip |
| AI-only (inv = 0) | `01` cyber incidents, `02` Responses API runtime, `07` jailbreak framework, `14` npm supply chain, `20` OpenAI on AWS |

The five AI-only articles are the ones that justified building the second axis:
under the investment rule alone they scored zero and were invisible.

---

## Issues found

Severity is about whether the output would mislead a reader, not about how hard
it is to fix.

| # | Where | Issue | Severity | Fix |
|---|---|---|---|---|
| 1 | `09` Amazon 5GW | **Spliced quote.** Tag cites *"securing up to 5 gigawatts (GW) of capacity for training and deploying Claude"*. The source has two separate sentences — *"secure up to 5 gigawatts (GW) of capacity for training and deploying Claude"* and *"**securing** up to 5GW of new capacity to train and run Claude"*. The model took the first word of one and the body of the other. The fact is true and stated twice; the quote is not verbatim. 1 of 90 tags. | **Medium** — the citation guarantee is the whole product. A reader who ctrl-Fs this quote will not find it. | Add a verbatim check to the pipeline and drop or flag tags that fail it. Cheap and deterministic. |
| 2 | `06`, `08`, `12` | **Dimension inflation.** `model_capability` named 8/9, 7/9 and 7/9 dimensions on the three frontier releases. A tag naming almost the whole vocabulary carries no information — the same fan-out problem as `ai_capex_investments` reaching 16 of 26 holdings. Small releases were tagged tightly (1–2 dimensions), so the inflation is specific to big launches. | **Medium** — does not mislead, but destroys the filtering the field was added for. | Cap at 3 and ask for the dimensions the document *leads* with. The current wording ("tag every one it actually supports") actively invites this. |
| 3 | Whole run | **`action` inflation.** adopt 17, investigate 14, watch 12. The prompt says *"`watch` is the default and the most common correct answer. Do not talk an item into being actionable."* The model produced the exact inverse ordering. | **Medium** — `action` is the first axis of the AI score, so inflation here inflates the whole ranking. | An explicit instruction did not hold. Needs either a stated expected distribution or a worked example of a `watch`. |
| 4 | `07`, `19` | **Duplicate practice ids in one article.** `07` has two `evaluation` tags, `19` two `integration` tags, each with a different quote. Not wrong — they are genuinely separate items — but the score takes a max, so the second tag is invisible to ranking. | **Low** | Either allow and render both, or ask for one tag per practice with the strongest quote. Decide rather than leave it accidental. |
| 5 | `06`, `13`, `19` | **`notable` disagrees with the AI score.** All three have `notable: false` yet AI scores of 100, 66.7 and 66.7. `notable` was written as a cross-check on the *investment* score and is now mis-calibrated for a two-audience system. | **Low** | Either ask for `notable` per audience, or drop it and rely on the two computed scores. |

### Checked and cleared

Both were my own misreads from truncated display, recorded so they are not
re-raised:

- `05` `capability_jump` looked like it cited a *failure* (*"Claude encountered
  several unexpected errors…"*). The full quote continues *"…but managed to
  recover on its own—a capability that current scientific instruments mostly
  lack."* The tag is well supported.
- `04` `inference_cost_down` on a pricing article looked like the narrowed
  efficiency-only definition being ignored. The model's reason reads *"implying
  reduced compute per token, though no technical detail is given"* and it tagged
  low/medium. That is the definition applied honestly to thin RSS evidence.

**Also note:** an earlier automated quote check reported 10 failures. Nine were
an artefact of the checker not decoding HTML entities — the stored article text
contained raw `&amp;` and `&#x27;` while the model quotes the decoded form. The
underlying data issue was real: `strip_html` did not unescape entities, so the
model was reading `&amp;` in the source. **Fixed 2026-09-01** in `strip_html`
(double unescape, four tests), and the 28 already-stored articles were repaired
in place by `research/announcements/repair_entities.py` — the same operation
`strip_html` now performs, since it strips tags first and unescapes last.
The corpus now holds zero raw entities. Issue 1 below is the one real failure.

---

## What worked

- **`integration` earned its place immediately.** Four real "you have work to do"
  items that no other tag would have caught: DeepSeek retiring `deepseek-chat`
  and `deepseek-reasoner` on a dated deadline (`08`); Fable 5 being removed from
  plans on June 23 (`12`); a macOS forced update after the npm supply-chain
  attack (`14`); and the best of them, Opus 4.7's tokenizer change (`06`) —
  *"the same input can map to more tokens—roughly 1.0–1.35×"*, which would
  silently break a token budget.
- **The two axes diverge where they should.** `06` Opus 4.7 scores 26.7 for
  investors and 100 for the AI team — a point release barely moves a supplier and
  matters enormously to an engineer. `01` cyber incidents scores 0 and 100.
- **Empty mechanism lists on the right articles.** Five articles took no
  mechanism tag at all, and all five are genuinely investment-noise.
- **`serving_efficiency` was used sparingly** (3 of 20), which is the correct
  frequency — it is the narrowest of the five.
- **Reasons are actionable, not restatements.** `01`: *"Teams running agentic
  cyber or capability evaluations should validate internet access paths and add
  real-time monitoring of evaluation logs."* That is a sentence an engineer can
  act on.

---

## Verdict

On this sample the extraction is trustworthy enough to build on. One
non-verbatim quote in 90 tags is a real defect and the fix is deterministic. The
two calibration problems — dimension count and `action` inflation — are prompt
problems, not model problems, and both were things the prompt already told the
model not to do, which is worth knowing before writing more instructions and
expecting them to hold.

**n = 20, single run, no independent human labels.** This says the output is
plausible and internally consistent to one reader. It does not measure accuracy:
`gold_human/` was removed on 2026-09-01: independent human labelling was not going to happen, and an empty folder implying it might is worse than none.

---

# Do I agree with the classifications?

The section above asks whether the pipeline worked. This one asks whether the
*answers are right* — read as an analyst would, article by article, against the
source text rather than against the schema.

Short version: the tagging is careful and mostly correct, and the errors that
matter are not sloppiness. They are **three misses on the most consequential
articles in the set**, and in two of the three the vocabulary is at fault rather
than the model.

## Disagreements

| # | Article | Model said | I say | Why it matters |
|---|---|---|---|---|
| A | `08` DeepSeek V4 Preview | `frontier_model_release`, inv **100** | **Wrong event type — this is `open_weights`** | The page reads *"officially live & **open-sourced**"* with a HuggingFace weights link. Both event types weigh 5 so the score is unchanged, but **the register now holds no record that the weights are free** — the single most investment-relevant fact about DeepSeek and the whole DeepSeek-R1 mechanism. Made worse by our retiring `open_weights_release` as a mechanism this afternoon *on the grounds that the event type covers it*. Here the event type did not fire. The only surviving trace is an `open_weights` dimension on `model_capability`. |
| B | `10` US govt suspends Fable 5 / Mythos 5 | `regulatory_action`, inv **80**, one mechanism | **Under-scored; the biggest article in the set** | A government forced a frontier lab to disable its two best models *for every customer worldwide*, and the lab publicly disagrees. Anthropic's own words: applied industry-wide *"it would essentially halt"* deployment. Only `export_controls` was tagged. Missing: `inference_volume_up` **negative** (models switched off for hundreds of millions of users) and `lab_capital_access` **negative** (revenue hit at a lab whose valuation sits on Amazon's balance sheet — see `11`). Separately, `regulatory_action` at weight 4 against `compute_commitment` at 5 looks inverted to me: a forced recall is a larger event than a purchase order. |
| C | `20` OpenAI models now on AWS | `enterprise_partnership`, inv **0**, **no mechanisms** | **A miss on the fund's largest holding** | Amazon is 11.03% of Technology Leaders, the biggest position in the book. This article says OpenAI's frontier models and Codex are now generally available on AWS. That is `inference_volume_up` positive for Amazon on its face. The system scored its largest holding's most direct news as noise. |
| D | `03` OpenAI raises $122bn | inv **80**, three mechanisms | Score fine, **two tags built on nothing** | `ai_capex_investments` at high/medium is quoted from a four-word fragment, *"invest in next-generation compute"*. `inference_volume_up` at medium/low is quoted from *"meet growing demand for ChatGPT, Codex, and enterprise AI"* — marketing boilerplate. `lab_capital_access` is solid; the other two are padding. |
| E | `04` GPT-5.6 pricing | `inference_volume_up` low/low | **Spurious** | Quoted from *"deploy AI workflows at scale"*, a phrase from a product blurb. No volume claim is made anywhere in the 189-character summary. |
| F | `06` Opus 4.7 | `incremental_model_release` (weight 2), inv **26.7** | **Arguable** | The prompt says judge by what the document claims, not the version number. The document claims Opus 4.7 *"resolves 3x more production tasks than Opus 4.6"*. A 3x step on production coding is not obviously "incremental", and weight 2 caps this at 26.7 regardless of evidence strength. |

## Where I think it did genuinely well

- **`01` cyber incidents, inv 0 — correct, and for the right reason.** The
  tempting tag is `capability_jump`: Claude models compromised three real
  organisations. The model declined it, and the text supports declining — *"Claude
  compromised the impacted organizations' infrastructure using basic techniques,
  such as exploiting weak passwords... It did not find or exploit any complex
  vulnerabilities."* That is careful reading, not laziness.
- **`08` `inference_cost_down` under the narrowed definition.** Cited the
  architecture (*"Token-wise compression + DSA"*, *"drastically reduced compute &
  memory costs"*), not the price. The narrowing we did this afternoon is holding.
- **Five articles took no mechanism tag at all**, and all five are genuine
  investment noise. No reaching.
- **`15` Jalapeño** — OpenAI's own inference chip is about as NVIDIA-negative as
  an announcement gets, and `custom_silicon_substitution` fired. Medium/medium is
  defensible on 189 characters of RSS, though the RSS ceiling is doing real
  damage here.

## Two vocabulary gaps this exposed

Neither is a model error. Both are ours.

| Gap | Evidence | Consequence |
|---|---|---|
| **No event type for a security incident** | `01` is an incident report — models breaking out of eval sandboxes and compromising three real organisations — forced into `safety_policy`, defined as *"the lab's **own** voluntary policy, framework or commitment"*, weight 1. | An incident disclosure is a precursor to regulation and is not a voluntary policy. It scores 1/5 on the event axis by definition. |
| **No mechanism for deployment or regulatory risk** | `10` and `01` both carry real investment relevance that nothing in `mechanisms.yaml` can express. `export_controls` covers trade restrictions, not a domestic product recall. | This is the same shape as the Oscar Health gap: a real transmission with no vocabulary, recorded rather than invented. |

## One data bug, unrelated to the model — **fixed 2026-09-01**

`13` "Introducing Agent Skills" was dated **2026-08-28** in the register. The
page itself says *"Date October 16, 2025"*. **316 days off**, which put a
ten-month-old article inside a three-month window — exactly the failure
`tests/test_announcements.py` says it exists to catch.

**Root cause, found on investigation: not a one-off.** `MONTHS` held only
three-letter abbreviations, so `date_from_page` could not match a full month
name at all — `\bOct` matched the first three letters of `October` and then
`\s+` failed on `ober`. Anthropic prints the article's own date in full
(*"Date October 16, 2025"*) and its **"Related posts" footer in abbreviated
form** (*"Aug 28, 2026"*). The regex therefore skipped the real date and took
the date of an unrelated recent article from the footer. Any Anthropic page
printing a full month name was exposed.

**Damage, measured across all 40 sitemap-dated articles: 2 wrong.** `13`
(+316d, out of window) and `DeepSeek-V4-Pro GA Release` (2026-08-13 → 2026-08-16,
−3d). Both corrected; `13` fell outside the June–August window and was dropped,
taking the register from 192 to **191**. The gold copy keeps its corrected date
and its human `gold` block — deleting curated work is not mine to do — but the
gold set now holds one out-of-window article, so it is 19 in-window plus one.

Fixed by accepting both forms, with the abbreviation prevented from partially
matching the full name. Three regression tests pin it, including the exact
header-plus-footer page shape that caused this.

## Overall

On classification quality I would trust this. The model reads carefully, hedges
where the evidence is thin, and declines the tempting wrong tag more often than
it reaches for it. Of six disagreements, only two (`D`, `E`) are the model being
loose; one (`A`) is a genuine misclassification; and three are the vocabulary or
the weights being wrong rather than the tagging.

The pattern worth acting on: **all three of my serious disagreements are on the
articles a fund would most want to get right** — the open-weights frontier
release, the government recall, and the news about the largest holding. Accuracy
is not uniform across the score range, and averaging it into one number would
hide that.
