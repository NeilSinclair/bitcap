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


class TestLabExposure:
    """The lab-keyed join: a holding exposed to one named lab directly.

    The point of the field is that it routes an announcement carrying no
    mechanism at all -- an Anthropic funding round tags nothing and still moves
    TeraWulf. These tests guard the two ways it would degrade silently: an edge
    that no longer resolves to a lab, and an exposure claim with no citation.
    """

    ENUMS = {
        "sign": ["positive", "negative", "mixed"],
        "magnitude": ["high", "medium", "low"],
        "confidence": ["high", "medium", "low"],
    }

    def good(self, **over):
        """One valid exposure entry, overridable field by field."""
        entry = {
            "lab": "anthropic",
            "kind": "equity",
            "sign": "positive",
            "magnitude": "high",
            "confidence": "high",
            "why": "A stake whose mark lands in the income statement.",
            "source": "https://www.sec.gov/example",
        }
        entry.update(over)
        return {"name": "Test Corp", "lab_exposure": [entry]}

    def test_a_well_formed_edge_passes(self):
        errors, warnings = validate.check_lab_exposure(
            self.good(), self.ENUMS, {"anthropic"})
        assert not errors and not warnings

    def test_an_unregistered_lab_is_dormant_not_an_error(self):
        """The exposure is real before the lab is ingestible. Warn, never fail."""
        errors, warnings = validate.check_lab_exposure(
            self.good(lab="microsoft"), self.ENUMS, {"anthropic"})
        assert not errors
        assert len(warnings) == 1 and "microsoft" in warnings[0]

    def test_adding_a_lab_to_the_register_activates_its_edges(self):
        """The property the design turns on: no code or config edit needed."""
        company = self.good(lab="microsoft")
        _, before = validate.check_lab_exposure(company, self.ENUMS, {"anthropic"})
        _, after = validate.check_lab_exposure(
            company, self.ENUMS, {"anthropic", "microsoft"})
        assert before and not after

    def test_an_uncited_exposure_is_an_error(self):
        entry = self.good()
        del entry["lab_exposure"][0]["source"]
        errors, _ = validate.check_lab_exposure(entry, self.ENUMS, {"anthropic"})
        assert any("source" in e for e in errors)

    def test_an_explicit_unverified_note_stands_in_for_a_source(self):
        entry = self.good()
        del entry["lab_exposure"][0]["source"]
        entry["lab_exposure"][0]["unverified"] = "Prose only; needs a filing."
        errors, _ = validate.check_lab_exposure(entry, self.ENUMS, {"anthropic"})
        assert not errors

    def test_an_unknown_kind_is_an_error(self):
        errors, _ = validate.check_lab_exposure(
            self.good(kind="vibes"), self.ENUMS, {"anthropic"})
        assert any("kind=" in e for e in errors)

    def test_an_edge_without_a_why_is_an_error(self):
        errors, _ = validate.check_lab_exposure(
            self.good(why=""), self.ENUMS, {"anthropic"})
        assert any("why" in e for e in errors)

    @pytest.mark.parametrize("field", ["sign", "magnitude", "confidence"])
    def test_an_off_vocabulary_value_is_an_error(self, field):
        errors, _ = validate.check_lab_exposure(
            self.good(**{field: "enormous"}), self.ENUMS, {"anthropic"})
        assert any(field in e for e in errors)

    def test_two_edges_of_the_same_kind_to_one_lab_are_a_duplicate(self):
        company = self.good()
        company["lab_exposure"].append(dict(company["lab_exposure"][0]))
        errors, _ = validate.check_lab_exposure(company, self.ENUMS, {"anthropic"})
        assert any("duplicate" in e for e in errors)

    def test_a_stake_and_a_contract_with_one_lab_coexist(self):
        """Different exposures with different failure modes, not a duplicate."""
        company = self.good()
        second = dict(company["lab_exposure"][0], kind="revenue_contract")
        company["lab_exposure"].append(second)
        errors, _ = validate.check_lab_exposure(company, self.ENUMS, {"anthropic"})
        assert not errors

    def test_the_committed_config_declares_the_known_lab_exposures(self):
        """Amazon, TeraWulf and IREN each carry a documented direct lab link.

        Recorded as prose in every one of them before this field existed, which
        meant no code could reach it.
        """
        comp = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text())
        by_ticker = {c["ticker"]: c for c in comp["companies"]}
        for ticker in ("AMZN", "WULF", "IREN"):
            assert by_ticker[ticker].get("lab_exposure"), f"{ticker}: no lab_exposure"

    def test_every_committed_edge_is_cited(self):
        """The citation guarantee, applied to the second join key too."""
        comp = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text())
        uncited = [
            (c["ticker"], x["lab"])
            for c in comp["companies"]
            for x in c.get("lab_exposure", [])
            if not x.get("source") and not x.get("unverified")
        ]
        assert not uncited, f"uncited lab exposure: {uncited}"
