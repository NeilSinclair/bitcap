"""Tests for byline extraction from Anthropic's research channels.

The contributor register is derived entirely from author lists, so a parser that
silently returns nothing degrades the register to empty without failing. Every
test below pins a defect this parser actually shipped with while being written
against the live pages.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "research"))

from byline import elements_by_class, parse_legend, parse_page  # noqa: E402

TC_PAPER = """
<div class='d-byline'>
<div class='authors'><h3>Authors</h3><div>
<span class='author'>Wes Gurnee<sup>*</sup>,</span> <span class='author'>Nick Sofroniew<sup>*</sup></span>
<span class='author'>Adam Pearce,</span> <span class='author'>Joshua Batson</span>
</div></div>
<div class='affiliations'><h3>Affiliations</h3><div><a href='#'>Anthropic</a></div></div>
<div class='published'><h3>Published</h3><div>July 6, 2026</div></div>
<div class='info'><span>* Core contributor;</span> <span>&dagger; Correspondence</span></div>
</div>
"""

ALIGNMENT_NUMBERED = """
<p class="authors">Aengus Lynch,<sup>1,*</sup> John Hughes,<sup>2</sup> Sam Bowman<sup>2</sup></p>
<p class="affiliations"><sup>1</sup> <a href="#">Theorem</a>; <sup>2</sup> Anthropic</p>
<p class="author-note"><sup>*</sup> Work done as part of the Anthropic Fellows program.</p>
"""

SECTION_AUTHORS = """
<div class="section-authors" data-author="" data-published="">
<div style="margin-bottom: 1em;">Mikhail Terekhov<sup>1,2</sup>, Vivek
    Hebbar<sup>3</sup>, Joe Benton<sup>4</sup><div style="float: right;">June 23, 2026</div></div>
