"""Tests for alias proposal from the co-authorship graph.

Merging two different people misattributes work, so these tests are weighted
towards the cases that must NOT merge.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "research"))

from alias_candidates import (  # noqa: E402
    given_names_compatible,
    load_confirmed,
    name_parts,
    propose,
    to_yaml,
)


def register(articles):
    names = {n for a in articles for n in a["authors_names"]}
    return {
        "people": [{"name": n, "appearances": sum(
            1 for a in articles if n in a["authors_names"])} for n in names],
        "articles": [
            {"url": a["url"], "authors": [{"name": n} for n in a["authors_names"]]}
            for a in articles
        ],
    }


def art(url, *names):
    return {"url": url, "authors_names": list(names)}


class TestNameShape(unittest.TestCase):
    def test_middle_initials_are_ignored(self):
        self.assertEqual(name_parts("Paul C. Bogdan"), ("paul", "bogdan"))

    def test_diminutives_are_compatible(self):
        for short, long in [("sam", "samuel"), ("nick", "nicholas"), ("jon", "jonathan")]:
            self.assertTrue(given_names_compatible(short, long), f"{short}/{long}")

    def test_unrelated_given_names_are_not_compatible(self):
        self.assertFalse(given_names_compatible("sarah", "samuel"))
        self.assertFalse(given_names_compatible("john", "james"))


class TestProposals(unittest.TestCase):
    def test_shared_coauthors_support_a_merge(self):
        r = register([
            art("a1", "Samuel Marks", "Ethan Perez", "Evan Hubinger", "Jack Lindsey"),
            art("a2", "Sam Marks", "Ethan Perez", "Evan Hubinger", "Jack Lindsey"),
        ])
        c = propose(r)[0]
        self.assertEqual({c["canonical"], c["variant"]}, {"Samuel Marks", "Sam Marks"})
        self.assertEqual(c["verdict"], "likely_same")

    def test_two_people_on_the_same_article_are_never_merged(self):
        # The decisive case: same surname, compatible given names, and plenty of
        # shared co-authors - but they co-authored, so they are two people.
        r = register([art("a1", "Sam Chen", "Samuel Chen", "Ethan Perez", "Jack Lindsey")])
        c = propose(r)[0]
        self.assertEqual(c["verdict"], "reject")
        self.assertIn("same article", c["reason"])

    def test_no_shared_coauthors_is_not_promoted_to_a_merge(self):
        r = register([
            art("a1", "Sam Wilson", "Ethan Perez"),
            art("a2", "Samuel Wilson", "Jack Lindsey"),
        ])
        self.assertEqual(propose(r)[0]["verdict"], "weak")

    def test_accent_and_punctuation_variants_need_no_coauthor_evidence(self):
        r = register([art("a1", "Clement Dumas", "X Y"), art("a2", "Clément Dumas", "P Q")])
        self.assertEqual(propose(r)[0]["verdict"], "same_string")

    def test_different_surnames_are_never_paired(self):
        r = register([art("a1", "Sam Marks", "Sam Bowman")])
        self.assertEqual(propose(r), [])


class TestConfirmationGate(unittest.TestCase):
    def test_nothing_is_applied_until_confirmed(self):
        r = register([
            art("a1", "Samuel Marks", "Ethan Perez", "Evan Hubinger", "Jack Lindsey"),
            art("a2", "Sam Marks", "Ethan Perez", "Evan Hubinger", "Jack Lindsey"),
        ])
        path = Path(__file__).parent / "_aliases_tmp.yaml"
        path.write_text(to_yaml(propose(r)), encoding="utf-8")
        try:
            self.assertEqual(load_confirmed(path), {})
            path.write_text(
                path.read_text(encoding="utf-8").replace("confirmed: false", "confirmed: true", 1),
                encoding="utf-8",
            )
            self.assertEqual(load_confirmed(path), {"Sam Marks": "Samuel Marks"})
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
