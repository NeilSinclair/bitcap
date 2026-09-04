"""Classify the gold-set articles once and write the run to its own file.

Deliberately does NOT touch test/articles/*.json. That file's `gold` block is
the human-editable slot, and overwriting it has destroyed tagging work before.
This writes a standalone run so the previous run stays on disk for comparison.

The cache is version-scoped by prompt, and the vocabulary has changed since the
last gold run (mechanisms retired, one renamed and inverted, inference_cost_down
narrowed to efficiency). Cached tags predate all of that, so `--fresh` bypasses
the cache and pays for a genuinely current classification.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import yaml

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import score_announcements as sa  # noqa: E402
from providers import load_env  # noqa: E402
from variance import use_prompt  # noqa: E402
from verbatim import enforce as enforce_quotes  # noqa: E402

GOLD = Path(__file__).parent / "test" / "articles"
RESULTS = ROOT / "research" / "test_results"
MODEL = "claude-sonnet-5"
PROMPT = "v8"


def main() -> None:
    """Classify every gold article once and record the result."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="ignore the cache")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--tag", default="", help="suffix for the output file")
    parser.add_argument("--prompt", default=PROMPT, help="prompt version to run")
    parser.add_argument(
        "--summaries", default=None,
        help="path to a summaries_<model>.json from summarise.py; classify the "
             "summary text instead of the article text (forces --fresh, since "
             "the cache is keyed on the article, not its text)",
    )
    args = parser.parse_args()
    if args.summaries:
        args.fresh = True

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"gold_run_{stamp}{('_' + args.tag) if args.tag else ''}.json"

    use_prompt(args.prompt)
    load_env()
    rules = yaml.safe_load(sa.SCORING.read_text())
    _, _, _, mech_ids, cat_ids, prac_ids = sa.vocabularies()
    dims, cap = sa.practice_dimensions(), sa.dimension_cap()
    client = anthropic.Anthropic()

    files = sorted(GOLD.glob("*.json"))
    records = [json.loads(f.read_text()) for f in files]
    if args.summaries:
        run = json.loads(Path(args.summaries).read_text())
        summaries = {r["id"]: r["summary"] for r in run["results"] if "summary" in r}
        missing = [r["id"] for r in records if r["id"] not in summaries]
        if missing:
            sys.exit(f"no summary for gold articles: {missing}")
        for rec in records:
            # Keep the original text_source: the rss_summary confidence cap
            # would otherwise change behaviour and muddy the comparison. This
            # run measures information loss from summarisation, nothing else.
            rec["text"] = summaries[rec["id"]]
        print(f"classifying SUMMARIES from {Path(args.summaries).name} "
              f"(model {run['model']})")
    print(f"{len(records)} gold articles | {MODEL} | prompt {args.prompt} | "
          f"{len(mech_ids)} mechanisms, {len(prac_ids)} practices | "
          f"cache {'BYPASSED' if args.fresh else 'used'}")

    def work(rec: dict) -> dict:
        # Pass the article through whole. The prompt builder reads text_source
        # to cap confidence on RSS-summary items, and silently drops fields it
        # does not use; a hand-picked subset just loses one of them.
        article = {k: v for k, v in rec.items()
                   if k not in ("gold", "review", "system")}
        if args.fresh:
            # classify() is the uncached path; classify_one() would return the
            # stale tags written under the previous vocabulary.
            try:
                result, cost = sa.classify(client, MODEL, article)
            except Exception as exc:
                return {"id": rec["id"], "error": str(exc)}
            result["dropped_tags"] = sa.drop_unknown_tags(
                result, mech_ids, cat_ids, prac_ids, dims, cap
            )
            result["dropped_tags"] += enforce_quotes(result, article["text"])
        else:
            result, cost, fail = sa.classify_one(
                client, MODEL, article, mech_ids, cat_ids, prac_ids, dims, cap
            )
            if fail:
                return {"id": rec["id"], "error": fail["error"]}
        score, band = sa.score_of(result, rules)
        ai_score, ai_band = sa.ai_score_of(result, rules)
        return {
            "id": rec["id"], "lab": rec["lab"], "date": rec["date"],
            "title": rec["title"], "url": rec["url"],
            "text_source": rec["text_source"], "text_chars": len(rec["text"]),
            "score": score, "band": band,
            "ai_score": ai_score, "ai_band": ai_band,
            "run": result, "cost": cost,
        }

    started = time.time()
    # Warm the prompt cache before fanning out: classify() caches the shared
    # system prompt, and a cold parallel start would make the whole first wave
    # 1.25x cache writes instead of 0.1x reads.
    first = work(records[0])
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = [first] + list(pool.map(work, records[1:]))

    spend = sum(r["cost"]["usd"] for r in rows if r.get("cost"))
    calls = sum(1 for r in rows if r.get("cost"))
    failures = [r for r in rows if "error" in r]
    out.write_text(json.dumps(
        {"model": MODEL, "prompt": args.prompt, "mechanisms": sorted(mech_ids),
         "practices": sorted(prac_ids), "results": rows,
         "usd": round(spend, 4), "calls": calls}, indent=2))

    scoring = sum(1 for r in rows if r.get("score", 0) > 0)
    ai_scoring = sum(1 for r in rows if r.get("ai_score", 0) > 0)
    print(f"\n{len(rows)} classified in {time.time() - started:.0f}s")
    print(f"  investment score >0: {scoring}")
    print(f"  AI-team score >0   : {ai_scoring}")
    print(f"  failures           : {len(failures)}")
    print(f"  ${spend:.4f} across {calls} paid calls -> {out.name}")


if __name__ == "__main__":
    main()
