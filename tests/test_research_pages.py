"""Tests for the research page builders.

These pages are how the corpus gets judged by eye, so the failure they must not
have is a silent one: a person or paper that quietly does not render, an
employment tier the renderer has never heard of, or a caveat that survives from
the lab the builder was originally written for onto a lab where it is false.

The last of those is the reason this file exists. Both GitHub and papers
builders were written against Anthropic-shaped data and hardcoded its domain,
its work-handle convention and its mirror list. Run against Mistral -- which has
no commit-email evidence at all -- the page rendered confident prose about
evidence that does not exist.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "github"))
sys.path.insert(0, str(ROOT / "research" / "papers"))
sys.path.insert(0, str(ROOT / "research"))

import build_corpus_survey  # noqa: E402
import build_github_page as ghp  # noqa: E402
import build_lab_authors_page as lap  # noqa: E402
import enrich_github  # noqa: E402

DOCS = ROOT / "research" / "docs"
ORGS = yaml.safe_load((ROOT / "config" / "github_sources.yaml").read_text())["orgs"]


def strip_tags(markup: str) -> str:
    """Reduce rendered HTML to its visible text, for prose assertions."""
    text = re.sub(r"<style.*?</style>", " ", markup, flags=re.S)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))


class TestGithubSourcesConfig:
    """The config is the register; a missing field breaks a builder at runtime."""

    def test_every_org_has_the_fields_both_builders_read(self):
        missing = {
            org: [k for k in ("lab", "label", "company_pattern") if k not in cfg]
            for org, cfg in ORGS.items()
        }
        assert not any(missing.values()), f"incomplete org config: {missing}"

    @pytest.mark.parametrize("org", sorted(ORGS))
    def test_company_pattern_compiles(self, org):
        """A regex typo here would surface as a crash mid-enrichment run."""
        re.compile(ORGS[org]["company_pattern"])

    def test_company_pattern_does_not_match_an_unrelated_company(self):
        """The xAI pattern is the fragile one: 'XAI' also abbreviates
        'explainable AI', which appears in real profile company fields."""
        pattern = re.compile(ORGS["xai-org"]["company_pattern"], re.I)
        assert pattern.search("xAI")
        assert pattern.search("x.ai")
        assert not pattern.search("Explainable AI Lab")
        assert not pattern.search("Toxaid Pharmaceuticals")

    def test_each_pattern_matches_its_own_label(self):
        """Cheap self-consistency check: the lab's own name should match."""
        for org, cfg in ORGS.items():
            if org in ("xai-org",):  # label "xAI" vs pattern needing a word break
                continue
            assert re.search(cfg["company_pattern"], cfg["label"], re.I), org


class TestEnrichConfigWiring:
    """Enrichment used to carry a hardcoded two-lab dict; it now reads config."""

    def test_pattern_comes_from_config_not_code(self):
        assert enrich_github.company_pattern("mistralai") == ORGS["mistralai"]["company_pattern"]

    def test_unknown_org_exits_rather_than_defaulting(self):
        """Defaulting silently scored a new org as if it were Anthropic."""
        with pytest.raises(SystemExit):
            enrich_github.company_pattern("not-a-real-org")

    def test_org_wide_evidence_is_worth_fetching(self):
        """The regression this catches: `confirmed_org_wide` post-dates
        enrich_github, so a one-commit DeepMind or Meta staffer -- evidenced,
        but below the commit threshold -- was skipped entirely."""
        person = {"employment": "confirmed_org_wide", "commits": 1}
        assert enrich_github.worth_fetching(person, min_commits=2)

    def test_every_tier_is_counted_in_the_totals(self):
        """A tier absent from TIERS reports as zero, not as missing."""
        assert set(enrich_github.EVIDENCED) <= set(enrich_github.TIERS)
        assert "confirmed_org_wide" in enrich_github.TIERS


