"""Build the candidate pair set the duplicate thresholds are calibrated on.

Thresholds chosen by eye are not defensible, and a regression test built on
them would enshrine whatever the author guessed. So this dumps the pairs, they
get labelled, and `config/dedupe.yaml` takes its numbers from the labels.

Two files come out, and the split is the point:

* ``dedupe_candidates.json`` — what the labeller sees. Lab, dates, titles,
  summaries and event types. **No cosine, and no cosine order.** Showing a
  labeller the number it is being used to calibrate would make the labels agree
  with the threshold by construction; sorting the file by that number leaks the
  same thing one transform away, which the first version of this script did.
* ``dedupe_features.json`` — cosine, shared identifiers and event-type match,
  keyed by pair id. Joined to the labels afterwards.

Pairs are gated on lab and window only, deliberately **not** on event type.
Gating first would make it impossible to learn whether the event-type gate is
justified; carrying it as a feature means the labels can be asked.

Sampling is stratified across the cosine range rather than uniform. Uniform
sampling of 4,412 pairs returns almost entirely obvious non-duplicates around
0.3 and tells you nothing about where the boundary sits.

Usage::

    python research/dedupe/candidates.py [--sample 100] [--seed 7]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import sqlalchemy as sa

from app.db import get_engine, get_session, load_env
from app.pipeline import dedupe

ROOT = Path(__file__).parent.parent.parent
OUT = ROOT / "research" / "docs"
CANDIDATES = OUT / "dedupe_candidates.json"
FEATURES = OUT / "dedupe_features.json"

# Everything at or above this cosine is labelled — a census, not a sample.
#
# The corpus does not contain enough near-duplicates to sample from. Of 4,412
# candidate pairs, 4,360 sit below 0.70, 42 fall in 0.70-0.80, and exactly 10
# reach 0.80 or higher. Sampling that would mean labelling three of the ten
# pairs the threshold is actually decided by. Labelling all 52 instead is both
# cheaper and stronger: the boundary region is known completely rather than
# estimated.
CENSUS_ABOVE = 0.70

# Negatives drawn from below the census floor. They are not there to place the
# boundary — nothing near it lives down here — but to prove the threshold does
# not collapse: a rule that merges these is broken in a way a boundary-only set
# would never reveal.
SAMPLE_BELOW = 30


def rows(session) -> list[dict]:
    """Read the non-release articles the collapse applies to.

    GitHub releases are excluded: a release train is identified by its repo and
    handled deterministically, so spending embeddings and labels on it would
    calibrate the threshold on 380 rows that never reach this path.

    Args:
        session: Open session.

    Returns:
        One dict per article with the fields the pairing and the labelling need.
    """
    query = (
        sa.select(
            dedupe.m.Article.id, dedupe.m.Article.url, dedupe.m.Article.title,
            dedupe.m.Article.lab, dedupe.m.Article.published_on,
            dedupe.m.Classification.event_type, dedupe.m.Classification.summary,
            dedupe.m.Classification.score,
        )
        .join(
            dedupe.m.Classification,
            dedupe.m.Classification.article_id == dedupe.m.Article.id,
        )
        .where(dedupe.m.Article.text_source != "github_release")
        .order_by(dedupe.m.Article.id)
    )
    return [
        {
            "id": r.id, "url": r.url, "title": r.title or "", "lab": r.lab,
            "published_on": r.published_on, "event_type": r.event_type or "",
            "summary": r.summary or "", "score": r.score,
        }
        for r in session.execute(query)
    ]


def features(session, items: list[dict], window_days: int) -> list[dict]:
    """Score every candidate pair.

    Args:
        session: Open session.
        items: Articles from :func:`rows`.
        window_days: Gate 1's window.

    Returns:
        One dict per pair: ids, cosine, shared identifiers, event-type match.
    """
    urls, block = dedupe.matrix(session, [i["url"] for i in items])
    position = {url: index for index, url in enumerate(urls)}
    subject = {i["id"]: dedupe.subjects(i["title"]) for i in items}

    out = []
    for left, right in dedupe.candidate_pairs(items, window_days):
        a, b = items[left], items[right]
        if a["url"] not in position or b["url"] not in position:
            continue
        cosine = float(block[position[a["url"]]] @ block[position[b["url"]]])
        shared = sorted(subject[a["id"]] & subject[b["id"]])
        out.append({
            "pair": f"{a['id']}-{b['id']}",
            "a": a["id"], "b": b["id"],
            "cosine": round(cosine, 4),
            "shared_subjects": shared,
            "same_event_type": a["event_type"] == b["event_type"],
            "days_apart": abs((b["published_on"] - a["published_on"]).days),
        })
    return out


def sample(pairs: list[dict], below: int, seed: int) -> list[dict]:
    """Take every pair in the decision region, plus negatives from below it.

    Args:
        pairs: Scored pairs.
        below: How many sub-threshold negatives to draw.
        seed: Fixed so the labelled set is reproducible; a set that changes
            under you cannot be a regression baseline.

    Returns:
        Pairs to label, ordered by cosine descending.
    """
    generator = random.Random(seed)
    census = [p for p in pairs if p["cosine"] >= CENSUS_ABOVE]
    rest = [p for p in pairs if p["cosine"] < CENSUS_ABOVE]
    picked = census + generator.sample(rest, min(below, len(rest)))
    # Shuffled, not sorted by cosine. Rank is a monotone transform of the very
    # number the blind file exists to withhold: ordered by similarity, the file
    # tells a labeller reading top to bottom that the last rows are the
    # negatives, which is most of the signal the ordering was hiding.
    generator.shuffle(picked)
    return picked


def main() -> None:
    """Write the blind labelling file and the feature file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--below", type=int, default=SAMPLE_BELOW,
                        help="negatives drawn from under the census floor")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    load_env()
    session = get_session(get_engine())
    config = dedupe.settings()
    items = rows(session)
    by_id = {i["id"]: i for i in items}

    scored = features(session, items, int(config["window_days"]))
    chosen = sample(scored, args.below, args.seed)

    blind = []
    for pair in chosen:
        a, b = by_id[pair["a"]], by_id[pair["b"]]
        blind.append({
            "pair": pair["pair"],
            "lab": a["lab"],
            "a": {"date": str(a["published_on"]), "title": a["title"],
                  "event_type": a["event_type"], "summary": a["summary"]},
            "b": {"date": str(b["published_on"]), "title": b["title"],
                  "event_type": b["event_type"], "summary": b["summary"]},
        })

    CANDIDATES.write_text(json.dumps(blind, indent=2), encoding="utf-8")
    FEATURES.write_text(json.dumps(chosen, indent=2), encoding="utf-8")

    census = sum(1 for p in chosen if p["cosine"] >= CENSUS_ABOVE)
    print(f"articles        {len(items)}")
    print(f"candidate pairs {len(scored)}")
    print(f"to label        {len(chosen)}  "
          f"({census} census >= {CENSUS_ABOVE}, {len(chosen) - census} negatives below)")
    print(f"  {CANDIDATES.relative_to(ROOT)}  (blind, for labelling)")
    print(f"  {FEATURES.relative_to(ROOT)}  (cosine + features)")


if __name__ == "__main__":
    main()
