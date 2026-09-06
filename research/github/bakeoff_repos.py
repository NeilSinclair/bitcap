"""Run both candidate models over the labelled population and compare them.

The question is not "which is cheaper". Both candidates are cheap enough that
the whole population costs pennies; what is being bought is a decision the
product cannot see afterwards, because an excluded repository leaves no trace
downstream. So the deciding metric is **recall on `relevant`** — how few real
repositories each model throws away — with precision as the tie-break and price
last. That is the same order `research/summarisation_results.md` settled on when
a 12-17% cost saving was rejected for destroying the axis the pipeline exists
for.

Three things are reported separately rather than averaged, because each hides a
different failure:

* **The vendor-SDK slice.** "Keep the SDKs" is the one rule the product depends
  on, and an SDK is exactly what a topic filter is most likely to discard: thin,
  routine, and the first public appearance of a new model identifier (D61).
  A model at 0.95 overall and 0.70 on SDKs is the wrong model.
* **The frontier cohort.** The gate sits inside the ranking, so a dropped
  repository promotes the next one down — and those promotions are repositories
  the corpus has never contained. Folding them into the headline would report
  accuracy on a population the filter no longer sees.
* **Every disagreement, in full**, plus the repositories where the two
  candidates disagree with *each other*. That second set is worth reading even
  when the labels themselves are uncertain, which they are: they came from a
  model, and `spotcheck_repos.py` is what says how far they can be trusted.

The prompt, the schema and the rendering all come from `app.pipeline.repo_relevance`,
so what is measured here is what production runs. A separate copy would drift and
the eval would quietly stop describing the system.

Usage::

    python research/github/bakeoff_repos.py            # run both, then report
    python research/github/bakeoff_repos.py --report   # re-report, pay nothing
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"
POPULATION = DOCS / "repo_population.json"
LABELS = DOCS / "repo_relevance_labels.json"
RESULTS = DOCS / "repo_relevance_bakeoff.json"
VERDICTS = DOCS / "repo_relevance_verdicts.json"
COST = DOCS / "announcement_cost.json"

sys.path.insert(0, str(ROOT))
for _leg in ("announcements", "github"):
    _path = str(ROOT / "research" / _leg)
    if _path not in sys.path:
        sys.path.insert(0, _path)

CANDIDATES = {
    "haiku-4.5": ("anthropic", "claude-haiku-4-5-20251001"),
    "gpt-5-mini": ("openai", "gpt-5-mini"),
}


def _record_cost(cost: dict) -> None:
    """Append one cost record to the shared ledger, attributed to this workflow."""
    log = json.loads(COST.read_text(encoding="utf-8")) if COST.exists() else []
    log.append({**cost, "workflow": "repo_relevance_bakeoff"})
    COST.write_text(json.dumps(log, indent=2), encoding="utf-8")


def run() -> dict:
    """Judge every repository with every candidate.

    Returns:
        `{model_name: {repo: {relevant, reason, usd, seconds}}}`, resumable —
        anything already in the results file is kept rather than re-bought.
    """
    import providers

    from app.pipeline import repo_relevance

    providers.load_env()
    population = json.loads(POPULATION.read_text(encoding="utf-8"))
    results = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}

    for name, (provider, model) in CANDIDATES.items():
        config = {"provider": provider, "model": model, "prompt_version": "r1"}
        system = repo_relevance.prompt(config)
        done = results.setdefault(name, {})
        for row in population:
            key = f"{row['org']}/{row['repo']}"
            if key in done:
                continue
            started = time.time()
            try:
                result, cost = providers.classify(
                    provider, model, system, repo_relevance.describe(row),
                    repo_relevance.SCHEMA, f"repo:{key}")
            except Exception as exc:
                # Recorded, never dropped. A model that fails on 5% of the
                # population is a different proposition from one that does not,
                # and averaging the failures away would hide it.
                done[key] = {"error": str(exc), "seconds": round(time.time() - started, 2)}
                RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
                continue
            _record_cost(cost)
            done[key] = {
                "relevant": result.get("relevant"),
                "reason": result.get("reason", ""),
                "usd": cost["usd"],
                "seconds": round(time.time() - started, 2),
            }
            RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"{name}: {len(done)}/{len(population)} judged")

    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def _confusion(rows: list[tuple[str, bool, str]]) -> dict:
    """Counts for one slice.

    Args:
        rows: `(repo, model_said_relevant, gold_label)` triples.

    Returns:
        Confusion counts plus recall and precision on `relevant`.
    """
    tp = [r for r, said, gold in rows if said and gold == "relevant"]
    fn = [r for r, said, gold in rows if not said and gold == "relevant"]
    fp = [r for r, said, gold in rows if said and gold == "off_topic"]
    tn = [r for r, said, gold in rows if not said and gold == "off_topic"]
    return {
        "kept_relevant": len(tp), "dropped_relevant": len(fn),
        "kept_off_topic": len(fp), "dropped_off_topic": len(tn),
        "recall": len(tp) / (len(tp) + len(fn)) if (tp or fn) else None,
        "precision": len(tp) / (len(tp) + len(fp)) if (tp or fp) else None,
        "missed": sorted(fn),
    }


def report() -> None:
    """Print the comparison."""
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    labels = {r["repo"]: r for r in json.loads(LABELS.read_text(encoding="utf-8"))}
    population = {f"{r['org']}/{r['repo']}": r
                  for r in json.loads(POPULATION.read_text(encoding="utf-8"))}

    for name, verdicts in results.items():
        answered = {k: v for k, v in verdicts.items() if "relevant" in v}
        errors = len(verdicts) - len(answered)
        rows = [(k, bool(v["relevant"]), labels[k]["label"]) for k, v in answered.items()]

        print(f"\n{'=' * 72}\n{name}   {len(answered)} answered, {errors} failed")
        overall = _confusion(rows)
        print(f"  recall(relevant)    {overall['recall']:.3f}"
              f"   precision {overall['precision']:.3f}")
        print(f"  kept {overall['kept_relevant']} relevant, "
              f"{overall['kept_off_topic']} off-topic; "
              f"dropped {overall['dropped_relevant']} relevant, "
              f"{overall['dropped_off_topic']} off-topic")

        for slice_name, subset in (
            ("watched", [r for r in rows if population[r[0]]["cohort"] == "watched"]),
            ("frontier", [r for r in rows if population[r[0]]["cohort"] == "frontier"]),
            ("vendor_sdk", [r for r in rows if labels[r[0]]["category"] == "vendor_sdk"]),
        ):
            if not subset:
                continue
            s = _confusion(subset)
            recall = f"{s['recall']:.3f}" if s["recall"] is not None else "n/a"
            print(f"  {slice_name:<12} n={len(subset):<4} recall {recall}"
                  f"   dropped-relevant {s['dropped_relevant']}")

        spend = sum(v.get("usd", 0.0) for v in answered.values())
        times = sorted(v["seconds"] for v in answered.values())
        print(f"  ${spend:.4f} total, median {times[len(times) // 2]:.1f}s/call")

        if overall["missed"]:
            print(f"\n  relevant repositories this model would drop "
                  f"({len(overall['missed'])}):")
            for key in overall["missed"]:
                print(f"    {key:<45} {labels[key]['category']}")
                print(f"       said: {answered[key]['reason'][:88]}")

    if len(results) == 2:
        left, right = list(results)
        both = [k for k in results[left]
                if "relevant" in results[left].get(k, {})
                and "relevant" in results[right].get(k, {})]
        split = [k for k in both
                 if results[left][k]["relevant"] != results[right][k]["relevant"]]
        print(f"\n{'=' * 72}")
        print(f"the two candidates disagree on {len(split)} of {len(both)} "
              "— worth reading even where the labels are shaky:")
        for key in sorted(split):
            print(f"  {key:<45} gold={labels[key]['label']}")
            print(f"     {left:<11} {results[left][key]['relevant']}"
                  f"  {results[left][key]['reason'][:70]}")
            print(f"     {right:<11} {results[right][key]['relevant']}"
                  f"  {results[right][key]['reason'][:70]}")


def freeze() -> None:
    """Write the configured model's verdicts as a committed artifact.

    `bitcap-db rebuild` drops `raw_llm_responses`, so without this a fresh clone
    starts with an empty verdict cache — and because the derivation gate only
    ever *reads* the cache, the rebuilt database renders every off-topic release
    again while `source_state` still reports the filter having excluded them.
    The README's promise that a rebuild needs no API key would also stop being
    true, since verdicts are the one derived thing here that cannot be recomputed
    from files.

    Everything else in this repo is reproducible from a committed artifact; this
    makes the verdicts so too. It is a by-product of the bake-off run, not a
    second purchase.
    """
    import yaml

    from app.pipeline import repo_relevance

    config = yaml.safe_load(
        (ROOT / "config" / "repo_signals.yaml").read_text(encoding="utf-8"))["relevance"]
    name = next(n for n, (_, model) in CANDIDATES.items() if model == config["model"])
    version = repo_relevance.cache_key(config)

    results = json.loads(RESULTS.read_text(encoding="utf-8"))[name]
    out = [
        {"repo": key, "relevant": bool(v["relevant"]), "reason": v["reason"],
         "prompt_version": version}
        for key, v in sorted(results.items()) if "relevant" in v
    ]
    VERDICTS.write_text(json.dumps(out, indent=2), encoding="utf-8")
    off = sum(1 for r in out if not r["relevant"])
    print(f"wrote {VERDICTS.relative_to(ROOT)} — {len(out)} verdicts under {version}")
    print(f"  {off} off-topic, {len(out) - off} relevant")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true",
                        help="re-report from the saved results, paying nothing")
    parser.add_argument("--freeze", action="store_true",
                        help="write the configured model's verdicts as a committed artifact")
    args = parser.parse_args()
    if args.freeze:
        freeze()
        return
    if not args.report:
        run()
    report()


if __name__ == "__main__":
    main()
