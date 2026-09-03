"""Tests for the verbatim quote gate.

The silent failure this exists to catch is a tag that states a true fact in
words the document never used. It looks like evidence, reads as evidence, and
breaks the moment a reader searches for it -- which is the one thing a fund
analyst will certainly do.

Every case below is drawn from real model output except where marked.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "announcements"))

from verbatim import MIN_SEGMENT, enforce, fold, snap  # noqa: E402

SOURCE = (
    "In the weeks leading up to the launch of Fable, Anthropic worked with the "
    "US government, the UK AISI, multiple private third-party organizations and "
    "internal teams to red-team Fable’s safeguards for thousands of hours in "
    "total. These tests showed that Fable’s safeguards are substantially more "
    "effective. For coding workflows, GPT-5.6 Sol sets a new state of the art on "
    "Terminal-Bench 2.1 , which tests command-line workflows. "
    "OpenAI models on Amazon Bedrock ⁠ (opens in a new window) allows teams to "
    "build AI applications using AWS-native security and governance controls."
)


class TestExactMatch:
    def test_a_verbatim_quote_passes_unchanged(self):
        q = "Anthropic worked with the US government, the UK AISI"
        assert snap(q, SOURCE) == q

    def test_a_quote_absent_from_the_document_is_rejected(self):
        assert snap("Anthropic worked with the FBI and the NSA", SOURCE) is None

    def test_an_empty_quote_is_rejected(self):
        assert snap("", SOURCE) is None
        assert snap("   ", SOURCE) is None


class TestSnapping:
    """A quote that matches only after normalisation is rewritten to the source.

    The rewrite is safe because it happens only when the source proves it
    correct: the returned string is a substring of the document, never anything
    the model wrote.
    """

    def test_a_literal_unicode_escape_is_decoded_and_snapped(self):
        """Seen in production: the model emitted the six characters `\\u2019`
        instead of the apostrophe, so ctrl-F for the quote found nothing."""
        out = snap("red-team Fable\\u2019s safeguards for thousands of hours", SOURCE)
        assert out == "red-team Fable’s safeguards for thousands of hours"
        assert "\\u" not in out
        assert out in SOURCE, "the result must be a literal substring of the source"

    def test_a_space_left_by_html_extraction_is_tolerated(self):
        """The extracted text holds `Terminal-Bench 2.1 ,`; a reader sees no
        space, so the model quotes none. The document's form wins."""
        out = snap("Terminal-Bench 2.1, which tests command-line workflows", SOURCE)
        assert out == "Terminal-Bench 2.1 , which tests command-line workflows"
        assert out in SOURCE

    def test_a_capitalised_first_letter_is_relaxed_and_snapped_back(self):
        """Real output: the source reads "it delivered", the model quoted "It
        delivered". Starting a quote mid-sentence and capitalising it is
        ordinary practice, not fabrication."""
        source = "In our evaluations, it delivered comparable quality at half the cost."
        out = snap("It delivered comparable quality at half the cost.", source)
        assert out == "it delivered comparable quality at half the cost."
        assert out in source

    def test_terminal_punctuation_may_differ_from_the_document(self):
        """Real output: the source reads "universal jailbreaks:", the model
        ended its quote with a full stop. The document's form is returned."""
        source = "aimed at finding universal jailbreaks: attacks that work broadly."
        out = snap("aimed at finding universal jailbreaks.", source)
        assert out == "aimed at finding universal jailbreaks"
        assert out in source

    def test_relaxing_case_does_not_relax_the_words(self):
        source = "In our evaluations, it delivered comparable quality at half the cost."
        assert snap("It delivered comparable safety at half the cost", source) is None

    def test_curly_and_straight_quotes_are_interchangeable(self):
        out = snap("red-team Fable's safeguards", SOURCE)
        assert out == "red-team Fable’s safeguards"

    def test_unmarked_elision_is_rejected_even_when_the_gap_is_chrome(self):
        """The model reads across `(opens in a new window)` -- correctly, since
        the prompt tells it to ignore furniture -- and the result is a quote
        that is not in the document. Dropping words from the middle without an
        ellipsis is the splice shape, so the gate must refuse it; the fix
        belongs upstream, in `strip_chrome`, not in a special case here."""
        assert snap(
            "OpenAI models on Amazon Bedrock allows teams to build AI applications",
            SOURCE,
        ) is None

    def test_the_same_quote_passes_once_the_chrome_is_stripped(self):
        import sys
        sys.path.insert(0, str(ROOT / "research" / "announcements"))
        from fetch_announcements import strip_chrome
        cleaned = strip_chrome(SOURCE)
        out = snap(
            "OpenAI models on Amazon Bedrock allows teams to build AI applications",
            cleaned,
        )
        assert out is not None and out in cleaned


