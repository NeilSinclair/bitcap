# OpenAI credit-section extraction — v1

**Task.** Extract the credited contributors of one OpenAI paper from its arXiv HTML.
**Used by.** `research/eval_openai.py`
**Evaluated against.** `research/openai_harvest.py` (deterministic).

**Untuned.** Written before seeing results.

---

## System

You extract contributor credits from research-paper HTML. You return only what the
document states. You never infer a person or a role the paper does not print.

## User

Below is HTML from an OpenAI paper. These papers carry a credit section — headed
"Authorship, credit attribution, and acknowledgments", "Contributors" or similar — in
which people are listed under headings naming a team, a role, or both.

Return:

- `authors` — every person credited in that section, in the order printed.
  - `name` — the person's name, with any footnote marker or asterisk removed. Keep a
    parenthetical nickname if the paper prints one.
  - `role` — the heading this name appears under, verbatim, with footnote digits
    removed (e.g. "Core contributors", "Pre-training leads"). Where a team heading and
    a role heading both apply, use the nearest heading above the name. `null` if the
    name sits under no heading.
- `order_meaningful` — false if the paper states the listing is alphabetical or
  otherwise not a credit ordering; true otherwise.
- `no_credit_section` — true if this HTML contains no such section.

Rules:

1. Only people. Section headings, team names, product names and topic labels
   ("Cybersecurity", "Persuasion", "Pricing") are not people, even where they sit in
   the same list.
2. Prose describing who did what is not a name list.
3. Take headings verbatim; do not map them onto a vocabulary of your own.
4. If no credit section is present, set `no_credit_section` and return an empty list.

HTML:

```
{html}
```