class TestGithubPageBuilder:
    def test_every_employment_tier_has_an_evidence_caption(self):
        """A tier missing from EVIDENCE is a KeyError mid-render, not a blank."""
        for org in ORGS:
            path = DOCS / f"github_enriched_{org}.json"
            if not path.exists():
                path = DOCS / f"github_people_{org}.json"
            tiers = {p["employment"] for p in json.loads(path.read_text())["people"]}
            assert tiers <= set(ghp.EVIDENCE), f"{org}: uncaptioned {tiers - set(ghp.EVIDENCE)}"

    def test_domain_is_normalised_to_a_list(self):
        """Config carries a string, a list, or null; the builder needs one shape."""
        assert ghp.load_org("anthropics")["domain"] == ["anthropic.com"]
        assert ghp.load_org("facebookresearch")["domain"] == ["meta.com", "fb.com"]
        assert ghp.load_org("mistralai")["domain"] == []

    @pytest.mark.parametrize("org", sorted(ORGS))
    def test_page_renders_every_person(self, org):
        page = ghp.build(org)
        path = DOCS / f"github_enriched_{org}.json"
        if not path.exists():
            path = DOCS / f"github_people_{org}.json"
            assert "Profile enrichment has not been run" in page
        expected = len(json.loads(path.read_text())["people"])
        assert len(re.findall(r'<tr class="p"', page)) == expected

    def test_no_domain_org_does_not_claim_commit_evidence(self):
        """Mistral evidences nobody by email. The Anthropic-shaped prose used
        to assert an '@None' address was direct evidence."""
        text = strip_tags(ghp.build("mistralai"))
        assert "no commit-email evidence" in text
        assert "@None" not in text
        assert "outranks everything else" not in text

    def test_shared_domain_org_is_not_described_as_lab_specific(self):
        """An @google.com commit proves Alphabet, not DeepMind. Conflating the
        two is the single easiest way to overstate this register."""
        text = strip_tags(ghp.build("google-deepmind"))
        assert "covers the whole parent company" in text
        assert "lab-specific evidence" not in text

    def test_lab_owned_domain_is_described_as_such(self):
        text = strip_tags(ghp.build("anthropics"))
        assert "lab-specific evidence" in text
        assert "@anthropic.com" in text

    def test_work_handle_prose_only_appears_where_a_convention_exists(self):
        """Only Anthropic and OpenAI have one; claiming it elsewhere invents
        a signal the register never used."""
        assert "work-handle convention</span> is used" in ghp.build("anthropics")
        assert "No work-handle convention is known" in strip_tags(ghp.build("deepseek-ai"))

    def test_filters_are_offered_only_for_non_empty_tiers(self):
        """A row of '(0)' buttons reads as missing data, not as an
        inapplicable signal."""
        page = ghp.build("mistralai")
        offered = set(re.findall(r'<button data-f="([a-z_]+)"', page))
        assert offered == {"all", "staff", "profile", "unknown"}

    def test_staff_filter_includes_org_wide_evidence(self):
        """Staff excluding confirmed_org_wide would show Meta as 5 people."""
        page = ghp.build("meta-llama")
        assert '"confirmed_org_wide"' in page.split("<script>")[1]


class TestLabAuthorsPageBuilder:
    @pytest.mark.parametrize("lab", sorted(lap.LABS))
    def test_renders_every_person_and_paper(self, lab):
        reg = json.loads((DOCS / f"{lab}_contributors.json").read_text())
        page = lap.render(lab)
        assert len(re.findall(r'<details class="row"', page)) == len(reg["people"])
        assert len(re.findall(r'<tr><td class="dt">', page)) == len(reg["papers"])

    @pytest.mark.parametrize("lab", sorted(lap.LABS))
    def test_every_paper_links_its_primary_source(self, lab):
        """No citation, no insight. A paper with no resolvable source URL is a
        gap the page must not hide."""
        reg = json.loads((DOCS / f"{lab}_contributors.json").read_text())
        assert all(p.get("source_url") for p in reg["papers"])
        assert len(re.findall(r">arXiv</a>", lap.render(lab))) == len(reg["papers"])

    @pytest.mark.parametrize("lab", sorted(lap.LABS))
    def test_each_lab_uses_its_own_landing_page_key(self, lab):
        """DeepMind, Meta and Mistral each store it under a different key;
        reading the wrong one silently drops the lab-side link."""
        reg = json.loads((DOCS / f"{lab}_contributors.json").read_text())
        key = lap.LABS[lab]["lab_url_key"]
        assert all(key in p for p in reg["papers"])
        assert len(re.findall(r">lab</a>", lap.render(lab))) == len(reg["papers"])

    def test_resolution_covers_every_value_present_in_the_data(self):
        for lab in lap.LABS:
            reg = json.loads((DOCS / f"{lab}_contributors.json").read_text())
            for p in reg["papers"]:
                assert p.get("resolution") in lap.RESOLUTION

    def test_classify_separates_staff_from_external(self):
        assert lap.classify({"is_lab_staff": True, "is_fellow": False})[0] == "staff"
        assert lap.classify({"is_lab_staff": False, "is_fellow": True})[0] == "fellow"
        assert lap.classify({"is_lab_staff": False, "is_fellow": False})[0] == "external"

    def test_affiliation_table_excludes_the_lab_itself(self):
        """The table is a shortlist of sources to add. The lab is already
        tracked, so listing it is noise that would top every ranking."""
        rows = lap.affiliation_rows(
            [
                {"affiliations": ["Google DeepMind", "Google DeepMind, London, UK"]},
                {"affiliations": ["University of Oxford"]},
            ],
            "Google DeepMind",
        )
        assert "Oxford" in rows
        assert "DeepMind" not in rows

    def test_affiliation_table_handles_a_lab_with_no_external_coauthors(self):
        """Mistral's two papers are entirely in-house; an empty ranking must
        say so rather than divide by a zero maximum."""
        rows = lap.affiliation_rows([{"affiliations": ["Mistral AI"]}], "Mistral AI")
        assert "No external co-authoring institutions" in rows

    def test_person_with_no_affiliation_is_stated_not_blank(self):
        page = lap.render("meta")
        assert "no affiliation stated" in page


