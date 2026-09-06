"""The source registry, built from the real config files.

The silent failure this catches: a lab added to one register and not another.
Coverage is per (lab, leg) and the three YAML files are edited separately, so a
lab can easily gain an announcements entry and never gain a papers one. Nothing
errors — the pipeline just quietly ingests less than anyone thinks it does, and
the missing leg looks identical to a leg that was deliberately ruled out.

The counts pinned here are the register as decided in D7/D14 and must be updated
deliberately alongside any register change, in the same spirit as the counts in
`tests/test_load.py`.
"""

import sys
from pathlib import Path

import pytest

from app.pipeline import registry as r

sys.path.insert(0, str(Path(__file__).parent.parent / "config"))
import validate  # noqa: E402

DEEP_COVERAGE_LABS = {
    "anthropic", "openai", "deepseek", "google-deepmind", "mistral", "meta-ai", "xai",
}


class TestTheRegisterIsComplete:
    def test_every_deep_coverage_lab_has_an_announcements_source(self):
        assert {s.id for s in r.announcement_sources()} == DEEP_COVERAGE_LABS

    def test_every_deep_coverage_lab_is_accounted_for_in_papers(self):
        """Covered or explicitly ruled out — but never simply absent."""
        assert {s.id for s in r.paper_sources()} == DEEP_COVERAGE_LABS

    def test_xai_papers_is_disabled_rather_than_missing(self):
        """D16/D17: xAI publishes no findable papers. A finding, not a gap."""
        xai = next(s for s in r.paper_sources() if s.id == "xai")
        assert xai.enabled is False
        assert "no corpus" in xai.config["notes"].lower() or "not a gap" in xai.config["notes"].lower()

    def test_every_other_lab_has_an_enabled_papers_harvester(self):
        enabled = {s.id for s in r.paper_sources() if s.enabled}
        assert enabled == DEEP_COVERAGE_LABS - {"xai"}

    def test_github_covers_every_lab_including_metas_two_orgs(self):
        sources = r.github_sources()
        labs = {s.config["lab"] for s in sources}
        assert labs == DEEP_COVERAGE_LABS
        meta_orgs = {s.id for s in sources if s.config["lab"] == "meta-ai"}
        assert meta_orgs == {"facebookresearch", "meta-llama"}

    def test_papers_labs_reference_real_announcement_labs(self):
        """A typo in papers_sources.yaml would create a lab that ingests nothing."""
        known = {s.id for s in r.announcement_sources()}
        assert {s.id for s in r.paper_sources()} <= known


class TestPapersEntriesAreCallable:
    @pytest.mark.parametrize("source", [s for s in r.paper_sources() if s.enabled],
                             ids=lambda s: s.id)
    def test_declared_module_and_entry_exist(self, source):
        """Config naming a function that does not exist fails at runtime, per lab.

        Checked here instead, so the whole register is verified in one cheap
        test rather than one lab at a time in production.
        """
        import importlib

        import app.pipeline.adapters  # noqa: F401  — puts research/ on sys.path

        module = importlib.import_module(source.config["module"])
        entry = getattr(module, source.config["entry"], None)
        assert callable(entry), f"{source.id}: {source.config['module']}.{source.config['entry']}"

    @pytest.mark.parametrize("source", [s for s in r.paper_sources() if s.enabled],
                             ids=lambda s: s.id)
    def test_declared_args_are_accepted_by_the_entry(self, source):
        """`args` must match the real signature, or the call raises TypeError."""
        import importlib
        import inspect

        import app.pipeline.adapters  # noqa: F401

        module = importlib.import_module(source.config["module"])
        entry = getattr(module, source.config["entry"])
        params = set(inspect.signature(entry).parameters)
        declared = set(source.config.get("args") or ())
        assert declared <= params, f"{source.id}: {declared - params} not in {params}"

    @pytest.mark.parametrize("source", [s for s in r.paper_sources() if s.enabled],
                             ids=lambda s: s.id)
    def test_url_field_is_declared(self, source):
        """The citation field differs per lab and must never default."""
        assert source.config["url_field"] in {"url", "meta_url", "announcement_url"}


class TestLoadSources:
    def test_returns_every_leg_in_stage_order(self):
        sources = r.load_sources()
        stages = [s.stage for s in sources]
        assert stages == sorted(stages)
        assert {s.leg for s in sources} == set(r.LEGS)

    def test_announcements_run_before_papers(self):
        """Mistral sources its candidate titles from the announcements corpus."""
        assert r.STAGES["announcements"] < r.STAGES["papers"]

    def test_a_single_leg_can_be_selected(self):
        sources = r.load_sources(legs=("announcements",))
        assert {s.leg for s in sources} == {"announcements"}

    def test_an_unknown_leg_raises_rather_than_ingesting_nothing(self):
        with pytest.raises(ValueError, match="unknown leg"):
            r.load_sources(legs=("twitter",))

    def test_source_keys_are_unique(self):
        keys = [s.key for s in r.load_sources()]
        assert len(keys) == len(set(keys))


