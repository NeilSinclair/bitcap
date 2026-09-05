"""Grade the `p1` classifier against the paper gold set, once, on demand.

The announcements leg splits this in two -- `run_gold.py` classifies and
`gold_metrics.py` reports -- because its runs are compared against each other
across prompt versions. Papers have one prompt version and ten items, so this is
one script: classify, report, write the run to `research/test_results/` so the
numbers can be re-derived without paying again (`--report <file>`).

**Deliberately not a nightly drift check** (docs/decisions.md D58). The gold set
carries four mechanism tags, and D35 already rejected a sample carrying ten as
unable to separate drift from its own sampling error. Run this by hand when the
`p1` prompt or the classification model changes, and compare against the
baseline recorded in D58.

**Uncached, always.** `classify_one` returns a cached result keyed on the URL,
so grading through the cache would report perfect agreement forever -- the
failure mode that looks exactly like success. This calls `classify` directly,
which means it always costs money (~$0.15 for the ten).

The reference is `research/papers/test/papers/*.json`, labelled by Fable 5
reading the same abstracts. That makes every number here **cross-model
agreement, not accuracy**, and it must be reported as such.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
for _leg in ("announcements", "papers"):
    _path = str(ROOT / "research" / _leg)
    if _path not in sys.path:
        sys.path.insert(0, _path)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLD = Path(__file__).parent / "test" / "papers"
RESULTS = ROOT / "research" / "test_results"

# Never sent to the model. `gold` and `review` are the answer; `document_type`
# is the stratum the sampler assigned, which is a hint about the answer.
NOT_INPUT = ("gold", "review", "system", "document_type")


def load_gold(gold_dir: Path = GOLD) -> list[dict]:
    """Read every labelled paper, in id order.

    Args:
        gold_dir: Directory of gold files.

    Returns:
        The gold records.

    Raises:
        ValueError: If any file is missing `gold.labelled_by`. A set whose
            provenance nobody can state must not reach a metric.
    """
    records = [json.loads(p.read_text(encoding="utf-8"))
               for p in sorted(gold_dir.glob("*.json"))]
    unlabelled = [r["id"] for r in records if not r["gold"].get("labelled_by")]
    if unlabelled:
        raise ValueError(f"gold papers with no labelled_by: {unlabelled}")
    return records


def classify_all(records: list[dict], model: str, workers: int) -> tuple[dict, float, list]:
    """Classify every gold paper under the `p1` variant, bypassing the cache.

    Args:
        records: Gold records from :func:`load_gold`.
        model: Model id.
        workers: Concurrent requests.

    Returns:
        Tuple of (results by id, dollars spent, errors).
    """
    import anthropic
    import yaml

    import score_announcements as sa
    from verbatim import enforce as enforce_quotes

    sa.load_env()
    variant = sa.papers()
    _, _, _, mech_ids, cat_ids, prac_ids = sa.vocabularies()
    dims, cap = sa.practice_dimensions(), sa.dimension_cap()
    rules = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())
    client = anthropic.Anthropic()

    runs, costs, errors = {}, [], []

    def one(record: dict) -> None:
        """Classify one paper and fold its scored result in."""
        article = {k: v for k, v in record.items() if k not in NOT_INPUT}
        try:
            result, cost = sa.classify(client, model, article, variant)
        except Exception as exc:  # noqa: BLE001 — one bad call is not a finding
            errors.append({"id": record["id"], "error": str(exc)})
            return
        result["dropped_tags"] = sa.drop_unknown_tags(
            result, mech_ids, cat_ids, prac_ids, dims, cap)
        result["dropped_tags"] += enforce_quotes(result, article["text"])
        result["score"], result["band"] = sa.score_of(result, rules)
        result["ai_score"], result["ai_band"] = sa.ai_score_of(result, rules)
        runs[record["id"]] = result
        costs.append(cost["usd"])

    # One call serially first, exactly as `score_announcements.run` and
    # `drift.measure` do: the shared system prompt is cached `ephemeral`, and a
    # cold parallel start bills every request as a 1.25x cache write rather
    # than a 0.10x read (docs/decisions.md D42).
    if records:
        one(records[0])
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one, r) for r in records[1:]]):
            future.result()

    return runs, sum(costs), errors


def metrics(records: list[dict], runs: dict) -> dict:
    """Compare a run against the gold labels.

    The headline for papers is **not** mechanism micro-F1, which is the
    announcement metric. Seven of ten gold papers correctly carry no mechanism,
    and `axis` scores an empty-vs-empty pair as no contribution at all, so the
    noise rejection this corpus exists to test is invisible to it. The headline
    is `investment_zero_agreement`: does a safety paper still score zero?

    Args:
        records: Gold records.
        runs: Results by id.

    Returns:
        Metrics dict, and the disagreements as lists rather than a mean.
    """
    import yaml

    import score_announcements as sa
    from gold_metrics import axis, kappa

    rules = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())
    gold = {r["id"]: r for r in records}
    ids = sorted(set(gold) & set(runs))
    if not ids:
        return {"compared": 0}

    ref_e = [gold[i]["gold"]["event_type"] for i in ids]
    run_e = [runs[i].get("event_type") for i in ids]

    axes = {}
    for key in ("mechanisms", "categories", "practices"):
        axes[key] = axis(
            [({t["id"] for t in gold[i]["gold"].get(key, [])},
              {t["id"] for t in runs[i].get(key, [])}) for i in ids], key)

    scores = {}
    for label, fn, run_key in (("investment", sa.score_of, "score"),
                               ("ai_team", sa.ai_score_of, "ai_score")):
        ref = [fn(gold[i]["gold"], rules)[0] for i in ids]
        got = [runs[i][run_key] for i in ids]
        scores[label] = {
            "mae": round(sum(abs(a - b) for a, b in zip(ref, got)) / len(ids), 3),
            "zero_agreement": round(
                sum((a > 0) == (b > 0) for a, b in zip(ref, got)) / len(ids), 4),
            "both_zero": sum(1 for a, b in zip(ref, got) if a == b == 0),
            "identical": sum(1 for a, b in zip(ref, got) if abs(a - b) < 1e-9),
            # Named, never averaged away: a paper the two disagree about being
            # signal at all is the only score disagreement that changes a
            # decision, and the mean hides it.
            "sign_disagreements": [
                {"id": i, "document_type": gold[i]["document_type"],
                 "reference": a, "run": b}
                for i, a, b in zip(ids, ref, got) if (a > 0) != (b > 0)],
        }

    dropped = [d for i in ids for d in runs[i].get("dropped_tags", [])]
    return {
        "compared": len(ids),
        "event_type_agreement": round(
            sum(a == b for a, b in zip(ref_e, run_e)) / len(ids), 4),
        "event_type_kappa": round(kappa(ref_e, run_e), 4),
        "event_type_disagreements": [
            {"id": i, "document_type": gold[i]["document_type"],
             "reference": a, "run": b}
            for i, a, b in zip(ids, ref_e, run_e) if a != b],
        "axes": axes,
        "scores": scores,
        "tags_surviving": sum(len(runs[i].get(k, [])) for i in ids
                              for k in ("mechanisms", "categories", "practices")),
        "quotes_dropped": sum(1 for d in dropped if "quote not in document" in d),
        "other_dropped": sum(1 for d in dropped if "quote not in document" not in d),
    }


def report(result: dict) -> None:
    """Print a run's metrics, provenance header first."""
    print(f"model {result['model']}   prompt {result['prompt']}   "
          f"n={result['metrics']['compared']}   ${result['usd']:.4f}")
    print("reference: research/papers/test/papers, labelled by "
          f"{result.get('labelled_by', 'a model')} reading the same abstracts.")
    print("These are CROSS-MODEL AGREEMENT figures, not accuracy.\n" + "=" * 72)

    m = result["metrics"]
    print(f"\nEVENT TYPE  {m['event_type_agreement']:.0%}   "
          f"kappa {m['event_type_kappa']:+.3f}")
    for d in m["event_type_disagreements"]:
        print(f"   X {d['id']} {d['document_type']:17}{d['reference']:24} -> {d['run']}")

    print(f"\n{'axis':12}{'ref':>5}{'run':>5}{'hit':>5}{'microF1':>9}"
          f"{'macroF1':>9}{'identical':>11}")
    for a in m["axes"].values():
        print(f"{a['axis']:12}{a['reference_tags']:5}{a['run_tags']:5}{a['matched']:5}"
              f"{a['micro_f1']:9.2f}{a['macro_f1']:9.2f}"
              f"{a['articles_identical']:8}/{a['articles']}")

    print("\nSCORES   (the reference is scored from its own labels, same rule)")
    for label, s in m["scores"].items():
        print(f"  {label:11} MAE {s['mae']:5.1f}   zero-vs-nonzero "
              f"{s['zero_agreement']:.0%}   both zero {s['both_zero']}   "
              f"identical {s['identical']}/{m['compared']}")
        for d in s["sign_disagreements"]:
            print(f"      SIGN MISS {d['id']} {d['document_type']:17}"
                  f"reference {d['reference']:5.1f} -> run {d['run']:5.1f}")

    print(f"\nCITATION GATE  {m['tags_surviving']} tags survived, "
          f"{m['quotes_dropped']} dropped for an unverifiable quote, "
          f"{m['other_dropped']} for an unknown id or over-cap")


