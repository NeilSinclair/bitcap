"""Rebuild the gold set's reference labels from the adjudication file.

The `gold` block used to hold unreviewed pre-fill, which meant the reference and
the thing being measured were the same object: agreement with it proved only
that the classifier repeats itself. This applies `adjudication.yaml` instead, so
the reference is a judgement about each tag with a recorded reason.

Idempotent: run it again after editing the adjudication and the labels follow.
The article text, the `system` block and `review` are untouched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
GOLD = HERE / "test" / "articles"
# Adjudication is retired; the file is kept as provenance for the design doc.
ADJUDICATION = ROOT / "docs" / "archive" / "adjudication.yaml"
RUN = ROOT / "research" / "test_results"

sys.path.insert(0, str(HERE))
from verbatim import snap  # noqa: E402


def latest_run() -> dict:
    """Load the most recent gold run as the starting point for the labels.

    Returns:
        Mapping of article id -> classifier output.
    """
    runs = sorted(RUN.glob("gold_run_*.json"))
    if not runs:
        raise SystemExit("no run in research/test_results to adjudicate")
    blob = json.loads(runs[-1].read_text())
    print(f"base run: {runs[-1].name}")
    return {r["id"]: r["run"] for r in blob["results"] if "run" in r}


def apply(tags: list[dict], verdicts: list[dict], text: str) -> list[dict]:
    """Apply one article's verdicts to one axis of tags.

    Args:
        tags: The classifier's tags for this axis.
        verdicts: Adjudication entries for this axis.
        text: Article text, for verifying any quote the adjudicator supplies.

    Returns:
        The adjudicated tags.
    """
    by_id = {t["id"]: dict(t) for t in tags}
    for v in verdicts:
        tid, verdict = v["id"], v["verdict"]
        if verdict == "drop":
            by_id.pop(tid, None)
            continue
        if verdict == "add":
            by_id[tid] = {k: v[k] for k in
                          ("id", "sign", "magnitude", "confidence", "quote") if k in v}
        elif verdict == "adjust":
            tag = by_id.setdefault(tid, {"id": tid})
            tag.update(v.get("to", {}))
            if "quote" in v:
                tag["quote"] = v["quote"]
        # `confirm` keeps the classifier's tag as it stands.

        # An adjudicator-supplied quote goes through the same gate as a model's.
        if tid in by_id and "quote" in by_id[tid]:
            fixed = snap(" ".join(by_id[tid]["quote"].split()), text)
            if fixed is None:
                raise SystemExit(
                    f"adjudicated quote for {tid} is not in the document -- "
                    "the adjudicator is held to the same rule as the model"
                )
            by_id[tid]["quote"] = fixed
    return [by_id[k] for k in sorted(by_id)]


def main() -> None:
    """Write the adjudicated labels into every gold article."""
    spec = yaml.safe_load(ADJUDICATION.read_text())
    runs = latest_run()
    changed = 0

    for path in sorted(GOLD.glob("*.json")):
        record = json.loads(path.read_text())
        aid = record["id"]
        base = runs.get(aid)
        if base is None:
            print(f"  {aid}: not in the run, left alone")
            continue
        entry = spec["articles"].get(aid, {})

        gold = {
            "event_type": entry.get("event_type", base["event_type"]),
            "mechanisms": apply(base.get("mechanisms", []),
                                entry.get("mechanisms", []), record["text"]),
            "categories": apply(base.get("categories", []),
                                entry.get("categories", []), record["text"]),
            "practices": apply(base.get("practices", []),
                               entry.get("practices", []), record["text"]),
            "notes": entry.get("note", ""),
        }
        record["gold"] = gold
        record["review"] = {
            "adjudicated": True,
            "adjudicator": spec["adjudicator"],
            "classifier": spec["classifier"],
            "runs_compared": spec["runs_compared"],
            "departures": sum(len(entry.get(k, [])) for k in
                              ("mechanisms", "categories", "practices"))
            + (1 if "event_type" in entry else 0),
            "why_event": entry.get("why_event", ""),
        }
        path.write_text(json.dumps(record, indent=2))
        changed += 1

    print(f"\nadjudicated labels written to {changed} files")


if __name__ == "__main__":
    main()
