"""Apply the decisions in reconcile.yaml to test/articles.

Dry run by default; pass --write to change files. The prior labels are kept in
a `gold_pre_reconcile` block so the comparison that produced the worksheet stays
reproducible after the set is edited.

Tags pulled in from gold-fable carry their quote across, and that quote is
re-verified against this article's text before it is written. The rule that the
model is held to applies to whoever edits the worksheet as well.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import yaml

from score_announcements import ai_score_of, score_of
from verbatim import snap

ROOT = Path(__file__).parent.parent.parent
REF = ROOT / "research" / "announcements" / "gold-fable" / "articles"
TEST = ROOT / "research" / "announcements" / "test" / "articles"
SHEET = ROOT / "research" / "announcements" / "gold-fable" / "reconcile.yaml"

PLURAL = {"mechanism": "mechanisms", "category": "categories",
          "practice": "practices"}


def resolve(row: dict) -> str:
    """Read one row's decision, defaulting an unanswered row to `gold`.

    Args:
        row: A worksheet row.

    Returns:
        The chosen value: "gold", "fable", or a literal override.
    """
    d = row.get("decision")
    return "gold" if d is None or d == "" else d


def apply_row(block: dict, row: dict, ref: dict, failures: list, text: str) -> None:
    """Apply a single decision to an article's label block, in place.

    Args:
        block: The gold block being edited.
        row: The worksheet row.
        ref: The gold-fable block for the same article.
        failures: Accumulator for quotes that do not verify.
        text: The article text, for quote verification.
    """
    choice = resolve(row)
    if choice == "gold":
        return

    if row["kind"] == "event_type":
        block["event_type"] = ref["event_type"] if choice == "fable" else choice
        return

    kind = PLURAL[row["kind"]]
    tag_id = row["tag"]

    if row["field"] == "presence":
        if choice != "fable":
            failures.append(f"{row['id']}: presence takes only `fable` or `gold`, got {choice!r}")
            return
        if row["gold"] == "absent":
            tag = copy.deepcopy(next(t for t in ref[kind] if t["id"] == tag_id))
            exact = snap(tag["quote"], text)
            if exact is None:
                failures.append(f"{row['id']}: {tag_id} quote not found in article {row['article']}")
                return
            tag["quote"] = exact
            block.setdefault(kind, []).append(tag)
        else:
            block[kind] = [t for t in block[kind] if t["id"] != tag_id]
        return

    value = (next(t for t in ref[kind] if t["id"] == tag_id)[row["field"]]
             if choice == "fable" else choice)
    for tag in block.get(kind, []):
        if tag["id"] == tag_id:
            tag[row["field"]] = value


def main() -> None:
    """Apply every answered row and report the resulting scores."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write the files")
    args = parser.parse_args()

    rules = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())
    loaded = yaml.safe_load(SHEET.read_text())
    source = loaded.get("source", "unknown")
    sheet = loaded["rows"]
    if not source.startswith("test/articles"):
        raise SystemExit(
            f"worksheet was built against {source!r}, which is a run file, not "
            "test/articles. Applying it would write one run's judgements onto "
            "another. Rebuild without --run, or apply to that run instead."
        )

    open_rows = [r for r in sheet if r.get("decision") in (None, "")]
    answered = len(sheet) - len(open_rows)

    by_article = {}
    for row in sheet:
        by_article.setdefault(row["article"], []).append(row)

    failures, changed = [], []
    for art_id, rows in sorted(by_article.items()):
        path = TEST / f"{art_id}.json"
        doc = json.loads(path.read_text())
        ref = json.loads((REF / f"{art_id}.json").read_text())["gold"]

        before = copy.deepcopy(doc["gold"])
        block = copy.deepcopy(doc["gold"])
        for row in rows:
            apply_row(block, row, ref, failures, doc["text"])

        if block == before:
            continue

        s, b = score_of(block, rules)
        a, ab = ai_score_of(block, rules)
        old_s = score_of(before, rules)[0]
        old_a = ai_score_of(before, rules)[0]
        changed.append((art_id, old_s, s, old_a, a))

        doc["gold_pre_reconcile"] = before
        doc["gold"] = block
        doc["computed"] = {"score": s, "band": b, "ai_score": a, "ai_band": ab,
                           "note": "Scored from the reconciled gold block above."}
        if args.write:
            path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" ", f)

    print(f"{answered}/{len(sheet)} rows answered, {len(open_rows)} still open")
    if changed:
        print(f"\n{'id':<5} {'score':>14}   {'ai_score':>14}")
        for art_id, o_s, n_s, o_a, n_a in changed:
            print(f"{art_id:<5} {o_s:>6.1f} -> {n_s:<6.1f}   {o_a:>6.1f} -> {n_a:<6.1f}")
    else:
        print("no article changed")
    print("\ndry run -- pass --write to apply" if not args.write
          else f"\nwrote {len(changed)} articles")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
