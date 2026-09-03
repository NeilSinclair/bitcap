"""Compare a classification run against the gold set and report agreement.

Read the header of the report before reading its numbers. The reference is
`gold/adjudication.yaml` -- every tag judged against the full article, with a
recorded reason -- not independent human ground truth. The adjudicator is a
different model from the classifier and reads the whole document, which makes
this cross-model review rather than a model grading itself; it does not make it
external.

One bias must be stated wherever these numbers are: **the adjudication was built
from a run, so that run scores against it optimistically.** The figures are a
baseline for the *next* run, not a report card on the one that produced them.

Usage:
    python research/announcements/gold_metrics.py research/test_results/<run>.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
GOLD = Path(__file__).parent / "test" / "articles"


def pearson(a: list[float], b: list[float]) -> float:
    """Pearson correlation of two equal-length series.

    Args:
        a: First series.
        b: Second series.

    Returns:
        Correlation in [-1, 1], or 0.0 if either series is constant.
    """
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if not va or not vb:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb)


def ranks(values: list[float]) -> list[float]:
    """Rank a series, averaging ties.

    Args:
        values: Series to rank.

    Returns:
        Ranks in the original order.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = mean
        i = j + 1
    return out


def kappa(a: list[str], b: list[str]) -> float:
    """Cohen's kappa: agreement corrected for what chance would give.

    Args:
        a: Reference labels.
        b: Run labels.

    Returns:
        Kappa. 1.0 is perfect; 0.0 is chance.
    """
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(a) | set(b)) / (n * n)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def prf(reference: set, run: set) -> tuple[float, float, float]:
    """Precision, recall and F1 of a predicted set against a reference set.

    Args:
        reference: Reference items.
        run: Predicted items.

    Returns:
        Tuple of (precision, recall, F1). A pair of empty sets scores 1.0.
    """
    if not reference and not run:
        return 1.0, 1.0, 1.0
    hit = len(reference & run)
    p = hit / len(run) if run else 0.0
    r = hit / len(reference) if reference else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def axis(pairs: list[tuple[set, set]], name: str) -> dict:
    """Micro and macro agreement for one tag axis.

    Args:
        pairs: (reference tags, run tags) per article.
        name: Axis name for the report.

    Returns:
        Metrics dict.
    """
    hit = sum(len(a & b) for a, b in pairs)
    ref = sum(len(a) for a, _ in pairs)
    got = sum(len(b) for _, b in pairs)
    micro_p = hit / got if got else 0.0
    micro_r = hit / ref if ref else 0.0
    macro = [prf(a, b) for a, b in pairs]
    exact = sum(1 for a, b in pairs if a == b)
    return {
        "axis": name,
        "reference_tags": ref,
        "run_tags": got,
        "matched": hit,
        "micro_precision": micro_p,
        "micro_recall": micro_r,
        "micro_f1": 2 * micro_p * micro_r / (micro_p + micro_r) if micro_p + micro_r else 0.0,
        "macro_f1": sum(m[2] for m in macro) / len(macro),
        "articles_identical": exact,
        "articles": len(pairs),
    }


