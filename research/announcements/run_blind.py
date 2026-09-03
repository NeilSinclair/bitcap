"""Classify the blind-label articles and compare against labels written first.

The adjudicated gold set is 85% classifier output the adjudicator read and let
stand. Reviewing is a weaker act than authoring: it is easier to accept a
plausible wrong tag than to notice a missing one. This measures the gap by
having the adjudicator label five unseen articles from scratch, committing those
labels, and only then running the classifier.

The articles are drawn from the corpus but from neither the gold set nor
hard_cases, so the adjudicator had seen no tags for any of them.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
import yaml

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import score_announcements as sa  # noqa: E402
from providers import load_env  # noqa: E402
from variance import use_prompt  # noqa: E402
from verbatim import enforce as enforce_quotes  # noqa: E402

RESULTS = ROOT / "research" / "test_results"
ARTICLES = RESULTS / "blind_articles.json"
OUT = RESULTS / "blind_run.json"
MODEL = "claude-sonnet-5"
PROMPT = "v6"


def main() -> None:
    """Classify the five blind articles once and record the run."""
    use_prompt(PROMPT)
    load_env()
    _, _, _, mech_ids, cat_ids, prac_ids = sa.vocabularies()
    dims, cap = sa.practice_dimensions(), sa.dimension_cap()
    client = anthropic.Anthropic()
    articles = json.loads(ARTICLES.read_text())

    def work(article: dict) -> dict:
        result, cost = sa.classify(client, MODEL, article)
        result["dropped_tags"] = sa.drop_unknown_tags(
            result, mech_ids, cat_ids, prac_ids, dims, cap
        )
        result["dropped_tags"] += enforce_quotes(result, article["text"])
        return {"title": article["title"], "url": article["url"],
                "run": result, "cost": cost}

    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(work, articles))

    rules = yaml.safe_load(sa.SCORING.read_text())
    for row in rows:
        row["score"], _ = sa.score_of(row["run"], rules)
        row["ai_score"], _ = sa.ai_score_of(row["run"], rules)

    spend = sum(r["cost"]["usd"] for r in rows)
    OUT.write_text(json.dumps({"model": MODEL, "prompt": PROMPT,
                               "results": rows, "usd": round(spend, 4)}, indent=2))
    print(f"{len(rows)} classified, ${spend:.4f} -> {OUT.name}")


if __name__ == "__main__":
    main()
