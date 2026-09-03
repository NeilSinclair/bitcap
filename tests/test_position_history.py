"""Tests for the position-history build.

The thing worth protecting here is the ticker check. If it silently starts
accepting mismatches, the report draws a confident price line for the wrong
company — a wrong answer that looks exactly like a right one. Each test below
corresponds to a bug this code actually had.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "research"))

import position_history  # noqa: E402
from position_history import (  # noqa: E402
    close_near, downsample, histories, load_tickers, verify)


def series(points):
    """[(date, close)] -> the vendor series shape."""
    return [{"date": d, "close": c} for d, c in points]


def quarters(points):
    """[(period, implied_price)] -> the filing-derived shape verify() reads."""
    return [{"period": p, "implied_price": v} for p, v in points]


QE = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]


class TestCloseNear(unittest.TestCase):
    def test_takes_last_close_on_or_before(self):
        s = series([("2025-06-27", 10.0), ("2025-06-30", 11.0), ("2025-07-01", 12.0)])
        self.assertEqual(close_near(s, "2025-06-30"), 11.0)

    def test_falls_back_to_earlier_close(self):
        s = series([("2025-06-27", 10.0), ("2025-07-01", 12.0)])
        self.assertEqual(close_near(s, "2025-06-30"), 10.0)

    def test_none_when_series_starts_later(self):
        self.assertIsNone(close_near(series([("2025-07-01", 12.0)]), "2025-06-30"))


class TestVerify(unittest.TestCase):
    def test_accepts_matching_ticker(self):
        prices = [100.0, 110.0, 120.0, 130.0]
        r = verify(series(zip(QE, prices)), quarters(zip(QE, prices)))
        self.assertTrue(r["ok"])
        self.assertEqual(r["quarters_checked"], 4)

    def test_rejects_a_different_company(self):
        r = verify(series(zip(QE, [100.0, 110.0, 120.0, 130.0])),
                   quarters(zip(QE, [40.0, 44.0, 39.0, 41.0])))
        self.assertFalse(r["ok"])
        self.assertIn("differs", r["reason"])

    def test_rejects_on_the_worst_quarter_not_the_average(self):
        """Three good quarters must not average away one bad one."""
        r = verify(series(zip(QE, [100.0, 110.0, 120.0, 130.0])),
                   quarters(zip(QE, [100.0, 110.0, 120.0, 90.0])))
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"].split()[-1], "2025-12-31")

    def test_split_does_not_fail_a_correct_mapping(self):
        """A 10:1 split leaves vendor at a tenth of the filing before the event."""
        periods = ["2024-12-31"] + QE
        vendor = [13.0, 100.0, 110.0, 120.0, 130.0]
        implied = [130.0, 100.0, 110.0, 120.0, 130.0]
        r = verify(series(zip(periods, vendor)), quarters(zip(periods, implied)))
        self.assertTrue(r["ok"])
        self.assertEqual(r["split_before"], "2024-12-31")

    def test_split_rule_cannot_rescue_a_wrong_ticker(self):
        """Dropping pre-split quarters must not let a mismatch through."""
        periods = ["2024-12-31"] + QE
        r = verify(series(zip(periods, [13.0, 100.0, 110.0, 120.0, 130.0])),
                   quarters(zip(periods, [130.0, 60.0, 66.0, 71.0, 78.0])))
        self.assertFalse(r["ok"])

    def test_no_overlap_is_not_a_pass(self):
        r = verify(series([("2026-01-05", 10.0)]), quarters([("2025-06-30", 10.0)]))
        self.assertFalse(r["ok"])
        self.assertIn("no overlapping", r["reason"])


class TestLoadTickers(unittest.TestCase):
    """YAML silently reads an unquoted CUSIP as an int, and a leading-zero one as
    octal: 023135106 becomes 5028422. That corrupted 16 identifiers with no
    symptom beyond a missing price line."""

    def write(self, body):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(body); f.close()
        position_history.TICKERS = Path(f.name)
        return f.name

    def tearDown(self):
        position_history.TICKERS = Path(__file__).parent.parent / "config" / "tickers.yaml"

    def test_rejects_unquoted_numeric_cusips(self):
        self.write("tickers:\n  595112103: MU\n")
        with self.assertRaises(ValueError) as e:
            load_tickers()
        self.assertIn("not CUSIPs", str(e.exception))

    def test_accepts_quoted_cusips(self):
        self.write('tickers:\n  "595112103": MU\n  "023135106": AMZN\n')
        self.assertEqual(load_tickers()["023135106"], "AMZN")

    def test_the_real_config_is_clean(self):
        """Guards the checked-in file itself, not just the loader."""
        self.assertGreater(len(load_tickers()), 0)


class TestDownsample(unittest.TestCase):
    def test_always_keeps_the_latest_point(self):
        s = series((f"2025-01-{d:02d}", float(d)) for d in range(1, 13))
        self.assertEqual(downsample(s, 5)[-1], s[-1])

    def test_does_not_duplicate_an_already_kept_last_point(self):
        s = series((f"2025-01-{d:02d}", float(d)) for d in range(1, 12))
        out = downsample(s, 5)
        self.assertEqual(len(out), len(set(p["date"] for p in out)))


class TestHistories(unittest.TestCase):
    def filing(self, period, holdings):
        return {"period": period, "holdings": [
            {"issuer": i, "cusip": c, "value_usd": v, "shares": s,
             "put_call": None, "class": "COM", "share_type": "SH"}
            for i, c, v, s in holdings]}

    def test_zero_fills_quarters_the_position_was_absent(self):
        h = histories([
            self.filing("2025-03-31", [("ACME", "111", 1000, 10)]),
            self.filing("2025-06-30", [("OTHER", "222", 500, 5)]),
            self.filing("2025-09-30", [("ACME", "111", 2000, 10)]),
        ])
        acme = h["111"]["quarters"]
        self.assertEqual([q["shares"] for q in acme], [10, 0, 10])
        self.assertIsNone(acme[1]["implied_price"])

    def test_implied_price_is_value_over_shares(self):
        h = histories([self.filing("2025-03-31", [("ACME", "111", 1000, 8)])])
        self.assertEqual(h["111"]["quarters"][0]["implied_price"], 125.0)

    def test_options_are_excluded_from_the_share_history(self):
        """Option notional must not be summed into a position's share count."""
        f = self.filing("2025-03-31", [("ACME", "111", 1000, 10)])
        f["holdings"].append({"issuer": "ACME", "cusip": "111", "value_usd": 9_000_000,
                              "shares": 90_000, "put_call": "Call", "class": "COM",
                              "share_type": "SH"})
        self.assertEqual(histories([f])["111"]["quarters"][0]["shares"], 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
