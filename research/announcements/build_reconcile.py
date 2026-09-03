"""Build a decision worksheet from the gold vs gold-fable differences.

Two independent label sets disagree on 20 articles. Rereading the corpus to
settle that is hours of work; the disagreements themselves are a short list,
and every quote in both sets is a verified literal substring of its article,
so each call can be made from the quote alone.

Each row is one decision. Rows carry `impact`: what that single call does to
the article's score if you side with gold-fable. Rows are sorted by impact, so
the list can be abandoned partway once the remaining calls stop mattering.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from score_announcements import ai_score_of, score_of

ROOT = Path(__file__).parent.parent.parent
REF = ROOT / "research" / "announcements" / "gold-fable" / "articles"
TEST = ROOT / "research" / "announcements" / "test" / "articles"
OUT = ROOT / "research" / "announcements" / "gold-fable" / "reconcile.yaml"
RESULTS = ROOT / "research" / "test_results"

KINDS = ("mechanisms", "categories", "practices")
SINGULAR = {"mechanisms": "mechanism", "categories": "category",
            "practices": "practice"}
ATTRS = {
    "mechanisms": ("magnitude", "confidence", "sign"),
    "categories": ("confidence", "sign"),
    "practices": ("action", "impact", "confidence"),
}


def impact(base: dict, mutate, rules: dict) -> dict:
    """Score change from applying one mutation to the test labels.

    Args:
        base: The test run's gold block.
        mutate: Callable taking a deep copy and applying one change.
        rules: Parsed config/scoring.yaml.

    Returns:
        Dict of score and ai_score deltas, rounded to one place.
    """
    before = score_of(base, rules)[0], ai_score_of(base, rules)[0]
    trial = copy.deepcopy(base)
    mutate(trial)
    after = score_of(trial, rules)[0], ai_score_of(trial, rules)[0]
    return {"score": round(after[0] - before[0], 1),
            "ai_score": round(after[1] - before[1], 1)}


def rows_for(art_id: str, ref: dict, test: dict, title: str, rules: dict) -> list[dict]:
    """Enumerate every disagreement between two label blocks for one article.

    Args:
        art_id: Article id.
        ref: gold-fable labels.
        test: gold labels.
        title: Article title, for orientation while editing.
        rules: Parsed config/scoring.yaml.

    Returns:
        List of decision rows.
    """
    out = []

    def row(**kw):
        out.append({"article": art_id, "title": title, **kw})

    if ref["event_type"] != test["event_type"]:
        def set_event(d):
            d["event_type"] = ref["event_type"]
        row(kind="event_type", tag="-", field="event_type",
            fable=ref["event_type"], gold=test["event_type"],
            impact=impact(test, set_event, rules), quote="",
            note=f"weights {rules['event_weight'][ref['event_type']]} vs "
                 f"{rules['event_weight'][test['event_type']]}")

    for kind in KINDS:
        rmap = {t["id"]: t for t in ref.get(kind, [])}
        tmap = {t["id"]: t for t in test.get(kind, [])}
        singular = SINGULAR[kind]

        for tag_id in sorted(set(rmap) - set(tmap)):
            tag = rmap[tag_id]

            def add(d, k=kind, t=tag):
                d.setdefault(k, []).append(copy.deepcopy(t))
            bits = "/".join(str(tag.get(f)) for f in ATTRS[kind])
            row(kind=singular, tag=tag_id, field="presence",
                fable=f"present ({bits})", gold="absent",
                impact=impact(test, add, rules), quote=tag["quote"],
                note=tag["reason"])

        for tag_id in sorted(set(tmap) - set(rmap)):
            tag = tmap[tag_id]

            def drop(d, k=kind, i=tag_id):
                d[k] = [x for x in d[k] if x["id"] != i]
            bits = "/".join(str(tag.get(f)) for f in ATTRS[kind])
            row(kind=singular, tag=tag_id, field="presence",
                fable="absent", gold=f"present ({bits})",
                impact=impact(test, drop, rules), quote=tag["quote"],
                note=tag.get("reason", ""))

        for tag_id in sorted(set(rmap) & set(tmap)):
            rt, tt = rmap[tag_id], tmap[tag_id]
            for field in ATTRS[kind]:
                if field not in rt or field not in tt or rt[field] == tt[field]:
                    continue

                def setf(d, k=kind, i=tag_id, f=field, v=rt[field]):
                    for x in d[k]:
                        if x["id"] == i:
                            x[f] = v
                row(kind=singular, tag=tag_id, field=field,
                    fable=rt[field], gold=tt[field],
                    impact=impact(test, setf, rules), quote=tt["quote"],
                    note="")
            if tag_id == "model_capability":
                rd, td = rt.get("dimensions", []), tt.get("dimensions", [])
                if rd != td:
                    row(kind=singular, tag=tag_id, field="dimensions",
                        fable=rd, gold=td,
                        impact={"score": 0.0, "ai_score": 0.0}, quote=tt["quote"],
                        note="ranking only; does not enter the score")
    return out


def existing_answers() -> dict:
    """Read answers already written into the worksheet, if it exists.

    Keyed on what the row is about rather than on its id, because ids are
    positional and shift whenever the row set changes.

    Returns:
        Map of (article, kind, tag, field) to the recorded decision.
    """
    if not OUT.exists():
        return {}
    kept = {}
    loaded = yaml.safe_load(OUT.read_text()) or {}
    # Sheets written before `source` was added are a bare list of rows.
    previous = loaded if isinstance(loaded, list) else loaded.get("rows", [])
    for row in previous:
        if row.get("decision") not in (None, ""):
            kept[(row["article"], row["kind"], row["tag"], row["field"])] = row["decision"]
    return kept


def main() -> None:
    """Write the worksheet, ordered by how much each call moves the score."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run", default="",
        help="a run file in research/test_results to use as the test side",
    )
    parser.add_argument(
        "--skip-presence", action="store_true",
        help="omit tag-existence rows; they then default to gold's tag set",
    )
    args = parser.parse_args()

    rules = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())
    answers = existing_answers()

    if args.run:
        run = json.loads((RESULTS / args.run).read_text())
        predictions = {r["id"]: r["run"] for r in run["results"]}
        source = f"{run['model']} @ {run['prompt']}, {args.run}"
    else:
        predictions, source = None, "test/articles"

    rows = []
    for path in sorted(REF.glob("*.json")):
        ref = json.loads(path.read_text())
        test = (predictions[ref["id"]] if predictions is not None
                else json.loads((TEST / path.name).read_text())["gold"])
        rows += rows_for(ref["id"], ref["gold"], test, ref["title"], rules)

    dropped = 0
    if args.skip_presence:
        before = len(rows)
        rows = [r for r in rows if r["field"] != "presence"]
        dropped = before - len(rows)

    rows.sort(key=lambda r: -max(abs(r["impact"]["score"]),
                                 abs(r["impact"]["ai_score"])))
    for n, r in enumerate(rows, 1):
        r["id"] = f"d{n:02d}"
        r["decision"] = answers.get(
            (r["article"], r["kind"], r["tag"], r["field"])
        )

    order = ["id", "article", "title", "kind", "tag", "field",
             "fable", "gold", "impact", "quote", "note", "decision"]
    body = yaml.dump(
        {"source": source,
         "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "rows": [{k: r[k] for k in order} for r in rows]},
        sort_keys=False, allow_unicode=True, width=88, default_flow_style=False,
    )
    OUT.write_text(HEADER + body)

    moves = sum(1 for r in rows
                if max(abs(r["impact"]["score"]), abs(r["impact"]["ai_score"])) > 0)
    print(f"test side: {source}")
    print(f"{len(rows)} decisions -> {OUT.relative_to(ROOT)}")
    print(f"{moves} of them change a score; {len(rows) - moves} are record-only")
    if dropped:
        print(f"{dropped} tag-existence rows omitted -- those tags stay as gold has them")
    if answers:
        kept = sum(1 for r in rows if r["decision"] is not None)
        print(f"{kept} previous answers carried over")


HEADER = """\
# Reconciling test/articles against gold-fable/articles.
#
# One row per disagreement between the two label sets. Every quote below is a
# verified literal substring of its article, so each call can be made from the
# quote alone -- no need to reopen the corpus.
#
# `source` names the test side these rows were computed against. Decisions are
# only meaningful against that source -- a different run has different quotes.
#
# `fable` is gold-fable/articles. `gold` is the classifier run under test.
# `impact` is what
# siding with fable does to THAT article's score, holding everything else fixed.
#
# Set `decision` on each row to one of:
#   fable   - take gold-fable's value (for `presence`: add the tag / drop it)
#   gold    - keep gold's value
#   <value> - a literal third answer, e.g. `medium`, or a list for dimensions
# Leave it null to defer; apply_reconcile.py treats null as `gold` and reports
# how many are still open.
#
# Rows are sorted by impact. Everything below the first zero-impact row is
# bookkeeping -- real disagreements, but they do not move a number.

"""


if __name__ == "__main__":
    main()
