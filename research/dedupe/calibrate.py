"""Turn the labelled pairs into the two thresholds in `config/dedupe.yaml`.

The headline result is that **cosine alone does not separate the classes**, and
that is the finding, not a failure of the embedding. On the labelled set the
positives run 0.763 to 0.966 and the negatives reach 0.869, so any single cut
either loses true duplicates or merges different articles. Full recall arrives
at precision 0.38; full precision arrives at recall 0.20.

That overlap is the whole argument for a band rather than a threshold:

* at or above ``cosine_high`` nothing in the labelled set is a false merge, so
  merging without asking is safe;
* below ``cosine_low`` no true duplicate survives, so separating without asking
  costs nothing;
* between them the number genuinely does not decide, and an LLM is asked.

Two pairs are excluded before the curve is drawn because they never reach the
threshold: they are the same article at two URLs on the same day, caught free by
the exact pass. They are also the reason the event-type gate cannot be
unconditional — the classifier gave "Introducing Intelligence Age" the labels
`other` and `safety_policy` on two copies of one text.

Caveat, and it belongs in the design doc rather than a footnote: the labelled
set contains **5 positives** once the exact pairs are removed. Every figure
below moves by 0.2 recall if one label is wrong. These thresholds are the best
available estimate, not a precise measurement, and the sample is small because
the corpus genuinely contains few near-duplicates, not because labelling stopped
early.

Usage::

    python research/dedupe/calibrate.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
LABELS = ROOT / "research" / "docs" / "dedupe_labels.json"
FEATURES = ROOT / "research" / "docs" / "dedupe_features.json"

GRID = [0.95, 0.90, 0.89, 0.88, 0.87, 0.86, 0.85, 0.84, 0.83,
        0.82, 0.81, 0.80, 0.79, 0.78, 0.77, 0.76, 0.75, 0.70]


def load() -> tuple[list[dict], dict]:
    """Read the features and the labels.

    Returns:
        Tuple of (feature rows, label by pair id).

    Raises:
        FileNotFoundError: If either file is missing — running the curve on a
            stale or absent set would produce numbers that look calibrated.
    """
    for path in (LABELS, FEATURES):
        if not path.exists():
            raise FileNotFoundError(f"{path} missing; run research/dedupe/candidates.py first")
    features = json.loads(FEATURES.read_text(encoding="utf-8"))
    labels = {row["pair"]: row for row in json.loads(LABELS.read_text(encoding="utf-8"))}
    missing = [f["pair"] for f in features if f["pair"] not in labels]
    if missing:
        raise ValueError(f"{len(missing)} pairs unlabelled, first is {missing[0]}")
    return features, labels


def exact_pairs(features: list[dict], articles: dict) -> set[str]:
    """Identify pairs the exact pass handles, which the curve must exclude.

    Asks `dedupe.exact_groups` — the code that actually runs — rather than
    inferring it from the labels. An earlier version defined these as "event
    types disagree and a labeller said same", which happened to be correct on
    this data and was wrong in principle: being defined in terms of the label,
    it excluded any cross-event-type duplicate whether or not the pipeline could
    merge it. A syndicated copy with a reworded headline would drop out of the
    pool, the real exact pass would miss it (the titles differ), gate 2 would
    refuse it, and the recall check would still report clean.

    Args:
        features: Feature rows.
        articles: Article id to `{lab, published_on, title}`.

    Returns:
        Pair ids the exact pass merges without reaching a threshold.
    """
    from app.pipeline import dedupe

    rows = [{"id": k, **v, "repo": None} for k, v in articles.items()]
    grouped = dedupe.exact_groups(rows)
    return {
        f["pair"] for f in features
        if grouped.get(f["a"]) is not None and grouped.get(f["a"]) == grouped.get(f["b"])
    }


def articles() -> dict:
    """Read the fields the exact pass needs, keyed by article id.

    Args:
        None.

    Returns:
        Article id to `{lab, published_on, title}`.
    """
    from app.db import get_engine, get_session, load_env
    from research.dedupe.candidates import rows as read_rows

    load_env()
    return {
        row["id"]: {"lab": row["lab"], "published_on": row["published_on"],
                    "title": row["title"]}
        for row in read_rows(get_session(get_engine()))
    }


def curve(features: list[dict], labels: dict, exact: set[str]) -> list[dict]:
    """Precision and recall at each candidate threshold.

    Computed only over pairs that reach the threshold at all: the exact pass
    has already run, and the event-type gate has already refused the rest.
    Scoring a threshold on pairs it never sees would measure the wrong thing.

    Args:
        features: Feature rows.
        labels: Labels by pair id.
        exact: Pair ids the exact pass already merges.

    Returns:
        One row per threshold.
    """
    pool = [f for f in features if f["pair"] not in exact and f["same_event_type"]]
    positives = [f for f in pool if labels[f["pair"]]["label"] == "same"]
    negatives = [f for f in pool if labels[f["pair"]]["label"] == "different"]

    rows = []
    for threshold in GRID:
        true_positive = sum(1 for f in positives if f["cosine"] >= threshold)
        false_positive = sum(1 for f in negatives if f["cosine"] >= threshold)
        rows.append({
            "threshold": threshold,
            "tp": true_positive,
            "fp": false_positive,
            "fn": len(positives) - true_positive,
            "precision": true_positive / (true_positive + false_positive)
            if true_positive + false_positive else 1.0,
            "recall": true_positive / len(positives) if positives else 0.0,
        })
    return rows


def recommend(rows: list[dict]) -> tuple[float, float]:
    """Pick the band edges from the curve.

    `cosine_high` is the lowest threshold that still admits no false merge —
    the point above which merging unasked is safe. `cosine_low` is the highest
    threshold that still loses no true duplicate — the point below which
    separating unasked is free.

    A false merge deletes a claim from the product; a missed merge leaves a
    visible duplicate row. The two are not symmetric, and that asymmetry is why
    the high edge is chosen on precision and the low edge on recall rather than
    both on some blended score.

    Args:
        rows: Curve from :func:`curve`.

    Returns:
        Tuple of (cosine_high, cosine_low).
    """
    clean = [r for r in rows if r["fp"] == 0 and r["tp"] > 0]
    full = [r for r in rows if r["fn"] == 0]
    high = min(r["threshold"] for r in clean) if clean else max(GRID)
    low = max(r["threshold"] for r in full) if full else min(GRID)
    return high, low


def main() -> None:
    """Print the curve and the resulting band."""
    features, labels = load()
    skip = exact_pairs(features, articles())
    rows = curve(features, labels, skip)
    high, low = recommend(rows)

    same = sum(1 for f in features if labels[f["pair"]]["label"] == "same")
    checked = sum(1 for row in labels.values() if row.get("labeller") == "neil")
    print(f"labelled pairs   {len(features)}  ({same} same, {len(features) - same} different)")
    print(f"human-checked    {checked}")
    print(f"exact pass takes {len(skip)}  {sorted(skip)}")
    print()
    print(" thresh   tp  fp  fn   precision  recall")
    for row in rows:
        print(f"  {row['threshold']:.2f}   {row['tp']:3d} {row['fp']:3d} {row['fn']:3d}"
              f"      {row['precision']:.2f}    {row['recall']:.2f}")
    print()
    print(f"cosine_high  {high}   merge unasked above this — no false merge in the set")
    print(f"cosine_low   {low}   separate unasked below this — no true duplicate lost")
    print(f"the band     [{low}, {high}) goes to the adjudicator")


if __name__ == "__main__":
    main()
