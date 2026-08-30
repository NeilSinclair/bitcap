"""Tests for the byline evaluation comparison.

An eval that scores itself wrong is worse than no eval, so the comparison logic is
tested independently of any API call.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "research"))

from eval_byline import compare_article, normalise, summarise  # noqa: E402


def gold(authors, **kw):
    base = {
        "url": "u",
        "title": "t",
        "date": "2026-07-06",
        "star_means": "core",
        "order_meaningful": True,
        "authors": authors,
    }
    base.update(kw)
    return base


def author(name, aff=("Anthropic",), anthropic=True, fellow=False):
    return {
        "name": name,
        "affiliations": list(aff),
        "is_anthropic": anthropic,
        "is_fellow": fellow,
    }


class TestNormalise(unittest.TestCase):
    def test_initial_punctuation_and_accents_do_not_split_a_person(self):
        self.assertEqual(normalise("Nicholas L. Turner"), normalise("Nicholas L Turner"))
        self.assertEqual(normalise("Caglar Gulcehre"), normalise("Çaglar Gulcehre"))

    def test_a_different_given_name_stays_a_different_person(self):
        self.assertNotEqual(normalise("Jon Kutasov"), normalise("Jonathan Kutasov"))


class TestCompare(unittest.TestCase):
    def test_missed_and_invented_names_are_reported_separately(self):
        g = gold([author("Wes Gurnee"), author("Jack Lindsey")])
        p = {"authors": [author("Wes Gurnee"), author("Nobody Real")], "date": "2026-07-06",
             "star_means": "core", "order_meaningful": True}
        r = compare_article(g, p)
        self.assertEqual(r["missed"], ["Jack Lindsey"])
        self.assertEqual(r["invented"], ["Nobody Real"])
        self.assertFalse(r["exact_set"])

    def test_affiliation_disagreement_is_surfaced_not_averaged(self):
        g = gold([author("Colin Toft", ["Anthropic Fellows Program"], anthropic=False, fellow=True)])
        p = {"authors": [author("Colin Toft", ["Anthropic"], anthropic=True, fellow=False)],
             "date": "2026-07-06", "star_means": "core", "order_meaningful": True}
        r = compare_article(g, p)
        fields = {d["field"] for d in r["field_diffs"]}
        self.assertEqual(fields, {"affiliations", "is_anthropic", "is_fellow"})

    def test_an_empty_gold_byline_is_matched_by_an_empty_prediction(self):
        r = compare_article(gold([]), {"authors": [], "date": None, "star_means": None,
                                       "order_meaningful": True})
        self.assertTrue(r["exact_set"])
        self.assertEqual(r["missed"], [])


class TestSummarise(unittest.TestCase):
    def test_precision_and_recall_are_micro_averaged_over_bylines(self):
        rows = [
            {"matched": 3, "missed": ["a"], "invented": [], "exact_set": False,
             "order_match": False, "field_diffs": [], "date_match": True,
             "star_match": True, "order_meaningful_match": True, "truncated": False},
            {"matched": 2, "missed": [], "invented": ["x", "y"], "exact_set": False,
             "order_match": False, "field_diffs": [], "date_match": False,
             "star_match": True, "order_meaningful_match": True, "truncated": True},
        ]
        s = summarise(rows)
        self.assertEqual(s["name_recall"], round(5 / 6, 4))
        self.assertEqual(s["name_precision"], round(5 / 7, 4))
        self.assertEqual(s["date_accuracy"], 0.5)
        self.assertEqual(s["truncated_pages"], 1)


if __name__ == "__main__":
    unittest.main()
