"""Classify by majority vote, and test whether the vote itself is stable.

A single classification is not reproducible: on a probe of borderline items only
6 of 12 scored identically across three runs. The obvious remedy is to classify
each item several times and reduce the results to one answer.

That remedy is worth nothing unless the *reduced* answer is stable, so this
script runs the whole voting process twice, independently, and compares. Two
voted passes that disagree would mean voting has bought nothing but cost.

Voting rule:
  event_type  majority; a three-way tie is recorded rather than broken silently
  mechanisms  kept when present in at least a majority of runs
  magnitude   median across the runs that tagged it, ties rounding down
  confidence  median across the runs that tagged it, ties rounding down

Usage:
    python research/announcements/vote.py --votes 3 --passes 2
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
from providers import classify as provider_classify  # noqa: E402
from providers import load_env as load_provider_env  # noqa: E402
from variance import use_prompt  # noqa: E402

DOCS = ROOT / "research" / "docs"
ARTICLES = DOCS / "announcements.json"
LEVELS = ["low", "medium", "high"]


def median_level(values: list[str]) -> str:
    """Take the median of ordered magnitude or confidence labels.

    Args:
        values: Labels drawn from LEVELS.

    Returns:
        The median label, rounding down on an even split so the vote never
        inflates a tag above what most runs claimed.
    """
    idx = sorted(LEVELS.index(v) for v in values)
    return LEVELS[idx[(len(idx) - 1) // 2]]


def vote(runs: list[dict]) -> dict:
    """Reduce several classifications of one article to a single answer.

    Args:
        runs: Parsed results for the same article.

    Returns:
        A voted result, plus `vote_notes` recording where the runs disagreed.
    """
    n = len(runs)
    needed = n // 2 + 1

    events = Counter(r["event_type"] for r in runs)
    top, top_n = events.most_common(1)[0]
    event_tie = top_n < needed

    tallies: dict[str, list[dict]] = {}
    for r in runs:
        for tag in r["mechanisms"]:
            tallies.setdefault(tag["id"], []).append(tag)

    mechanisms = []
    for mid, tags in sorted(tallies.items()):
        if len(tags) < needed:
            continue
        mechanisms.append(
            {
                "id": mid,
                "sign": Counter(t["sign"] for t in tags).most_common(1)[0][0],
                "magnitude": median_level([t["magnitude"] for t in tags]),
                "confidence": median_level([t["confidence"] for t in tags]),
                "reason": tags[0]["reason"],
                "quote": tags[0]["quote"],
                "votes": f"{len(tags)}/{n}",
            }
        )

    cats: dict[str, list[dict]] = {}
    for r in runs:
        for tag in r["categories"]:
            cats.setdefault(tag["id"], []).append(tag)
    categories = [
        {**tags[0], "votes": f"{len(tags)}/{n}"}
        for cid, tags in sorted(cats.items())
        if len(tags) >= needed
    ]

    return {
        "event_type": top,
        "is_signal": True,  # unused by scoring; kept for schema compatibility
        "summary": runs[0]["summary"],
        "mechanisms": mechanisms,
        "categories": categories,
        "notable": Counter(r["notable"] for r in runs).most_common(1)[0][0],
        "notable_reason": runs[0].get("notable_reason", ""),
        "vote_notes": {
            "runs": n,
            "event_counts": dict(events),
            "event_tie": event_tie,
            "mechanisms_seen": {k: len(v) for k, v in sorted(tallies.items())},
            "mechanisms_dropped": sorted(
                k for k, v in tallies.items() if len(v) < needed
            ),
        },
    }


def run_pass(
    provider: str,
    model: str,
    articles: list[dict],
    votes: int,
    workers: int,
    mech_ids: set,
    cat_ids: set,
) -> tuple[dict, list[dict]]:
    """Classify every article `votes` times and reduce each by majority.

    Args:
        provider: Provider key, "anthropic" or "openai".
        model: Model id.
        articles: Articles to classify.
        votes: Classifications per article.
        workers: Concurrency.
        mech_ids: Valid mechanism ids.
        cat_ids: Valid category ids.

    Returns:
        Tuple of (url -> voted result, cost records).
    """
    jobs = [(a, i) for a in articles for i in range(votes)]

    def one(job):
        article, _ = job
        system, user = sa.build_prompt(article)
        result, cost = provider_classify(
            provider, model, system, user, sa.SCHEMA, article["url"]
        )
        result["dropped_tags"] = drop_unknown_tags(result, mech_ids, cat_ids)
        return article["url"], result, cost

    collected: dict[str, list[dict]] = {a["url"]: [] for a in articles}
    costs = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for url, result, cost in pool.map(one, jobs):
            collected[url].append(result)
            costs.append(cost)

    return {url: vote(runs) for url, runs in collected.items()}, costs


def main() -> None:
    """Run the voting process twice and compare the two voted answers."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--votes", type=int, default=3)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    parser.add_argument("--prompt", default="v4")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--urls", default=str(DOCS / "vote_sample.json"))
    args = parser.parse_args()

    use_prompt(args.prompt)
    load_env()
    load_provider_env()
    rules = yaml.safe_load(SCORING.read_text())
    _, _, mech_ids, cat_ids = vocabularies()

    by_url = {a["url"]: a for a in json.loads(ARTICLES.read_text())}
    urls = json.loads(Path(args.urls).read_text())
    articles = [by_url[u] for u in urls if u in by_url]
    print(
        f"{len(articles)} articles, {args.votes} votes each, "
        f"{args.passes} independent passes, {args.provider}/{args.model}\n"
    )

    passes, costs = [], []
    for p in range(args.passes):
        voted, c = run_pass(
            args.provider, args.model, articles, args.votes, args.workers,
            mech_ids, cat_ids,
        )
        for url, result in voted.items():
            result["score"], result["band"] = score_of(result, rules)
        passes.append(voted)
        costs.extend(c)
        print(f"pass {p + 1} done  (${sum(x['usd'] for x in c):.3f})")

    print(f"\n{'item':<40}" + "".join(f"{'pass ' + str(i + 1):>10}" for i in range(args.passes)) + f"{'agree':>8}")
    identical = 0
    for article in articles:
        url = article["url"]
        scores = [p[url]["score"] for p in passes]
        same = len(set(scores)) == 1
        identical += same
        print(
            f"{article['title'][:38]:<40}"
            + "".join(f"{s:>10}" for s in scores)
            + f"{'yes' if same else 'NO':>8}"
        )

    print(f"\nvoted score identical across passes: {identical}/{len(articles)}")

    print("\n=== WHERE THE PASSES DIFFER ===")
    for article in articles:
        url = article["url"]
        a, b = passes[0][url], passes[-1][url]
        if a["score"] == b["score"] and a["event_type"] == b["event_type"]:
            continue
        print(f"\n{article['title'][:62]}")
        print(f"   score       {a['score']}  vs  {b['score']}")
        print(f"   event_type  {a['event_type']}  vs  {b['event_type']}")
        print(f"   events seen {a['vote_notes']['event_counts']}  vs  {b['vote_notes']['event_counts']}")
        print(f"   mechs kept  {[m['id'] for m in a['mechanisms']]}  vs  {[m['id'] for m in b['mechanisms']]}")
        print(f"   mechs seen  {a['vote_notes']['mechanisms_seen']}  vs  {b['vote_notes']['mechanisms_seen']}")

    ties = sum(1 for p in passes for r in p.values() if r["vote_notes"]["event_tie"])
    dropped = sum(
        len(r["vote_notes"]["mechanisms_dropped"]) for p in passes for r in p.values()
    )
    print(f"\nevent-type votes with no majority : {ties}")
    print(f"mechanism tags dropped by the vote: {dropped}")

    spend = sum(x["usd"] for x in costs)
    tag = args.model.replace("claude-", "").replace("-20251001", "")
    out = DOCS / f"vote_{args.prompt}_{args.votes}x{args.passes}_{tag}.json"
    out.write_text(
        json.dumps(
            {
                "votes": args.votes,
                "passes": [
                    {u: r for u, r in p.items()} for p in passes
                ],
                "usd": round(spend, 4),
            },
            indent=2,
        )
    )
    print(f"\n${spend:.4f} for {len(costs)} calls -> {out.name}")


if __name__ == "__main__":
    main()
