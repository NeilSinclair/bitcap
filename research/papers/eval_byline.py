"""Compare LLM byline extraction against the deterministic parser.

The deterministic output is treated as the reference, not as truth: it was
hand-checked against all 17 pages, but every disagreement is printed in full so a
human can decide which side is wrong. Averaging the disagreements away would hide
exactly the cases the evaluation exists to find.

Usage:
    python research/eval_byline.py
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
GOLD = ROOT / "research" / "docs" / "anthropic_contributors.json"
PRED = ROOT / "research" / "docs" / "byline_llm.json"
REPORT = ROOT / "research" / "docs" / "byline_eval.json"


def normalise(name: str) -> str:
    """Reduce a name to a comparison key.

    Initials and accents differ between renderings of the same byline, so
    "Nicholas L. Turner" and "Nicholas L Turner" must not count as two people.
    Distinct given names ("Jon" vs "Jonathan") deliberately still differ — that is
    a real entity-resolution question, not a formatting one.

    Args:
        name: Author name as extracted.

    Returns:
        Normalised comparison key.
    """
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^\w\s]", "", n.lower())
    return re.sub(r"\s+", " ", n).strip()


def compare_article(gold: dict, pred: dict) -> dict:
    """Compare one article's gold and predicted bylines.

    Args:
        gold: Article record from the deterministic harvest.
        pred: Matching record from the LLM run.

    Returns:
        Per-article comparison with counts and explicit disagreement lists.
    """
    g_by = {normalise(a["name"]): a for a in gold["authors"]}
    p_by = {normalise(a["name"]): a for a in pred.get("authors", [])}
    shared = sorted(set(g_by) & set(p_by))

    field_diffs = []
    for key in shared:
        g, p = g_by[key], p_by[key]
        g_aff = sorted({x.strip() for x in g["affiliations"]})
        p_aff = sorted({x.strip() for x in p["affiliations"]})
        if g_aff != p_aff:
            field_diffs.append(
                {"name": g["name"], "field": "affiliations", "gold": g_aff, "llm": p_aff}
            )
        for field in ("is_anthropic", "is_fellow"):
            if g.get(field) != p.get(field):
                field_diffs.append(
                    {"name": g["name"], "field": field, "gold": g.get(field), "llm": p.get(field)}
                )

    return {
        "url": gold["url"],
        "title": gold["title"],
        "truncated": pred.get("truncated", False),
        "gold_n": len(g_by),
        "llm_n": len(p_by),
        "matched": len(shared),
        "missed": [g_by[k]["name"] for k in sorted(set(g_by) - set(p_by))],
        "invented": [p_by[k]["name"] for k in sorted(set(p_by) - set(g_by))],
        "exact_set": set(g_by) == set(p_by),
        "order_match": [g_by[k]["name"] for k in g_by] == [p_by[k]["name"] for k in p_by]
        if set(g_by) == set(p_by)
        else False,
        "date_gold": gold["date"],
        "date_llm": pred.get("date"),
        "date_match": gold["date"] == pred.get("date"),
        "star_gold": gold.get("star_means"),
        "star_llm": pred.get("star_means"),
        "star_match": gold.get("star_means") == pred.get("star_means"),
        "order_meaningful_gold": gold.get("order_meaningful"),
        "order_meaningful_llm": pred.get("order_meaningful"),
        "order_meaningful_match": gold.get("order_meaningful") == pred.get("order_meaningful"),
        "field_diffs": field_diffs,
    }


def summarise(rows: list[dict]) -> dict:
    """Aggregate per-article comparisons into headline metrics.

    Args:
        rows: Per-article comparison records.

    Returns:
        Micro-averaged metrics over all bylines.
    """
    tp = sum(r["matched"] for r in rows)
    fn = sum(len(r["missed"]) for r in rows)
    fp = sum(len(r["invented"]) for r in rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    aff_checked = sum(r["matched"] for r in rows)
    aff_wrong = sum(1 for r in rows for d in r["field_diffs"] if d["field"] == "affiliations")
    return {
        "articles": len(rows),
        "gold_bylines": tp + fn,
        "llm_bylines": tp + fp,
        "name_precision": round(precision, 4),
        "name_recall": round(recall, 4),
        "name_f1": round(f1, 4),
        "names_missed": fn,
        "names_invented": fp,
        "articles_exact_author_set": sum(1 for r in rows if r["exact_set"]),
        "articles_exact_and_ordered": sum(1 for r in rows if r["order_match"]),
        "affiliation_accuracy_on_matched": round(1 - aff_wrong / aff_checked, 4)
        if aff_checked
        else None,
        "is_anthropic_disagreements": sum(
            1 for r in rows for d in r["field_diffs"] if d["field"] == "is_anthropic"
        ),
        "is_fellow_disagreements": sum(
            1 for r in rows for d in r["field_diffs"] if d["field"] == "is_fellow"
        ),
        "date_accuracy": round(sum(r["date_match"] for r in rows) / len(rows), 4),
        "star_means_accuracy": round(sum(r["star_match"] for r in rows) / len(rows), 4),
        "order_meaningful_accuracy": round(
            sum(r["order_meaningful_match"] for r in rows) / len(rows), 4
        ),
        "truncated_pages": sum(1 for r in rows if r["truncated"]),
    }


def main() -> None:
    """Run the comparison and print the report."""
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    pred = json.loads(PRED.read_text(encoding="utf-8"))
    by_url = {p["url"]: p for p in pred["results"] if "error" not in p}
    errors = [p for p in pred["results"] if "error" in p]

    rows = [compare_article(a, by_url[a["url"]]) for a in gold["articles"] if a["url"] in by_url]
    summary = summarise(rows)
    summary["call_errors"] = len(errors)
    summary["model"] = pred["model"]
    summary["prompt"] = pred["prompt"]

    REPORT.write_text(
        json.dumps({"summary": summary, "articles": rows, "errors": errors}, indent=2),
        encoding="utf-8",
    )

    print(f"model={summary['model']}  prompt={summary['prompt']}\n")
    for k, v in summary.items():
        if k not in ("model", "prompt"):
            print(f"  {k:34} {v}")

    print("\n--- disagreements, per article ---")
    for r in rows:
        if r["exact_set"] and not r["field_diffs"] and r["date_match"] and r["star_match"]:
            continue
        print(f"\n{r['title'][:70]}  (gold {r['gold_n']} / llm {r['llm_n']})")
        if r["missed"]:
            print(f"    missed  : {', '.join(r['missed'])}")
        if r["invented"]:
            print(f"    invented: {', '.join(r['invented'])}")
        if not r["date_match"]:
            print(f"    date    : gold={r['date_gold']} llm={r['date_llm']}")
        if not r["star_match"]:
            print(f"    star    : gold={r['star_gold']} llm={r['star_llm']}")
        if not r["order_meaningful_match"]:
            print(
                f"    order   : gold={r['order_meaningful_gold']} "
                f"llm={r['order_meaningful_llm']}"
            )
        for d in r["field_diffs"]:
            print(f"    {d['field']:12} {d['name']}: gold={d['gold']} llm={d['llm']}")
    print(f"\nfull report -> {REPORT}")


if __name__ == "__main__":
    main()
