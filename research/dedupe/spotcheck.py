"""Measure how far the machine-labelled pair set can be trusted.

The thresholds in `config/dedupe.yaml` and the regression floor in
`tests/test_dedupe.py` both rest on `dedupe_labels.json`, which a model wrote.
CLAUDE.md's rule for that case is to say so plainly and use a defensible proxy —
a proxy with a *measured* error rate is defensible, one with an assumed error
rate is not. This is the measurement.

Twenty pairs, stratified across the cosine range so the check covers the
boundary rather than the easy cases, and **blind**: the sheet does not show the
model's label. Seeing it first would make agreement a test of anchoring rather
than of the labels.

Usage::

    python research/dedupe/spotcheck.py            # write the blind sheet
    python research/dedupe/spotcheck.py --score    # after filling it in
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"
CANDIDATES = DOCS / "dedupe_candidates.json"
FEATURES = DOCS / "dedupe_features.json"
LABELS = DOCS / "dedupe_labels.json"
SHEET = DOCS / "dedupe_spotcheck.json"

SIZE = 20
SEED = 11


def build() -> None:
    """Write the blind sheet.

    Stratified by cosine so the sample spans the decision boundary; a uniform
    draw from 82 pairs would return mostly obvious negatives and measure
    agreement on the cases that were never in doubt.

    Low-confidence pairs are included first. The labeller flagged the ones it
    found genuinely hard, and those are where a human check is worth most.
    """
    blind = {row["pair"]: row for row in json.loads(CANDIDATES.read_text(encoding="utf-8"))}
    features = {row["pair"]: row for row in json.loads(FEATURES.read_text(encoding="utf-8"))}
    labels = json.loads(LABELS.read_text(encoding="utf-8"))

    hard = [row["pair"] for row in labels if row.get("confidence") == "low"]
    rest = sorted(
        (p for p in blind if p not in hard),
        key=lambda p: -features[p]["cosine"],
    )
    generator = random.Random(SEED)
    bands = [rest[:10], rest[10:30], rest[30:]]
    picked = list(hard)
    for index, band in enumerate(bands):
        # Distribute the remainder across the bands rather than discarding it,
        # or integer division quietly returns 19 of a requested 20.
        share = SIZE - len(hard)
        want = share // len(bands) + (1 if index < share % len(bands) else 0)
        picked.extend(generator.sample(band, min(max(0, want), len(band))))
    picked = picked[:SIZE]

    sheet = []
    for pair in picked:
        row = blind[pair]
        sheet.append({
            "pair": pair,
            "lab": row["lab"],
            "a": row["a"],
            "b": row["b"],
            # Fill this in with "same" or "different". The model's own label is
            # deliberately not shown.
            "neil": "",
        })
    SHEET.write_text(json.dumps(sheet, indent=2), encoding="utf-8")

    print(f"wrote {SHEET.relative_to(ROOT)} — {len(sheet)} pairs")
    print(f"  {len(hard)} the labeller flagged low-confidence, {len(sheet) - len(hard)} sampled")
    print('  set each "neil" field to "same" or "different", then re-run with --score')


def score() -> None:
    """Report agreement between the human marks and the machine labels."""
    sheet = json.loads(SHEET.read_text(encoding="utf-8"))
    labels = {row["pair"]: row for row in json.loads(LABELS.read_text(encoding="utf-8"))}

    marked = [row for row in sheet if row["neil"] in ("same", "different")]
    if not marked:
        print(f"nothing marked yet in {SHEET.relative_to(ROOT)}")
        return

    agree = [r for r in marked if r["neil"] == labels[r["pair"]]["label"]]
    disagree = [r for r in marked if r["neil"] != labels[r["pair"]]["label"]]

    print(f"marked      {len(marked)} of {len(sheet)}")
    print(f"agreement   {len(agree)}/{len(marked)} = {len(agree) / len(marked):.2f}")
    if disagree:
        print("\ndisagreements — these are the ones worth reading:")
        for row in disagree:
            model = labels[row["pair"]]
            print(f"  {row['pair']}  you: {row['neil']:9s} model: {model['label']}")
            print(f"     model's reason: {model['reason']}")
            print(f"     a: {row['a']['title'][:70]}")
            print(f"     b: {row['b']['title'][:70]}")
    print("\nRecord the agreement rate in docs/decisions.md D57 — it is currently "
          "marked pending, and the eval is unvalidated until it is filled in.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", action="store_true", help="report agreement")
    args = parser.parse_args()
    score() if args.score else build()


if __name__ == "__main__":
    main()
