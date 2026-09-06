"""Measure how far the machine-labelled repository set can be trusted.

Everything the relevance filter claims rests on `repo_relevance_labels.json`,
which a model wrote. CLAUDE.md's rule for that case is to say so plainly and use
a defensible proxy — and a proxy with a *measured* error rate is defensible where
one with an assumed error rate is not. This is the measurement, and it is the
same shape as `research/dedupe/spotcheck.py` for the same reason.

Twenty repositories, and the stratification is the point. A uniform draw from 183
would return mostly obvious calls — a physics simulator, a Python SDK — and
measure agreement on the cases that were never in doubt. Instead, in priority
order:

1. every repository the labeller flagged `confidence: low`
2. every **vendor SDK**, because "keep the SDKs" is the one rule the product
   depends on that a topic filter is most likely to get wrong: an SDK bump is
   thin and routine and looks exactly like noise, and it is also where a new
   model identifier first appears in public (D61's pairing runs on them)
3. every **frontier** repository drawn, because those are the ones a freed slot
   promotes and none of them is in the corpus today
4. a star-stratified sample of the remainder

**Blind**: the sheet does not carry the model's label. Seeing it first would make
this a test of anchoring rather than of the labels.

Usage::

    python research/github/spotcheck_repos.py           # write the blind sheet
    python research/github/spotcheck_repos.py --score   # after filling it in
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"
POPULATION = DOCS / "repo_population.json"
LABELS = DOCS / "repo_relevance_labels.json"
SHEET = DOCS / "repo_relevance_spotcheck.json"

SIZE = 20
SEED = 87


def build() -> None:
    """Write the blind sheet."""
    population = {f"{r['org']}/{r['repo']}": r
                  for r in json.loads(POPULATION.read_text(encoding="utf-8"))}
    labels = json.loads(LABELS.read_text(encoding="utf-8"))

    generator = random.Random(SEED)
    by_repo = {r["repo"]: r for r in labels}

    def stratified(pool: list[str], want: int, taken: set[str]) -> list[str]:
        """Draw `want` from `pool` spread across the star range."""
        pool = sorted((k for k in pool if k not in taken),
                      key=lambda k: -population[k]["stars"])
        if not pool or want <= 0:
            return []
        bands = [pool[:len(pool) // 3 or 1],
                 pool[len(pool) // 3: 2 * len(pool) // 3],
                 pool[2 * len(pool) // 3:]]
        out: list[str] = []
        for index, band in enumerate(bands):
            share = want // len(bands) + (1 if index < want % len(bands) else 0)
            out.extend(generator.sample(band, min(max(0, share), len(band))))
        return out[:want]

    # Quotas, not a priority queue. An earlier version filled the sheet from
    # low-confidence and then vendor SDKs, and since there are 46 SDKs and all
    # of them are labelled `relevant`, every one of the 20 slots went to a
    # repository of a single category and a single class. Twenty marks that are
    # all "relevant" measure agreement on nothing: the cut being checked is
    # relevant-vs-off_topic, so both sides have to be on the sheet.
    picked: list[str] = [r["repo"] for r in labels if r.get("confidence") == "low"]
    hard = list(picked)
    for pool, want in (
        # The rule the product depends on and a topic filter is most likely to
        # break: an SDK bump is thin and routine and looks exactly like noise.
        ([k for k, r in by_repo.items() if r["category"] == "vendor_sdk"], 5),
        # The other half of the cut. Without these the sheet cannot disagree.
        ([k for k, r in by_repo.items() if r["label"] == "off_topic"], 7),
        # Everything else kept, so the easy positives are represented too.
        ([k for k, r in by_repo.items()
          if r["label"] == "relevant" and r["category"] != "vendor_sdk"], SIZE),
    ):
        picked.extend(stratified(pool, min(want, SIZE - len(picked)), set(picked)))
    picked = picked[:SIZE]

    sheet = []
    for key in picked:
        row = population[key]
        sheet.append({
            "repo": key,
            "cohort": row["cohort"],
            "stars": row["stars"],
            "language": row["language"],
            "topics": row["topics"],
            "description": row["description"],
            # Fill this in with "relevant" or "off_topic". The model's own label
            # is deliberately not shown.
            "neil": "",
        })
    SHEET.write_text(json.dumps(sheet, indent=2), encoding="utf-8")

    print(f"wrote {SHEET.relative_to(ROOT)} — {len(sheet)} repositories")
    print(f"  {len(hard)} flagged low-confidence by the labeller")
    print(f"  {sum(1 for s in sheet if by_repo[s['repo']]['category'] == 'vendor_sdk')}"
          " vendor SDKs")
    print(f"  {sum(1 for s in sheet if by_repo[s['repo']]['label'] == 'off_topic')}"
          " labelled off_topic, "
          f"{sum(1 for s in sheet if by_repo[s['repo']]['label'] == 'relevant')} relevant")
    print(f"  {sum(1 for s in sheet if s['cohort'] == 'frontier')} frontier")
    print('  set each "neil" field to "relevant" or "off_topic", then --score')


def score() -> None:
    """Report agreement between the human marks and the machine labels."""
    sheet = json.loads(SHEET.read_text(encoding="utf-8"))
    labels = {r["repo"]: r for r in json.loads(LABELS.read_text(encoding="utf-8"))}

    marked = [r for r in sheet if r["neil"] in ("relevant", "off_topic")]
    if not marked:
        print(f"nothing marked yet in {SHEET.relative_to(ROOT)}")
        return

    agree = [r for r in marked if r["neil"] == labels[r["repo"]]["label"]]
    disagree = [r for r in marked if r["neil"] != labels[r["repo"]]["label"]]

    print(f"marked      {len(marked)} of {len(sheet)}")
    print(f"agreement   {len(agree)}/{len(marked)} = {len(agree) / len(marked):.2f}")

    # Reported separately because it is the rule the product depends on. A high
    # overall number carried by easy calls, with the SDKs wrong, is the failure
    # this whole sheet exists to make visible.
    sdk = [r for r in marked if labels[r["repo"]].get("category") == "vendor_sdk"]
    if sdk:
        ok = sum(1 for r in sdk if r["neil"] == labels[r["repo"]]["label"])
        print(f"  vendor SDKs   {ok}/{len(sdk)}")
    frontier = [r for r in marked if r["cohort"] == "frontier"]
    if frontier:
        ok = sum(1 for r in frontier if r["neil"] == labels[r["repo"]]["label"])
        print(f"  frontier      {ok}/{len(frontier)}")

    if disagree:
        print("\ndisagreements — these are the ones worth reading:")
        for row in disagree:
            model = labels[row["repo"]]
            print(f"  {row['repo']}  you: {row['neil']:10s} model: {model['label']}"
                  f"  ({model['category']}, {model['confidence']})")
            print(f"     model's reason: {model['reason']}")
            print(f"     {row['stars']} stars · {row['description'] or '(no description)'}")
    print("\nRecord the agreement rate in docs/decisions.md D65. The labels are "
          "unvalidated until it is filled in, and every number downstream rests "
          "on them.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", action="store_true", help="report agreement")
    args = parser.parse_args()
    score() if args.score else build()


if __name__ == "__main__":
    main()