class TestCorpusSurvey:
    def test_normalises_both_register_schemas(self):
        """Six registers, two schemas. The survey compares them side by side,
        so a field read from the wrong one shows a plausible wrong number."""
        assert build_corpus_survey.load_papers("google-deepmind")["staff"] == 38
        assert build_corpus_survey.load_papers("anthropic")["staff"] == 117

    def test_absent_distinction_is_none_not_zero(self):
        """The OpenAI and DeepSeek registers hold only the lab's own authors,
        so they cannot express a staff/external split. Reporting 0 would read
        as 'checked, found none'."""
        assert build_corpus_survey.load_papers("openai")["staff"] is None
        assert build_corpus_survey.load_papers("deepseek")["staff"] is None

    def test_lab_with_no_papers_leg_returns_none(self):
        assert build_corpus_survey.load_papers("xai") is None

    def test_primary_source_found_under_all_three_register_shapes(self):
        """The three schemas put the resolvable link in different keys. Reading
        only `source_url` reported Anthropic, OpenAI and DeepSeek as citing
        nothing, which is false -- they cite it under `url`."""
        assert build_corpus_survey.primary_source(
            {"source_url": "https://arxiv.org/html/1", "url": "https://lab/x"}
        ) == "https://arxiv.org/html/1"
        assert build_corpus_survey.primary_source(
            {"url": "https://arxiv.org/abs/2303.08774v6"}
        ) == "https://arxiv.org/abs/2303.08774v6"
        assert build_corpus_survey.primary_source({"meta_url": "https://m/x"}) == "https://m/x"
        assert build_corpus_survey.primary_source({"title": "no link"}) is None

    @pytest.mark.parametrize(
        "lab", ["anthropic", "openai", "deepseek", "google-deepmind", "meta-ai", "mistral"]
    )
    def test_every_register_cites_a_source_for_every_paper(self, lab):
        """The project's first non-negotiable: no citation, no insight. If a
        register ever drops below full coverage this must fail loudly."""
        p = build_corpus_survey.load_papers(lab)
        assert p["resolved"] == p["papers"], f"{lab}: {p['resolved']}/{p['papers']}"

    def test_cite_kind_names_the_venue_type(self):
        assert build_corpus_survey.cite_kind(
            [{"source_url": "u", "arxiv_id": "1"}]
        ) == "arXiv + lab page"
        assert build_corpus_survey.cite_kind([{"arxiv_id": "1"}]) == "arXiv"
        assert build_corpus_survey.cite_kind([{"url": "u"}]) == "publishing venue"

    def test_survey_does_not_claim_a_register_records_no_source(self):
        """The page said 'not recorded' for three registers that do record it."""
        assert "not recorded" not in build_corpus_survey.build()

    def test_evidence_note_distinguishes_the_three_cases(self):
        assert build_corpus_survey.evidence_note({"domain": None}, {})[0] == "weak"
        assert build_corpus_survey.evidence_note(
            {"domain": "google.com", "domain_shared": True}, {}
        )[0] == "mid"
        assert build_corpus_survey.evidence_note(
            {"domain": "anthropic.com", "domain_shared": False}, {}
        )[0] == "ok"

    def test_page_names_every_registered_lab(self):
        page = build_corpus_survey.build()
        labs = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())["labs"]
        for lab in labs:
            assert lab["label"] in page, lab["id"]

    def test_page_flags_unenriched_orgs_rather_than_showing_a_silent_zero(self):
        page = build_corpus_survey.build()
        unenriched = [
            org for org in ORGS if not (DOCS / f"github_enriched_{org}.json").exists()
        ]
        for org in unenriched:
            assert org in page
        if unenriched:
            assert "have had no profile pass run" in page

    def test_code_tags_in_the_gap_note_are_not_double_escaped(self):
        """They were: the org list was joined with markup and then escaped,
        so the page printed a literal &lt;/code&gt;."""
        assert "&lt;/code&gt;" not in build_corpus_survey.build()
