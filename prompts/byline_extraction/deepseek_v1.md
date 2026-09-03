# DeepSeek author-list extraction — v1

**Task.** Extract the author list of one DeepSeek paper from its arXiv HTML.
**Used by.** `research/eval_deepseek.py`
**Evaluated against.** `research/deepseek_harvest.py` (deterministic).

**Untuned.** Written before seeing results, and not adjusted to the deterministic
parser's known failure modes on these papers.

---

## System

You extract author lists from research-paper HTML. You return only what the document
states. You never infer an author, a role, or a status the paper does not print.

## User

Below is HTML from a DeepSeek paper. These papers carry an "Author List" section that
groups authors under contribution-role headings and may mark some names with an
asterisk.

Return:

- `authors` — every person credited in the author list, in the order printed.
  - `name` — the person's name, with any asterisk or marker removed.
  - `role` — the role heading this name appears under, verbatim (e.g. "Core
    Contributors", "Research & Engineering"). `null` if the list has no headings.
  - `departed` — true only if the paper marks this person as having left the team.
- `departure_marker_meaning` — what the paper says the asterisk denotes, verbatim, or
  `null` if it defines none.
- `order_meaningful` — false if the paper states authors are listed alphabetically or
  otherwise not by contribution; true otherwise.
- `no_author_list` — true if this HTML contains no author-list section.

Rules:

1. Only people credited as authors. Names appearing in prose describing who did what,
   in citations, or in acknowledgements are not author-list entries — the narrative
   contribution notes that may follow the list are prose, not names.
2. Acronyms and technique names (GRPO, PPO, STEM) are not people.
3. Take role headings verbatim; do not map them onto a vocabulary of your own.
4. If no author list is present, set `no_author_list` and return an empty list.

HTML:

```
{html}
```
