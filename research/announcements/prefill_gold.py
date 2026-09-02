"""Pre-fill the gold set with system tags for human review.

Labelling twenty articles from scratch means reading them end to end and hunting
for supporting quotes, which is slow enough that it does not get done. This
fills each `gold` block with the system's own consensus tags so the job becomes
editing rather than authoring.

The trade is real and is recorded in the folder README: a pre-filled set is
**anchored**. A reviewer accepts a plausible wrong tag more readily than they
invent a right one, so the result is human-reviewed system output, not
independent ground truth.

To push back on that, every tag carries how many of the three runs produced it,
and items where the runs disagreed are flagged `needs_review`. Attention goes
where the model was least certain rather than being spread evenly.

Fills both axes. A gold block without `practices` cannot measure the half of the
system that serves the AI team, and the previous fill predates that axis
entirely.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import score_announcements as sa  # noqa: E402
from score_announcements import SCORING, ai_score_of, score_of, vocabularies  # noqa: E402
from providers import load_env as load_provider_env  # noqa: E402
from variance import use_prompt  # noqa: E402
from vote import run_pass  # noqa: E402

GOLD = Path(__file__).parent / "test" / "articles"
DOCS = ROOT / "research" / "docs"
ARTICLES = DOCS / "announcements.json"
COST = DOCS / "gold_prefill_cost.json"

MODEL = "claude-sonnet-5"
PROMPT = "v6"


def main() -> None:
    """Classify each gold article and write the tags back.

    With --votes 1 this is a single ordinary classification, which is what the
    pipeline actually does per article. Higher values reduce several runs by
    majority, which is more stable but is not the thing being measured when the
    system is compared against independent human labels.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--votes", type=int, default=1)
    args = parser.parse_args()
    votes = args.votes

    use_prompt(PROMPT)
    load_provider_env()
    rules = yaml.safe_load(SCORING.read_text())
    _, _, _, mech_ids, cat_ids, prac_ids = vocabularies()
    dims, cap = sa.practice_dimensions(), sa.dimension_cap()

    files = sorted(GOLD.glob("*.json"))
    records = [json.loads(f.read_text()) for f in files]
    by_url = {a["url"]: a for a in json.loads(ARTICLES.read_text())}
    articles = [by_url[r["url"]] for r in records]
    label = "single run" if votes == 1 else f"{votes}-vote consensus"
    print(f"{len(articles)} articles, {label}, {MODEL}, prompt {PROMPT}")

    voted, costs = run_pass(
        "anthropic", MODEL, articles, votes, 12,
        mech_ids, cat_ids, prac_ids, dims, cap,
    )
    for result in voted.values():
        result["score"], result["band"] = score_of(result, rules)
        result["ai_score"], result["ai_band"] = ai_score_of(result, rules)

    COST.write_text(json.dumps(costs, indent=2))

    contested = 0
    for path, record in zip(files, records):
        result = voted[record["url"]]
        notes = result["vote_notes"]

        # A tag that only some runs produced, or an event type without a clean
        # sweep, is where a human eye is actually worth spending.
        split_tags = [
            m["id"] for m in result["mechanisms"] if m["votes"] != f"{votes}/{votes}"
        ]
        # A single run cannot disagree with itself, so it carries no evidence of
        # certainty either way. Saying so is more honest than reporting False.
        if votes == 1:
            needs_review = None
        else:
            needs_review = bool(
                notes["mechanisms_dropped"]
                or split_tags
                or len(notes["event_counts"]) > 1
            )
        contested += bool(needs_review)

        record["gold"] = {
            "event_type": result["event_type"],
            "mechanisms": [
                {
                    "id": m["id"],
                    "sign": m["sign"],
                    "magnitude": m["magnitude"],
                    "confidence": m["confidence"],
                    "quote": m["quote"],
                }
                for m in result["mechanisms"]
            ],
            "categories": [
                {
                    "id": c["id"],
                    "sign": c["sign"],
                    "confidence": c["confidence"],
                    "quote": c.get("quote", ""),
                }
                for c in result["categories"]
            ],
            "practices": [
                {
                    "id": p["id"],
                    "action": p["action"],
                    "impact": p["impact"],
                    "confidence": p["confidence"],
                    "dimensions": p.get("dimensions", []),
                    "quote": p.get("quote", ""),
                }
                for p in result["practices"]
            ],
            "notes": "",
        }
        record["review"] = {
            "votes": votes,
            "needs_review": needs_review,
            "why": (
                "single run: no agreement signal available"
                if votes == 1
                else
                "; ".join(
                    filter(
                        None,
                        [
                            f"event type split {notes['event_counts']}"
                            if len(notes["event_counts"]) > 1
                            else "",
                            f"tags not unanimous: {split_tags}" if split_tags else "",
                            f"tags dropped by the vote: {notes['mechanisms_dropped']}"
                            if notes["mechanisms_dropped"]
                            else "",
                        ],
                    )
                )
                or "all runs agreed"
            ),
            "tag_votes": {
                **{m["id"]: m["votes"] for m in result["mechanisms"]},
                **{p["id"]: p["votes"] for p in result["practices"]},
            },
        }
        record["system"] = {
            "score": result["score"],
            "band": result["band"],
            "ai_score": result["ai_score"],
            "ai_band": result["ai_band"],
            "summary": result["summary"],
            "model": MODEL,
            "prompt": PROMPT,
            "votes": votes,
            "note": "The system's own answer, kept for comparison. Edit `gold`, not this.",
        }
        path.write_text(json.dumps(record, indent=2))

    spend = sum(c["usd"] for c in costs)
    scoring = sum(1 for r in voted.values() if r["score"] > 0)
    ai_scoring = sum(1 for r in voted.values() if r["ai_score"] > 0)
    print(f"\nprefilled {len(files)} files")
    print(f"  investment score >0: {scoring}")
    print(f"  AI-team score >0   : {ai_scoring}")
    if votes > 1:
        print(f"  flagged for review : {contested}")
    print(f"  ${spend:.4f} for {len(costs)} calls")


if __name__ == "__main__":
    main()
