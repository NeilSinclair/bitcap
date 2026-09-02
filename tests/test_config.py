"""Tests for the config layer's cross-file consistency.

`config/validate.py` already catches an id that exists on one side of a join and
not the other. Nothing ran it. It caught a real inconsistency only because it
happened to be invoked by hand -- an edit to companies.yaml succeeded while the
matching edit to mechanisms.yaml silently failed its own guard, and the two
files disagreed until someone thought to check.

These tests make that check part of the suite instead of part of the ritual.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "config"))
import validate  # noqa: E402


class TestTheRealConfig:
    """The committed config must be internally consistent at all times."""

    def test_validate_passes_on_the_committed_config(self):
        """The whole cross-file check, as a test rather than a manual step."""
        assert validate.main() == 0, "config/validate.py reported errors"

    def test_every_company_mechanism_exists_in_mechanisms_yaml(self):
        """The exact drift that slipped through: a link to a retired id."""
        mech = yaml.safe_load((ROOT / "config" / "mechanisms.yaml").read_text())
        comp = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text())
        valid = {m["id"] for m in mech["mechanisms"]}
        unknown = {
            (c["name"], m["id"])
            for c in comp["companies"]
            for m in c.get("mechanisms", [])
            if m["id"] not in valid
        }
        assert not unknown, f"company links to undefined mechanisms: {unknown}"

    def test_the_two_vocabularies_do_not_share_ids(self):
        """A tag id on both axes is ambiguous about which renderer produced it."""
        mech = yaml.safe_load((ROOT / "config" / "mechanisms.yaml").read_text())
        prac = yaml.safe_load((ROOT / "config" / "practices.yaml").read_text())
        overlap = {m["id"] for m in mech["mechanisms"]} & {
            p["id"] for p in prac["practices"]
        }
        assert not overlap, f"id defined on both axes: {overlap}"


class TestPracticesChecks:
    """The practices checks must actually fail on a broken file.

    A validator that cannot fail is worse than none: it reports success over a
    file nobody has checked.
    """

    @staticmethod
    def _write(tmp_path: Path, doc: dict) -> Path:
        (tmp_path / "practices.yaml").write_text(yaml.safe_dump(doc))
        return tmp_path

    @staticmethod
    def _good() -> dict:
        return {
            "version": 1,
            "practices": [
                {
                    "id": "model_capability",
                    "label": "L",
                    "description": "D",
                    "adopt_note": "A",
                    "dimensions": {"coding": "Software engineering."},
                }
            ],
            "enums": {
                "action": {"adopt": "x"},
                "impact": {"high": "x"},
                "confidence": {"high": "x"},
            },
            "required_per_tag": ["id", "quote"],
            "required_per_tag_model_capability": ["dimensions"],
        }

    def test_a_well_formed_file_passes(self, tmp_path):
        errors, _ = validate.check_practices(self._write(tmp_path, self._good()), set())
        assert errors == []

    def test_missing_file_is_an_error(self, tmp_path):
        errors, _ = validate.check_practices(tmp_path, set())
        assert any("missing" in e for e in errors)

    def test_id_shared_with_a_mechanism_is_an_error(self, tmp_path):
        """The join cannot tell which vocabulary an ambiguous id came from."""
        errors, _ = validate.check_practices(
            self._write(tmp_path, self._good()), {"model_capability"}
        )
        assert any("also defined in mechanisms.yaml" in e for e in errors)

    def test_duplicate_practice_id_is_an_error(self, tmp_path):
        doc = self._good()
        doc["practices"].append(dict(doc["practices"][0]))
        errors, _ = validate.check_practices(self._write(tmp_path, doc), set())
        assert any("duplicate id" in e for e in errors)

    @pytest.mark.parametrize("field", ["label", "description", "adopt_note"])
    def test_a_practice_without_its_prose_is_an_error(self, tmp_path, field):
        doc = self._good()
        doc["practices"][0][field] = ""
        errors, _ = validate.check_practices(self._write(tmp_path, doc), set())
        assert any(field in e for e in errors)

    def test_model_capability_without_dimensions_is_an_error(self, tmp_path):
        """An undifferentiated 'it got better' is what this tag replaced."""
        doc = self._good()
        doc["practices"][0]["dimensions"] = {}
        errors, _ = validate.check_practices(self._write(tmp_path, doc), set())
        assert any("dimensions" in e for e in errors)

    def test_a_dimension_without_a_definition_is_an_error(self, tmp_path):
        doc = self._good()
        doc["practices"][0]["dimensions"]["latency"] = ""
        errors, _ = validate.check_practices(self._write(tmp_path, doc), set())
        assert any("latency" in e for e in errors)

    def test_dropping_the_quote_requirement_is_an_error(self, tmp_path):
        """The citation guarantee has to hold on the AI-team axis too."""
        doc = self._good()
        doc["required_per_tag"] = ["id"]
        errors, _ = validate.check_practices(self._write(tmp_path, doc), set())
        assert any("citation guarantee" in e for e in errors)

    @pytest.mark.parametrize("enum", ["action", "impact", "confidence"])
    def test_an_empty_enum_is_an_error(self, tmp_path, enum):
        doc = self._good()
        doc["enums"][enum] = {}
        errors, _ = validate.check_practices(self._write(tmp_path, doc), set())
        assert any(enum in e for e in errors)

    @pytest.mark.parametrize(
        "key", ["version", "practices", "enums", "required_per_tag"]
    )
    def test_a_missing_top_level_block_is_an_error(self, tmp_path, key):
        doc = self._good()
        del doc[key]
        errors, _ = validate.check_practices(self._write(tmp_path, doc), set())
        assert any(key in e for e in errors)


class TestGoldReadmeVocabulary:
    """The gold README's vocabulary is what a human labels against.

    It was hand-pasted and drifted: it listed `open_weights_release` after that
    mechanism was retired, carried the pre-narrowing `inference_cost_down`
    definition, and had never heard of the practice axis. A reviewer labelling
    against a stale vocabulary produces labels the pipeline cannot be scored on.
    """

    @staticmethod
    def _rendered():
        import sys
        sys.path.insert(0, str(ROOT / "research" / "announcements"))
        from gold_readme_vocab import HEADING, README, render
        return HEADING, README.read_text(), render()

    def test_the_readme_matches_the_config(self):
        heading, text, rendered = self._rendered()
        assert heading in text, "the generated section was removed"
        assert text.split(heading)[1] == rendered.split(heading)[1], (
            "gold/README.md has drifted from config/ -- "
            "run research/announcements/gold_readme_vocab.py"
        )

    def test_no_retired_mechanism_survives_in_the_readme(self):
        _, text, _ = self._rendered()
        import yaml
        live = {m["id"] for m in
                yaml.safe_load((ROOT / "config" / "mechanisms.yaml").read_text())["mechanisms"]}
        for retired in ("open_weights_release", "ai_capex_cycle_turns"):
            assert retired not in live, "test is stale: that mechanism is live again"
            assert f"`{retired}`" not in text

    def test_the_practice_axis_is_documented(self):
        _, text, _ = self._rendered()
        for practice in ("model_capability", "orchestration", "evaluation",
                         "serving_efficiency", "integration"):
            assert f"`{practice}`" in text