def main() -> None:
    """Report how a run compares with the gold set."""
    parser = argparse.ArgumentParser()
    parser.add_argument("run", help="path to a run file from run_gold.py")
    args = parser.parse_args()

    run = json.loads(Path(args.run).read_text())
    results = {r["id"]: r for r in run["results"] if "error" in r or "run" in r}
    gold = {json.loads(f.read_text())["id"]: json.loads(f.read_text())
            for f in sorted(GOLD.glob("*.json"))}
    ids = sorted(set(gold) & set(results))

    print(f"run       : {Path(args.run).name}")
    print(f"model     : {run['model']}   prompt: {run['prompt']}")
    print(f"reference : test/articles ({len(gold)} files)")
    print(f"compared  : {len(ids)} articles   cost: ${run.get('usd', 0):.4f}")
    base = run.get("adjudication_base")
    print("\nReference: adjudicated labels (gold/adjudication.yaml), not human truth.")
    if base is None:
        print("NOTE: if the adjudication was derived from this run, these are")
        print("      optimistic -- use them as a baseline for the next run.\n")
    print("=" * 74)

    # --- event type -----------------------------------------------------
    ref_e = [gold[i]["gold"]["event_type"] for i in ids]
    run_e = [results[i]["run"]["event_type"] for i in ids]
    same = sum(x == y for x, y in zip(ref_e, run_e))
    print(f"\nEVENT TYPE   agreement {same}/{len(ids)} = {100*same/len(ids):.0f}%"
          f"   Cohen's kappa {kappa(ref_e, run_e):+.3f}")
    for i, x, y in zip(ids, ref_e, run_e):
        if x != y:
            print(f"   {i}  {x:26} -> {y}")

    # --- tag axes -------------------------------------------------------
    print()
    rows = []
    for name, key in (("mechanisms", "mechanisms"), ("categories", "categories"),
                      ("practices", "practices")):
        pairs = [({t["id"] for t in gold[i]["gold"].get(key, [])},
                  {t["id"] for t in results[i]["run"].get(key, [])}) for i in ids]
        rows.append(axis(pairs, name))
    print(f"{'axis':12}{'ref':>5}{'run':>5}{'hit':>5}{'prec':>7}{'rec':>7}"
          f"{'F1':>7}{'macroF1':>9}{'identical':>11}")
    for m in rows:
        print(f"{m['axis']:12}{m['reference_tags']:5}{m['run_tags']:5}{m['matched']:5}"
              f"{m['micro_precision']:7.2f}{m['micro_recall']:7.2f}{m['micro_f1']:7.2f}"
              f"{m['macro_f1']:9.2f}{m['articles_identical']:8}/{m['articles']}")

    # --- attributes on tags both runs produced --------------------------
    print("\nATTRIBUTE agreement, on tags present in BOTH (the shared subset only)")
    for key, fields in (("mechanisms", ("sign", "magnitude", "confidence")),
                        ("practices", ("action", "impact", "confidence"))):
        for field in fields:
            a, b = [], []
            for i in ids:
                g = {t["id"]: t for t in gold[i]["gold"].get(key, [])}
                r = {t["id"]: t for t in results[i]["run"].get(key, [])}
                for tid in set(g) & set(r):
                    if field in g[tid] and field in r[tid]:
                        a.append(g[tid][field])
                        b.append(r[tid][field])
            if not a:
                continue
            agree = sum(x == y for x, y in zip(a, b))
            print(f"   {key[:4]}.{field:12} {agree:3}/{len(a):3} = {100*agree/len(a):3.0f}%"
                  f"   kappa {kappa(a, b):+.3f}")

    # --- scores ---------------------------------------------------------
    print("\nSCORES")
    import yaml
    sys.path.insert(0, str(Path(__file__).parent))
    import score_announcements as sa
    rules = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())
    # Score the reference from its adjudicated tags. The `system` block records
    # what the pipeline said at pre-fill time and is not the reference any more.
    ref_scores = {i: (sa.score_of(gold[i]["gold"], rules)[0],
                      sa.ai_score_of(gold[i]["gold"], rules)[0]) for i in ids}
    for label, gkey in (("investment", 0), ("AI-team", 1)):
        g = [ref_scores[i][gkey] for i in ids]
        r = [results[i]["score" if gkey == 0 else "ai_score"] for i in ids]
        mae = sum(abs(x - y) for x, y in zip(g, r)) / len(g)
        rmse = math.sqrt(sum((x - y) ** 2 for x, y in zip(g, r)) / len(g))
        exact = sum(1 for x, y in zip(g, r) if abs(x - y) < 1e-9)
        both0 = sum(1 for x, y in zip(g, r) if x == 0 and y == 0)
        sign = sum(1 for x, y in zip(g, r) if (x > 0) == (y > 0))
        print(f"   {label:11} MAE {mae:5.1f}   RMSE {rmse:5.1f}   r {pearson(g, r):+.3f}"
              f"   rho {pearson(ranks(g), ranks(r)):+.3f}")
        print(f"   {'':11} identical {exact}/{len(g)}   agree on zero-vs-nonzero "
              f"{sign}/{len(g)}   both zero {both0}")

    # --- the citation guarantee ----------------------------------------
    dropped = [d for i in ids for d in results[i]["run"].get("dropped_tags", [])]
    quote_fails = [d for d in dropped if "quote not in document" in d]
    tags = sum(len(results[i]["run"].get(k, []))
               for i in ids for k in ("mechanisms", "categories", "practices"))
    print(f"\nCITATION GATE   {tags} tags survived   "
          f"{len(quote_fails)} dropped for an unverifiable quote   "
          f"{len(dropped) - len(quote_fails)} dropped for an unknown id or over-cap")
    for d in Counter(dropped).most_common(8):
        print(f"   {d[1]}x  {d[0]}")

    # Absent entirely means the run predates the counter, which is not the same
    # as zero repairs -- reporting 0 either way would be a quiet lie.
    tagged = [t for i in ids for k in ("mechanisms", "categories", "practices")
              for t in results[i]["run"].get(k, [])]
    if any("quote_repaired" in t for t in tagged):
        repaired = sum(1 for t in tagged if t.get("quote_repaired"))
        print(f"                {repaired} quotes snapped to the document's wording")
    else:
        print("                snap count not recorded for this run")

    dims = [len(t.get("dimensions", [])) for i in ids
            for t in results[i]["run"].get("practices", [])
            if t["id"] == "model_capability"]
    if dims:
        print(f"\nDIMENSION CAP   {len(dims)} model_capability tags, "
              f"counts {dict(sorted(Counter(dims).items()))}, max {max(dims)}")
    acts = Counter(t["action"] for i in ids
                   for t in results[i]["run"].get("practices", []))
    print(f"ACTION MIX      adopt {acts['adopt']}  investigate {acts['investigate']}  "
          f"watch {acts['watch']}   (the prompt says watch should lead)")


if __name__ == "__main__":
    main()
