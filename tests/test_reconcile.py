"""Tests for the gold/gold-fable reconciliation worksheet and its applier.

The invariant that matters: answering every row `fable` must reproduce
gold-fable exactly. If it does not, the worksheet is not a faithful enumeration
of the differences, and a partly-answered sheet would silently mis-apply.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "announcements"))

from apply_reconcile import PLURAL, apply_row, resolve  # noqa: E402
from build_reconcile import rows_for  # noqa: E402
from score_announcements import ai_score_of, score_of  # noqa: E402

REF = ROOT / "research" / "announcements" / "gold-fable" / "articles"
TEST = ROOT / "research" / "announcements" / "test" / "articles"
SHEET = ROOT / "research" / "announcements" / "gold-fable" / "reconcile.yaml"


@pytest.fixture(scope="module")
def loaded():
    return yaml.safe_load(SHEET.read_text())


@pytest.fixture(scope="module")
def sheet(loaded):
    return loaded["rows"]


@pytest.fixture(scope="module")
def rules():
    return yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())


def labels_only(block):
    """The decision-bearing fields, excluding free-text commentary.

    `notes` is the labeller's prose and carries no decision, so a difference
    there is not something the worksheet should ask anyone to adjudicate.
    """
    return {k: v for k, v in block.items() if k != "notes"}


@pytest.fixture(scope="module")
def full_rows(rules):
    """Every difference, computed fresh -- independent of the sheet on disk.

    The sheet may legitimately be a subset (build_reconcile --skip-presence),
    so the exhaustiveness invariant is tested here rather than against the file.
    """
    out = {}
    for path in sorted(REF.glob("*.json")):
        ref = json.loads(path.read_text())
        test = json.loads((TEST / path.name).read_text())
        rows = rows_for(ref["id"], ref["gold"], test["gold"], ref["title"], rules)
        if rows:
            out[ref["id"]] = rows
    return out


def test_every_differing_article_produces_rows(full_rows):
    for path in REF.glob("*.json"):
        ref = json.loads(path.read_text())["gold"]
        test = json.loads((TEST / path.name).read_text())["gold"]
        if labels_only(ref) != labels_only(test):
            assert path.stem in full_rows, f"article {path.stem} differs but has no rows"


def test_sheet_records_what_it_was_built_against(loaded):
    """Decisions only mean something against a named test side."""
    assert loaded.get("source"), "worksheet does not name its test side"


def test_sheet_is_a_subset_when_built_from_test_articles(loaded, sheet, full_rows):
    if not loaded["source"].startswith("test/articles"):
        pytest.skip(f"sheet built against {loaded['source']}")
    keys = {(r["article"], r["kind"], r["tag"], r["field"])
            for rows in full_rows.values() for r in rows}
    for row in sheet:
        k = (row["article"], row["kind"], row["tag"], row["field"])
        assert k in keys, f"{row['id']} is not a real difference"


def test_ids_are_unique_and_fields_present(sheet):
    assert len({r["id"] for r in sheet}) == len(sheet)
    for row in sheet:
        for field in ("article", "kind", "tag", "field", "fable", "gold",
                      "impact", "decision"):
            assert field in row, f"{row['id']} missing {field}"
        assert row["fable"] != row["gold"], f"{row['id']} records a non-difference"


def test_kinds_are_resolvable(sheet):
    for row in sheet:
        if row["kind"] != "event_type":
            assert row["kind"] in PLURAL, f"{row['id']} has unusable kind {row['kind']!r}"


def test_rows_are_sorted_by_impact(sheet):
    weights = [max(abs(r["impact"]["score"]), abs(r["impact"]["ai_score"]))
               for r in sheet]
    assert weights == sorted(weights, reverse=True)


def test_unanswered_row_defaults_to_gold():
    assert resolve({"decision": None}) == "gold"
    assert resolve({"decision": ""}) == "gold"
    assert resolve({"decision": "fable"}) == "fable"
    assert resolve({"decision": "medium"}) == "medium"


def test_answering_everything_fable_reproduces_gold_fable(full_rows, rules):
    """The full row set must enumerate the differences exhaustively."""
    for art_id, rows in full_rows.items():
        doc = json.loads((TEST / f"{art_id}.json").read_text())
        ref = json.loads((REF / f"{art_id}.json").read_text())["gold"]
        block = copy.deepcopy(doc["gold"])
        failures = []
        for row in rows:
            apply_row(block, dict(row, decision="fable"), ref, failures, doc["text"])
        assert not failures, failures
        assert score_of(block, rules) == score_of(ref, rules), f"article {art_id}"
        assert ai_score_of(block, rules) == ai_score_of(ref, rules), f"article {art_id}"


def test_answering_everything_gold_changes_nothing(sheet):
    by_article = {}
    for row in sheet:
        by_article.setdefault(row["article"], []).append(row)

    for art_id, rows in by_article.items():
        doc = json.loads((TEST / f"{art_id}.json").read_text())
        ref = json.loads((REF / f"{art_id}.json").read_text())["gold"]
        block = copy.deepcopy(doc["gold"])
        for row in rows:
            apply_row(block, dict(row, decision="gold"), ref, [], doc["text"])
        assert block == doc["gold"], f"article {art_id} changed on a no-op"


def test_literal_override_is_applied():
    doc = json.loads((TEST / "23.json").read_text())
    ref = json.loads((REF / "23.json").read_text())["gold"]
    block = copy.deepcopy(doc["gold"])
    row = {"id": "x", "article": "23", "kind": "mechanism",
           "tag": "lab_capital_access", "field": "confidence", "decision": "low"}
    apply_row(block, row, ref, [], doc["text"])
    tag = next(t for t in block["mechanisms"] if t["id"] == "lab_capital_access")
    assert tag["confidence"] == "low"


def test_added_tag_quote_is_verified_against_this_article():
    """A tag pulled from gold-fable must still quote the article it lands in."""
    doc = json.loads((TEST / "19.json").read_text())
    ref = copy.deepcopy(json.loads((REF / "19.json").read_text())["gold"])
    for tag in ref["mechanisms"]:
        tag["quote"] = "a sentence that appears in no article anywhere"
    block = copy.deepcopy(doc["gold"])
    failures = []
    row = {"id": "x", "article": "19", "kind": "mechanism", "tag": "capability_jump",
           "field": "presence", "gold": "absent", "decision": "fable"}
    apply_row(block, row, ref, failures, doc["text"])
    assert failures, "an unverifiable quote was written without complaint"
