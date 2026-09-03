"""Tests for the generalized (v2) LLM byline extractor.

The silent failure this suite exists to catch: a lab-agnostic call silently
falling back to Anthropic's v1 prompt (or vice versa), or a `{lab_label}`
placeholder surviving unreplaced into a live request -- either would ship a
byline whose employment field means something other than what its schema
claims.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "papers"))

from llm_byline import HTML_BUDGET, SCHEMA, SCHEMA_V2, ExtractionError, build_prompt, prepare_html


class TestBuildPromptVersionSelection:
    def test_no_lab_label_uses_v1_prompt(self):
        _, user = build_prompt("<p>x</p>")
        assert "is_anthropic" in user
        assert "is_lab_staff" not in user

    def test_lab_label_uses_v2_prompt_and_interpolates(self):
        system, user = build_prompt("<p>x</p>", lab_label="Google DeepMind")
        assert "Google DeepMind" in user
        assert "{lab_label}" not in user
        assert "{lab_label}" not in system

    def test_html_is_always_interpolated(self):
        _, user = build_prompt("UNIQUE_MARKER_XYZ", lab_label="Mistral AI")
        assert "UNIQUE_MARKER_XYZ" in user
        assert "{html}" not in user

    def test_v1_and_v2_produce_different_prompts(self):
        _, v1_user = build_prompt("<p>x</p>")
        _, v2_user = build_prompt("<p>x</p>", lab_label="xAI")
        assert v1_user != v2_user


class TestSchemaVersions:
    """The whole point of v2 is that `is_lab_staff` never gets silently
    confused with v1's Anthropic-specific `is_anthropic` -- if a future edit
    ever makes the two schemas identical, that confusion has already
    happened.
    """

    def _author_fields(self, schema):
        return set(schema["properties"]["authors"]["items"]["properties"])

    def test_v1_schema_has_is_anthropic(self):
        assert "is_anthropic" in self._author_fields(SCHEMA)
        assert "is_lab_staff" not in self._author_fields(SCHEMA)

    def test_v2_schema_has_is_lab_staff_not_is_anthropic(self):
        assert "is_lab_staff" in self._author_fields(SCHEMA_V2)
        assert "is_anthropic" not in self._author_fields(SCHEMA_V2)

    def test_v2_author_required_fields_reference_is_lab_staff(self):
        required = SCHEMA_V2["properties"]["authors"]["items"]["required"]
        assert "is_lab_staff" in required
        assert "is_anthropic" not in required


class TestPrepareHtml:
    def test_short_page_not_truncated(self):
        html, truncated = prepare_html("<p>short</p>")
        assert truncated is False
        assert html == "<p>short</p>"

    def test_long_page_truncated_and_flagged(self):
        html, truncated = prepare_html("x" * 100_000)
        assert truncated is True
        assert len(html) < 100_000

    def test_scripts_and_comments_stripped(self):
        html, _ = prepare_html("<p>keep</p><script>evil()</script><!-- note -->")
        assert "evil()" not in html
        assert "note" not in html
        assert "keep" in html

    def test_leading_chrome_before_article_tag_is_skipped(self):
        # Confirmed live on a real arXiv page: 66KB of site chrome (search
        # modal, license banner) precedes `<article`, pushing the byline --
        # which LaTeXML places right at the top of `<article>` -- past
        # HTML_BUDGET entirely, so it was never sent to the model at all.
        chrome = "x" * 100_000
        html, truncated = prepare_html(f"{chrome}<article>byline here</article>")
        assert html.startswith("<article>")
        assert "byline here" in html
        assert truncated is False

    def test_no_article_tag_leaves_budget_window_at_start(self):
        # Lab blog pages (not arXiv) have no <article> tag -- must not
        # change behaviour for every other caller of this function.
        html, truncated = prepare_html("<p>keep</p>" + "x" * 100_000)
        assert html.startswith("<p>keep</p>")
        assert truncated is True

    def test_long_page_with_no_named_section_falls_back_to_tail(self):
        # No "Contributors"/"Author List" heading found -- falls back to a
        # blind tail, better than nothing for an unknown document shape.
        html, truncated = prepare_html("HEAD_MARKER" + "x" * 100_000 + "TAIL_MARKER")
        assert truncated is True
        assert html.startswith("HEAD_MARKER")
        assert html.endswith("TAIL_MARKER")
        assert len(html) == HTML_BUDGET

    def test_long_page_windows_around_named_author_section(self):
        # Confirmed live on two real Mistral AI papers (docs/decisions.md
        # D17): a "Contributors" heading sat 70KB-135KB past <article>,
        # itself well before the document's end -- a blind head+tail split
        # grabbed the references section that followed it, not the
        # contributor list itself, and still returned a false no_byline.
        filler_before = "x" * 40_000
        filler_after = "y" * 40_000 + "END_OF_DOCUMENT"  # e.g. references/bibliography
        html, truncated = prepare_html(
            f"HEAD_MARKER{filler_before}Contributors</h3>REAL_NAMES_HERE{filler_after}"
        )
        assert truncated is True
        assert html.startswith("HEAD_MARKER")
        assert "REAL_NAMES_HERE" in html
        # Windowed on the section, not a blind tail grab of the document's
        # actual end -- the window runs out inside filler_after long before
        # reaching it.
        assert "END_OF_DOCUMENT" not in html

    def test_author_section_window_stops_at_next_heading_not_full_tail_budget(self):
        # Real case (docs/decisions.md D17): Shieldstral's actual
        # "Contributors" section was ~4.5K characters, followed immediately
        # by a references/bibliography section dense with its own
        # comma-separated citation author names. A flat tail_budget window
        # ran straight past the boundary into it, and the model tried to
        # enumerate the whole mess as the byline, returning truncated,
        # invalid JSON instead of a parseable author list.
        html, truncated = prepare_html(
            "HEAD_MARKER" + "x" * 40_000 + "Contributors</h3>"
            "REAL_NAMES_HERE"
            "<h2>References</h2>NOT_A_CONTRIBUTOR" + "z" * 40_000
        )
        assert truncated is True
        assert "REAL_NAMES_HERE" in html
        assert "NOT_A_CONTRIBUTOR" not in html

    def test_page_exactly_at_budget_is_not_split(self):
        html, truncated = prepare_html("x" * HTML_BUDGET)
        assert truncated is False
        assert html == "x" * HTML_BUDGET


class TestExtractionError:
    def test_carries_cost_for_a_call_that_was_billed_but_failed_to_parse(self):
        # Confirmed live (docs/decisions.md D17): a plain re-raise on a
        # JSONDecodeError inside extract() silently lost the cost record,
        # since the API had already been billed before json.loads() failed.
        cost = {"model": "claude-sonnet-5", "usd": 0.05}
        exc = ExtractionError("malformed JSON from model: ...", cost)
        assert exc.cost == cost
        assert isinstance(exc, RuntimeError)