class TestUnresolvedReportingCoverage:
    """How much of the papers register can report its own gaps: two labs of six.

    Not a passing grade — a pinned limitation. `unresolved_items` is a floor,
    not a census, because only the harvesters returning `papers_and_unresolved`
    hand back what they could not resolve. The other four drop failures
    internally: observed live, DeepMind lost two papers (a 403 and a 404) and
    Anthropic one (unparseable byline), and none left a record.

    This test exists so that number cannot drift silently in either direction —
    upward without the config saying so, or downward without anyone noticing the
    register got blinder.
    """

    def test_exactly_two_labs_can_report_unresolved_candidates(self):
        reporting = {
            s.id for s in r.paper_sources()
            if s.enabled and s.config.get("returns") == "papers_and_unresolved"
        }
        assert reporting == {"meta-ai", "mistral"}

    def test_the_rest_return_a_bare_list_and_lose_their_failures(self):
        silent = {
            s.id for s in r.paper_sources()
            if s.enabled and s.config.get("returns") != "papers_and_unresolved"
        }
        assert silent == {"anthropic", "openai", "deepseek", "google-deepmind"}

    def test_the_config_documents_the_gap(self):
        """A limitation nobody can find is not documented."""
        text = (r.CONFIG / "papers_sources.yaml").read_text(encoding="utf-8")
        assert "floor, not a census" in text


class TestTheNewConfigFilesAreValidated:
    """Both new config files fail *silently* when wrong, which is why they need
    a validator rather than a comment.

    Every value in `pipeline.yaml` is read with a `.get(..., default)` or fed
    straight into a comparison, so a typo does not raise — it changes behaviour.
    These tests assert the validator catches each named failure AND passes the
    real files, because a validator that rejects the committed config is just as
    useless as one that accepts anything.
    """

    def _config_dir(self, tmp_path, pipeline=None, papers=None):
        import yaml as _yaml

        base = _yaml.safe_load((r.CONFIG / "pipeline.yaml").read_text())
        if pipeline:
            for section, changes in pipeline.items():
                base[section] = {**base.get(section, {}), **changes}
        (tmp_path / "pipeline.yaml").write_text(_yaml.safe_dump(base))

        papers_doc = _yaml.safe_load((r.CONFIG / "papers_sources.yaml").read_text())
        if papers:
            papers_doc["labs"][0].update(papers)
        (tmp_path / "papers_sources.yaml").write_text(_yaml.safe_dump(papers_doc))
        return tmp_path

    def test_the_committed_config_passes(self):
        assert validate.check_pipeline(r.CONFIG) == []
        assert validate.check_papers_sources(r.CONFIG, DEEP_COVERAGE_LABS) == []

    def test_a_capitalised_band_is_rejected(self, tmp_path):
        """`High` matches no row, so zero content alerts are raised — forever."""
        errors = validate.check_pipeline(self._config_dir(tmp_path, {"alerts": {"content_band": "High"}}))
        assert any("content_band" in e for e in errors)

    def test_an_unknown_cadence_leg_is_rejected(self, tmp_path):
        """A mistyped leg name means that leg silently never runs."""
        errors = validate.check_pipeline(self._config_dir(tmp_path, {"cadence": {"twitter": 2}}))
        assert any("twitter" in e for e in errors)

    def test_a_month_ceiling_below_the_run_ceiling_is_rejected(self, tmp_path):
        errors = validate.check_pipeline(self._config_dir(tmp_path, {"budget": {"per_month_usd": 0.5}}))
        assert any("per_month_usd" in e for e in errors)

    def test_an_unknown_alert_channel_is_rejected(self, tmp_path):
        errors = validate.check_pipeline(self._config_dir(tmp_path, {"alerts": {"channel": "carrier"}}))
        assert any("channel" in e for e in errors)

    def test_a_listing_window_above_the_lookup_window_is_rejected(self, tmp_path):
        """Legal YAML that throws away the whole point of splitting the two.

        A listing is how a new paper is found at all; a lookup asks about a
        title we already have. Set them equal and DeepSeek is back to a
        fortnight's lag on its own papers, with nothing failing.
        """
        errors = validate.check_pipeline(
            self._config_dir(tmp_path, {"fetch": {"listing_ttl_hours": 336}}))
        assert any("listing_ttl_hours" in e for e in errors)

    def test_a_zero_listing_window_is_rejected(self, tmp_path):
        errors = validate.check_pipeline(
            self._config_dir(tmp_path, {"fetch": {"listing_ttl_hours": 0}}))
        assert any("listing_ttl_hours" in e for e in errors)

    def test_jitter_outside_a_fraction_is_rejected(self, tmp_path):
        """1.0 doubles a window nobody asked to double; negative dithers below
        the configured floor, making every URL quietly fresher than the file says."""
        for bad in (1.0, -0.1):
            errors = validate.check_pipeline(
                self._config_dir(tmp_path, {"fetch": {"discovery_ttl_jitter": bad}}))
            assert any("discovery_ttl_jitter" in e for e in errors), bad

    def test_a_mistyped_url_field_is_rejected(self, tmp_path):
        """Every paper would resolve to a null citation and be dropped, while
        the source still reported SUCCEEDED with a healthy items_seen."""
        errors = validate.check_papers_sources(
            self._config_dir(tmp_path, papers={"url_field": "ur1"}), DEEP_COVERAGE_LABS
        )
        assert any("url_field" in e for e in errors)

    def test_a_disabled_lab_must_say_why(self, tmp_path):
        """Otherwise a deliberate exclusion looks identical to an oversight."""
        errors = validate.check_papers_sources(
            self._config_dir(tmp_path, papers={"enabled": False, "notes": ""}),
            DEEP_COVERAGE_LABS,
        )
        assert any("no note" in e for e in errors)
