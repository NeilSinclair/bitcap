"""Emit a blind copy of the gold sample for independent human tagging.

Two folders, deliberately:

- ``gold/``       system tags, pre-filled, for review
- ``gold_human/`` the same articles with nothing filled in

The pre-filled folder is anchored — a reviewer accepts a plausible wrong tag
more readily than they invent a right one — so it cannot serve as ground truth
for the system that produced it. This folder can, because nothing here reveals
what the system thought.

Article ids are copied from ``gold/`` rather than re-sampled, so file 07 is the
same article in both folders and the two can be compared one to one.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_gold_set import reference  # noqa: E402

HERE = Path(__file__).parent
SOURCE = HERE / "gold" / "articles"
OUT = HERE / "gold_human"


def existing_work() -> int:
    """Count human files that already carry tags.

    Returns:
        Number of files whose `gold` block is non-empty.
    """
    filled = 0
    for path in sorted((OUT / "articles").glob("*.json")):
        gold = json.loads(path.read_text()).get("gold", {})
        if gold.get("event_type") or gold.get("mechanisms"):
            filled += 1
    return filled


def main() -> None:
    """Write the blind folder, refusing to destroy tagging already done."""
    force = "--force" in sys.argv

    if (OUT / "articles").exists():
        filled = existing_work()
        if filled and not force:
            sys.exit(
                f"refusing to rebuild: {filled} files already carry your tags.\n"
                "  --force to discard them."
            )

    files = sorted(SOURCE.glob("*.json"))
    if not files:
        sys.exit(f"no articles in {SOURCE}; build the gold set first")

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "articles").mkdir(parents=True)

    for path in files:
        record = json.loads(path.read_text())
        # Everything the system produced is stripped: tags, score, summary,
        # agreement flags. Only the article and its provenance survive.
        blind = {
            "id": record["id"],
            "lab": record["lab"],
            "date": record["date"],
            "title": record["title"],
            "url": record["url"],
            "text_source": record["text_source"],
            "text": record["text"],
            "gold": {
                "event_type": "",
                "mechanisms": [],
                "categories": [],
                "notes": "",
            },
        }
        (OUT / "articles" / path.name).write_text(json.dumps(blind, indent=2))

    (OUT / "README.md").write_text(
        f"""# Gold set — independent human tagging

{len(files)} announcements in `articles/`, the **same articles and the same
numbering** as `../gold/`, with nothing filled in. File `07` here is file `07`
there.

**Do not look at `../gold/` before tagging.** That folder holds the system's own
answers. The whole value of this folder is that it was produced without seeing
them — it is the only thing in the project that can serve as ground truth for
the classifier, and one glance at the other folder removes that.

## Filling one in

```json
"gold": {{
  "event_type": "compute_commitment",
  "mechanisms": [
    {{
      "id": "training_compute_up",
      "sign": "positive",
      "magnitude": "high",
      "confidence": "high",
      "quote": "verbatim sentence from the text that supports this"
    }}
  ],
  "categories": [
    {{
      "id": "ai_compute_hosting",
      "sign": "positive",
      "confidence": "medium",
      "quote": "verbatim sentence"
    }}
  ],
  "notes": "anything that was hard to call"
}}
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

{reference()}
"""
    )

    print(f"{len(files)} blank articles -> {OUT}")
    print("  ids match ../gold/ one to one; no system output included")


if __name__ == "__main__":
    main()
