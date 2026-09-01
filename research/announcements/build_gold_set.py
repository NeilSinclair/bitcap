"""Emit an unlabelled sample for human gold tagging.

Writes one JSON file per article with the text the model was given and an empty
`gold` block to fill in. The system's own classification is deliberately absent
from every file, and the files are shuffled and numbered so nothing about the
sample order or filename hints at what the system thought.

Selection is stratified rather than random: a random draw from the corpus would
be roughly three quarters noise and waste most of the labelling effort. The
consequence is recorded in the README -- this sample supports per-item agreement
and error analysis, not corpus-level precision and recall.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"
SCORED = DOCS / "scored_announcements_v2.json"
OUT = Path(__file__).parent / "gold"

SEED = 20260901  # fixed so the sample is reproducible

# Per-lab quotas, not a proportional draw. OpenAI publishes 4x the volume of the
# other two and almost all of it reaches us as a short RSS summary, so a
# proportional sample was 15/20 OpenAI abstracts -- thin material to label and a
# poor test of the full-text path. These quotas trade representativeness for
# coverage, which is the same trade the signal/noise split already makes.
SIGNAL_QUOTA = {"anthropic": 6, "openai": 7, "deepseek": 2}
NOISE_QUOTA = {"anthropic": 3, "openai": 2}

# Event types to spread each lab's share across, most important first.
PRIORITY = [
    "compute_commitment",
    "corporate_finance",
    "regulatory_action",
    "frontier_model_release",
    "open_weights",
    "pricing_change",
    "incremental_model_release",
    "capability_result",
    "developer_tooling",
    "research_result",
    "product_launch",
    "enterprise_partnership",
    "safety_policy",
    "personnel",
]


def take_spread(pool: list[dict], quota: int, rng: random.Random) -> list[dict]:
    """Take `quota` items from a pool, spreading across event types.

    Args:
        pool: Candidate articles for one lab.
        quota: How many to take.
        rng: Seeded RNG.

    Returns:
        The chosen articles.
    """
    by_event: dict[str, list[dict]] = {}
    for a in pool:
        by_event.setdefault(a["event_type"], []).append(a)
    for bucket in by_event.values():
        # Longer text first: a 137-character summary is poor labelling material.
        bucket.sort(key=lambda a: -len(a["text"]))

    chosen: list[dict] = []
    while len(chosen) < quota:
        added = False
        for event in PRIORITY + sorted(set(by_event) - set(PRIORITY)):
            if len(chosen) >= quota:
                break
            bucket = by_event.get(event)
            if bucket:
                chosen.append(bucket.pop(0))
                added = True
        if not added:
            break
    return chosen


def pick(scored: list[dict], rng: random.Random) -> list[dict]:
    """Choose a stratified sample of scoring and non-scoring articles.

    Stratifies on lab first, then event type, so the sample covers both the
    full-text and RSS-summary paths and does not collapse onto whichever lab
    publishes most.

    Args:
        scored: The scored register.
        rng: Seeded RNG.

    Returns:
        The selected articles, unshuffled.
    """
    chosen: list[dict] = []

    for lab, quota in SIGNAL_QUOTA.items():
        pool = [a for a in scored if a["score"] > 0 and a["lab"] == lab]
        chosen += take_spread(pool, quota, rng)

    for lab, quota in NOISE_QUOTA.items():
        pool = [a for a in scored if a["score"] == 0 and a["lab"] == lab]
        rng.shuffle(pool)
        pool.sort(key=lambda a: -len(a["text"]))
        chosen += pool[:quota]

    return chosen


def reference() -> str:
    """Render the tagging vocabulary from config, so it is never duplicated.

    Returns:
        Markdown reference block.
    """
    mech = yaml.safe_load((ROOT / "config" / "mechanisms.yaml").read_text())["mechanisms"]
    cats = yaml.safe_load((ROOT / "config" / "categories.yaml").read_text())["categories"]
    scoring = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())

    lines = ["### Event types (choose exactly one)\n"]
    for event in scoring["event_weight"]:
        lines.append(f"- `{event}`")
    lines.append("\n### Mechanisms\n")
    for m in mech:
        lines.append(f"- `{m['id']}` — {m['label']}. {' '.join(m['description'].split())}")
    lines.append("\n### Categories\n")
    for c in cats:
        if c.get("lab_signal_routable", True):
            lines.append(f"- `{c['id']}` — {c['label']}. {' '.join(c['definition'].split())}")
    return "\n".join(lines)


def write_readme(selected: list[dict]) -> None:
    """Write the folder README.

    Args:
        selected: The sampled articles, used only for the count.
    """
    (OUT / "README.md").write_text(
        f"""# Gold set — human review

{len(selected)} announcements in `articles/`, one file each. The `gold` block in
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

{reference()}
"""
    )



def existing_work() -> int:
    """Count gold blocks that already carry tags.

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
    """Write the gold-set folder, refusing to destroy existing tags."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--readme-only",
        action="store_true",
        help="regenerate README.md and leave the articles untouched",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild articles even though they already carry tags",
    )
    args = parser.parse_args()

    scored = json.loads(SCORED.read_text())["scored"]
    rng = random.Random(SEED)

    selected = pick(scored, rng)
    rng.shuffle(selected)  # so file number reveals nothing

    # A plain rebuild wipes the articles. That destroyed a prefilled set once;
    # tagging effort, human or paid, must not be silently deleted by a script
    # run to refresh the README.
    if not args.readme_only and (OUT / "articles").exists():
        filled = existing_work()
        if filled and not args.force:
            sys.exit(
                f"refusing to rebuild: {filled} files already carry tags.\n"
                "  --readme-only to refresh the README, or --force to discard them."
            )

    if args.readme_only:
        if not OUT.exists():
            sys.exit("nothing to refresh; run without --readme-only first")
        write_readme(selected)
        print(f"README refreshed, {len(list((OUT / 'articles').glob('*.json')))} articles untouched")
        return

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "articles").mkdir(parents=True)

    for i, article in enumerate(selected, 1):
        record = {
            "id": f"{i:02d}",
            "lab": article["lab"],
            "date": article["date"],
            "title": article["title"],
            "url": article["url"],
            "text_source": article["text_source"],
            "text": article["text"],
            "gold": {
                "event_type": "",
                "mechanisms": [],
                "categories": [],
                "notes": "",
            },
        }
        (OUT / "articles" / f"{i:02d}.json").write_text(json.dumps(record, indent=2))

    write_readme(selected)

    print(f"{len(selected)} articles -> {OUT}")
    print(f"  seed {SEED}, one file per article, tags withheld")


if __name__ == "__main__":
    main()