class TestEllipsis:
    def test_an_honest_elision_passes_and_is_normalised(self):
        out = snap("Anthropic worked with the US government ... for thousands of "
                   "hours in total", SOURCE)
        assert out == ("Anthropic worked with the US government ... for thousands "
                       "of hours in total")

    def test_segments_out_of_order_are_rejected(self):
        """Without ordering, `A ... B` licenses the exact splice this exists to
        catch -- taking the end of the document and gluing the start to it."""
        assert snap("for thousands of hours in total ... Anthropic worked with "
                    "the US government", SOURCE) is None

    def test_a_segment_that_is_absent_fails_the_whole_quote(self):
        assert snap("Anthropic worked with the US government ... and the NSA "
                    "for many hours", SOURCE) is None

    def test_trivially_short_segments_cannot_carry_a_quote(self):
        assert snap("the ... and", SOURCE) is None
        assert len("the") < MIN_SEGMENT

    def test_an_ellipsis_present_in_the_source_is_matched_whole(self):
        source = "We asked: is this real ... and the answer came back yes."
        assert snap("is this real ... and the answer came back", source) is not None


class TestSplice:
    def test_a_quote_spliced_from_two_sentences_is_rejected(self):
        """The real failure from an earlier run: the first word of one sentence
        joined to the body of another. The fact was true and stated twice; the
        quote was not in the document."""
        source = (
            "Anthropic will secure up to 5 gigawatts (GW) of capacity for training "
            "and deploying Claude. The agreement involves securing up to 5GW of new "
            "capacity to train and run Claude."
        )
        spliced = ("securing up to 5 gigawatts (GW) of capacity for training and "
                   "deploying Claude")
        assert spliced not in source
        assert snap(spliced, source) is None


class TestEnforce:
    def test_a_bad_tag_is_dropped_and_reported_and_good_ones_survive(self):
        result = {
            "mechanisms": [
                {"id": "export_controls", "quote": "Anthropic worked with the US government"},
                {"id": "training_compute_up", "quote": "Anthropic worked with the FBI"},
            ],
            "categories": [],
            "practices": [
                {"id": "evaluation",
                 "quote": "red-team Fable\\u2019s safeguards for thousands of hours"},
            ],
        }
        dropped = enforce(result, SOURCE)
        assert [m["id"] for m in result["mechanisms"]] == ["export_controls"]
        assert dropped == ["mechanisms:training_compute_up:quote not in document"]
        assert result["practices"][0]["quote"] in SOURCE, "surviving tags are snapped"

    def test_a_result_with_no_tags_is_left_alone(self):
        result = {"mechanisms": [], "categories": [], "practices": []}
        assert enforce(result, SOURCE) == []

    def test_missing_keys_are_not_invented(self):
        result = {"mechanisms": []}
        enforce(result, SOURCE)
        assert "practices" not in result


class TestFold:
    def test_the_offset_map_lines_up_with_the_original(self):
        folded, index = fold(SOURCE)
        assert len(folded) == len(index)
        for i, offset in enumerate(index):
            assert 0 <= offset < len(SOURCE)

    def test_folding_is_idempotent_on_already_clean_text(self):
        clean = "A simple sentence with no tricks."
        folded, _ = fold(clean)
        assert folded == clean