def main(argv: list[str] | None = None) -> int:
    """Classify the gold papers and report agreement, or re-report a saved run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path,
                        help="re-report a saved run instead of paying for a new one")
    parser.add_argument("--model", default=None, help="defaults to config/pipeline.yaml")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--tag", default="", help="suffix for the output filename")
    args = parser.parse_args(argv)

    if args.report:
        report(json.loads(args.report.read_text(encoding="utf-8")))
        return 0

    from app.pipeline.classify import settings as classify_settings

    configured = classify_settings()
    model = args.model or configured["model"]
    workers = args.workers or int(configured.get("workers", 12))

    import score_announcements as sa

    records = load_gold()
    runs, usd, errors = classify_all(records, model, workers)
    result = {
        "model": model,
        "prompt": sa.PAPER_PROMPT_VERSION,
        "labelled_by": records[0]["gold"]["labelled_by"],
        "at": datetime.now(timezone.utc).isoformat(),
        "usd": round(usd, 6),
        "errors": errors,
        "runs": runs,
        "metrics": metrics(records, runs),
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"paper_gold_{stamp}_{sa.PAPER_PROMPT_VERSION}{args.tag}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    report(result)
    print(f"\nwrote {out.relative_to(ROOT)}")
    if errors:
        print(f"{len(errors)} paper(s) failed to classify: "
              f"{[e['id'] for e in errors]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
