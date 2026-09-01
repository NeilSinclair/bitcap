"""Measure run-to-run variance of the classifier on the same documents.

Classification is not deterministic, and a comparison of two runs showed 9% of
items flipping `is_signal` between them. That is the silent-degradation failure
mode: the pipeline looks healthy while individual verdicts move underneath it.

This runs the same article through the model N times and reports what varies —
the fields, the tags, and the resulting score. It deliberately bypasses the
cache, since caching is what normally hides this.

Usage:
    python research/announcements/variance.py --runs 3 [--urls FILE]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
import yaml

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import score_announcements as sa  # noqa: E402
from score_announcements import (  # noqa: E402
    SCORING,
    drop_unknown_tags,
    load_env,
    score_of,
    vocabularies,
)

DOCS = ROOT / "research" / "docs"
ARTICLES = DOCS / "announcements.json"


def use_prompt(version: str) -> None:
    """Point the classifier at a prompt version and matching schema.

    Args:
        version: Prompt file stem, e.g. "v3" or "v4". Versions from v4 onward
            omit the `is_signal` field entirely.
    """
    sa.PROMPT_VERSION = version
    sa.PROMPT = ROOT / "prompts" / "announcement_scoring" / f"{version}.md"
    sa.SCHEMA = sa.build_schema(include_is_signal=version < "v4")


def unstable_urls() -> list[str]:
    """Find the items that already demonstrated instability.

    Compares the two completed runs and returns anything whose `is_signal`
    disagreed, which is the failing set worth probing.

    Returns:
        List of URLs.
    """
    a = {x["url"]: x for x in json.loads((DOCS / "scored_announcements.json").read_text())["scored"]}
    b = {
        x["url"]: x
        for x in json.loads((DOCS / "scored_announcements_v2.json").read_text())["scored"]
        if x.get("provenance") == "reclassified_v2"
    }
    return [u for u in b if u in a and a[u]["is_signal"] != b[u]["is_signal"]]


def summarise(article: dict, runs: list[dict], rules: dict) -> dict:
    """Reduce N classifications of one article to a variance record.

    Args:
        article: The article record.
        runs: One parsed result per run.
        rules: Parsed scoring config.

    Returns:
        Dict describing what did and did not vary.
    """
    scores = [score_of(r, rules)[0] for r in runs]
    mech_sets = [frozenset(m["id"] for m in r["mechanisms"]) for r in runs]
    strengths = []
    for r in runs:
        strengths.append(
            max(
                (
                    rules["magnitude"][m["magnitude"]] * rules["confidence"][m["confidence"]]
                    for m in r["mechanisms"]
                ),
                default=0,
            )
        )
    return {
        "url": article["url"],
        "lab": article["lab"],
        "title": article["title"],
        "text_source": article["text_source"],
        "runs": len(runs),
        "is_signal": [r.get("is_signal") for r in runs],
        "is_signal_stable": len(set(r.get("is_signal") for r in runs)) == 1,
        "event_types": [r["event_type"] for r in runs],
        "event_stable": len(set(r["event_type"] for r in runs)) == 1,
        "mechanism_sets": [sorted(s) for s in mech_sets],
        "mechanisms_stable": len(set(mech_sets)) == 1,
        "strengths": strengths,
        "scores": scores,
        "score_min": min(scores),
        "score_max": max(scores),
        "score_range": round(max(scores) - min(scores), 1),
        "score_stdev": round(statistics.stdev(scores), 2) if len(scores) > 1 else 0.0,
    }


def main() -> None:
    """Run the repeat-classification probe and report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--urls", help="JSON list of urls; defaults to the unstable set")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--prompt", default="v3", help="prompt version to probe")
    args = parser.parse_args()

    use_prompt(args.prompt)
    load_env()
    rules = yaml.safe_load(SCORING.read_text())
    _, _, mech_ids, cat_ids = vocabularies()

    articles = {a["url"]: a for a in json.loads(ARTICLES.read_text())}
    urls = json.loads(Path(args.urls).read_text()) if args.urls else unstable_urls()
    urls = [u for u in urls if u in articles]
    print(f"{len(urls)} articles x {args.runs} runs, prompt {args.prompt}, model {args.model}\n")

    client = anthropic.Anthropic()
    jobs = [(u, i) for u in urls for i in range(args.runs)]

    def one(job):
        url, _ = job
        result, cost = sa.classify(client, args.model, articles[url])
        result["dropped_tags"] = drop_unknown_tags(result, mech_ids, cat_ids)
        return url, result, cost

    collected: dict[str, list[dict]] = {u: [] for u in urls}
    costs = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for url, result, cost in pool.map(one, jobs):
            collected[url].append(result)
            costs.append(cost)

    tag = args.model.replace("claude-", "").replace("-20251001", "")
    out = DOCS / f"variance_{args.prompt}_{tag}.json"
    records = [summarise(articles[u], collected[u], rules) for u in urls]
    records.sort(key=lambda r: -r["score_range"])

    spend = sum(c["usd"] for c in costs)
    out.write_text(json.dumps({"records": records, "usd": round(spend, 4)}, indent=2))

    n = len(records)
    print(f"{'':<34}{'stable':>8}{'unstable':>10}")
    for field in ("is_signal", "event", "mechanisms"):
        key = f"{field}_stable"
        st = sum(1 for r in records if r[key])
        print(f"  {field:<32}{st:>8}{n - st:>10}")

    ranges = [r["score_range"] for r in records]
    print(f"\nscore range across {args.runs} runs:")
    print(f"  identical every run : {sum(1 for x in ranges if x == 0)}/{n}")
    print(f"  median range        : {statistics.median(ranges)}")
    print(f"  worst range         : {max(ranges)}")

    print("\n=== PER ITEM ===")
    for r in records:
        flag = "" if r["score_range"] == 0 else "  <-- varies"
        print(f"\n[{r['lab'][:4]}] {r['title'][:60]}{flag}")
        print(f"   scores      {r['scores']}   range {r['score_range']}")
        print(f"   is_signal   {r['is_signal']}")
        print(f"   event_type  {r['event_types']}")
        for s in r["mechanism_sets"]:
            print(f"   mechanisms  {s}")

    print(f"\n${spend:.4f} for {len(costs)} calls -> {out.name}")


if __name__ == "__main__":
    main()