<div style="font-size: 0.75em;">
<sup>1</sup>Anthropic Fellows Program;&nbsp;<sup>2</sup>EPFL;&nbsp;<sup>3</sup>Redwood Research;
<sup>4</sup>Anthropic
</div></div>
"""

MULTI_PARAGRAPH = """
<div class="section-authors">
<div style="margin-bottom: 1em;">Jonathan Kutasov<sup>*</sup>, Adam Jermyn<div style="float: right;">May 8, 2026</div></div>
<p>Julius Steen, Minh Le, Chris Olah<br></p>
<p>Evan Hubinger, Sara Price</p>
<div style="font-size: 0.75em;"><sup>*</sup>Correspondence to <a href="#">jonk@anthropic.com</a></div>
</div>
"""

ALPHABETICAL = """
<div class="d-byline"><div class="authors"><h3>Authors</h3>
<div>Kit Fraser-Taliente*, Subhash Kantamneni*, Dan Mossing</div></div>
<div class="affiliations"><h3>Affiliations</h3><div>Anthropic</div></div>
<div class="published"><h3>Published</h3><div>May 7, 2026</div></div>
<div class="info"><div>* Equal contribution, author order alphabetical</div></div></div>
"""


class TestElementExtraction(unittest.TestCase):
    """A void tag inside a byline used to desynchronise the tag stack."""

    def test_void_tag_does_not_swallow_the_element(self):
        html = '<div class="section-authors">A<br>B<img src="x">C</div>'
        self.assertEqual(elements_by_class(html, "section-authors"), ['A<br>B<img src="x">C'])

    def test_nested_elements_are_kept_whole(self):
        got = elements_by_class(SECTION_AUTHORS, "section-authors")
        self.assertEqual(len(got), 1)
        self.assertIn("Joe Benton", got[0])


class TestTransformerCircuits(unittest.TestCase):
    def test_authors_dates_and_core_marker(self):
        got = parse_page(TC_PAPER)
        self.assertEqual(
            [a["name"] for a in got["authors"]],
            ["Wes Gurnee", "Nick Sofroniew", "Adam Pearce", "Joshua Batson"],
        )
        self.assertEqual(got["date"], "2026-07-06")
        self.assertEqual([a["is_core"] for a in got["authors"]], [True, True, False, False])

    def test_marker_symbol_is_stripped_from_the_name(self):
        for author in parse_page(TC_PAPER)["authors"]:
            self.assertNotIn("*", author["name"])


class TestAlignmentBlog(unittest.TestCase):
    def test_superscript_before_the_comma_binds_to_the_preceding_author(self):
        # "Aengus Lynch,<sup>1,*</sup>" - the marker follows the comma, so a
        # naive comma split hands Aengus's affiliation to John Hughes.
        authors = {a["name"]: a for a in parse_page(ALIGNMENT_NUMBERED)["authors"]}
        self.assertEqual(authors["Aengus Lynch"]["affiliations"], ["Theorem"])
        self.assertEqual(authors["John Hughes"]["affiliations"], ["Anthropic"])

    def test_line_wrapped_name_is_not_split_in_two(self):
        names = [a["name"] for a in parse_page(SECTION_AUTHORS)["authors"]]
        self.assertIn("Vivek Hebbar", names)
        self.assertNotIn("Vivek", names)

    def test_affiliations_and_date_from_a_section_byline(self):
        got = parse_page(SECTION_AUTHORS)
        self.assertEqual(got["date"], "2026-06-23")
        authors = {a["name"]: a for a in got["authors"]}
        self.assertEqual(authors["Joe Benton"]["affiliations"], ["Anthropic"])
        self.assertEqual(authors["Vivek Hebbar"]["affiliations"], ["Redwood Research"])
        self.assertFalse(authors["Vivek Hebbar"]["is_anthropic"])

    def test_authors_continuing_into_later_paragraphs_are_collected(self):
        names = [a["name"] for a in parse_page(MULTI_PARAGRAPH)["authors"]]
        self.assertEqual(len(names), 7)
        self.assertIn("Sara Price", names)

    def test_correspondence_footnote_is_not_read_as_an_author(self):
        names = [a["name"] for a in parse_page(MULTI_PARAGRAPH)["authors"]]
        self.assertFalse(any("Correspondence" in n for n in names))


class TestLegendSemantics(unittest.TestCase):
    """The same asterisk means different things on different Anthropic pages."""

    def test_star_meanings_are_distinguished(self):
        self.assertEqual(parse_legend(TC_PAPER)["star_means"], "core")
        self.assertEqual(parse_legend(ALPHABETICAL)["star_means"], "equal")
        self.assertEqual(parse_legend(ALIGNMENT_NUMBERED)["star_means"], "fellows")

    def test_fellows_marker_is_never_promoted_to_core_contributor(self):
        for author in parse_page(ALIGNMENT_NUMBERED)["authors"]:
            self.assertIsNot(author["is_core"], True)

    def test_alphabetical_byline_marks_author_order_as_meaningless(self):
        self.assertFalse(parse_page(ALPHABETICAL)["order_meaningful"])
        self.assertTrue(parse_page(TC_PAPER)["order_meaningful"])


class TestNoByline(unittest.TestCase):
    def test_page_without_a_byline_returns_empty_rather_than_guessing(self):
        got = parse_page("<d-article><p><span>Some Name</span></p></d-article>")
        self.assertEqual(got["authors"], [])


class TestFellowsAndEmployment(unittest.TestCase):
    """A fellowship is declared two ways and is not employment."""

    FELLOW_BY_AFFILIATION = """
    <div class="section-authors">
    <div>Colin Toft<sup>1</sup>, Joe Benton<sup>2</sup><div>May 19, 2026</div></div>
    <div><sup>1</sup>Anthropic Fellows Program; <sup>2</sup>Anthropic</div></div>
    """

    def test_fellowship_declared_as_an_affiliation_sets_is_fellow(self):
        authors = {a["name"]: a for a in parse_page(self.FELLOW_BY_AFFILIATION)["authors"]}
        self.assertTrue(authors["Colin Toft"]["is_fellow"])
        self.assertFalse(authors["Joe Benton"]["is_fellow"])

    def test_fellowship_declared_by_the_star_footnote_sets_is_fellow(self):
        authors = {a["name"]: a for a in parse_page(ALIGNMENT_NUMBERED)["authors"]}
        self.assertTrue(authors["Aengus Lynch"]["is_fellow"])

    def test_a_fellow_is_not_counted_as_anthropic_staff(self):
        authors = {a["name"]: a for a in parse_page(self.FELLOW_BY_AFFILIATION)["authors"]}
        self.assertFalse(authors["Colin Toft"]["is_anthropic"])
        self.assertTrue(authors["Joe Benton"]["is_anthropic"])


if __name__ == "__main__":
    unittest.main()


class TestDefectsFoundByTheLLMEval(unittest.TestCase):
    """Regressions the LLM comparison exposed in the deterministic parser."""

    def test_trailing_superscript_stays_with_its_own_name(self):
        # "Robert Kirk,<sup>4</sup> Sam Bowman<sup>2</sup>" splits into one chunk
        # holding Robert's marker and Sam's name; only the leading marker is his.
        authors = {a["name"]: a for a in parse_page(ALIGNMENT_NUMBERED)["authors"]}
        self.assertEqual(authors["John Hughes"]["marks"], ["2"])
        self.assertEqual(authors["John Hughes"]["affiliations"], ["Anthropic"])

    def test_a_page_with_no_asterisk_has_no_star_convention(self):
        html = """
        <div class="section-authors">
        <div>Elle Najt<sup>1</sup>, Joe Benton<sup>2</sup></div>
        <div><sup>1</sup>Anthropic Fellows Program; <sup>2</sup>Anthropic</div></div>
        """
        # The legend names a fellows programme, but "*" is never printed, so
        # there is nothing for "*" to mean.
        self.assertIsNone(parse_page(html)["star_means"])
        self.assertTrue(parse_page(html)["authors"][0]["is_fellow"])
