"""Tests for the thesis-report analysis.

Every number on that page comes from these functions, so a silently wrong one is
worse than a crash. Each test below corresponds to a defect the code shipped
with: a counterfactual that priced sales at zero, and a sparkline that drew
straight through quarters the position was not held.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "research"))

QUARTERS = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
            "2026-03-31", "2026-06-30"]


def position(shares, prices, cusip="TEST00001", vendor=True):
    """A synthetic position: shares per quarter, and a price per quarter."""
    quarters = [
        {"period": p, "shares": s, "value_usd": s * (px or 0), "pct": 1.0,
         "implied_price": px if s else None}
        for p, s, px in zip(QUARTERS, shares, prices)
    ]
    rec = {"cusip": cusip, "issuer": "Test Corp", "quarters": quarters,
           "price_source": "vendor" if vendor else "implied", "price": []}
    if vendor:
        rec["quarter_close"] = {p: px for p, px in zip(QUARTERS, prices) if px}
    return rec


def load_module(positions, window=5):
    """Import build_thesis against synthetic data instead of the real files."""
    payload = {"periods": QUARTERS, "positions": positions}
    analysis = {"latest_holdings": [
        {"cusip": c, "pct": 1.0, "issuer": r["issuer"]} for c, r in positions.items()]}
    # Statutory view, used only by nav_limits(); one fund, one position.
    views = {"positions": [{"fund_full": "Test Fund", "name": "Test Corp",
                            "pct_of_fund": 6.5, "as_of": QUARTERS[-1]}]}
    import importlib.util
    src = Path(__file__).parent.parent / "research" / "build_thesis.py"
    with mock.patch("json.loads", side_effect=[payload, analysis, views]), \
         mock.patch.object(sys, "argv", ["build_thesis.py", str(window)]), \
         mock.patch("pathlib.Path.read_text", return_value="{}"):
        spec = importlib.util.spec_from_file_location("bt", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class TestCounterfactual(unittest.TestCase):
    def test_refuses_a_window_containing_an_unheld_quarter(self):
        """Pricing a sale at zero returned a confident wrong number for 40 names."""
        pos = {"TEST00001": position([100, 100, 0, 0, 50, 50],
                                     [10, 20, None, None, 40, 50], vendor=False)}
        bt = load_module(pos)
        with self.assertRaises(ValueError) as e:
            bt.counterfactual("TEST00001", QUARTERS[:5])
        self.assertIn("undefined", str(e.exception))

    def test_computes_opportunity_cost_of_selling_into_a_rise(self):
        """Sell half at 20, price ends at 40: holding beats trading."""
        pos = {"TEST00001": position([100, 50, 50, 50, 50, 50],
                                     [10, 20, 25, 30, 35, 40])}
        bt = load_module(pos)
        cf = bt.counterfactual("TEST00001", QUARTERS)
        self.assertEqual(cf["untraded"], 100 * 40)      # never sold
        self.assertEqual(cf["actual"], 50 * 40 + 50 * 20)  # held half + proceeds
        self.assertEqual(cf["delta"], -1000)            # the rule cost 1000

    def test_returns_none_when_not_held_at_window_start(self):
        pos = {"TEST00001": position([0, 0, 0, 100, 100, 100],
                                     [None, None, None, 10, 20, 30])}
        bt = load_module(pos)
        self.assertIsNone(bt.counterfactual("TEST00001", QUARTERS))


class TestSpark(unittest.TestCase):
    def setUp(self):
        self.bt = load_module({"TEST00001": position([1] * 6, [1] * 6)})

    def test_breaks_the_line_where_the_position_was_exited(self):
        """An exit and re-entry must not render as one continuous holding."""
        svg = self.bt.spark([5, 4, 0, 0, 6, 7], "#fff")
        self.assertEqual(svg.count("<polyline"), 2)

    def test_marks_the_quarters_that_were_gaps(self):
        svg = self.bt.spark([5, 4, 0, 0, 6, 7], "#fff")
        self.assertEqual(svg.count("<line"), 2)

    def test_continuous_series_is_a_single_line(self):
        svg = self.bt.spark([1, 2, 3, 4, 5, 6], "#fff")
        self.assertEqual(svg.count("<polyline"), 1)
        self.assertNotIn("<line", svg)

    def test_isolated_point_renders_as_a_dot_not_a_line(self):
        svg = self.bt.spark([0, 0, 5, 0, 6, 7], "#fff")
        self.assertIn("<circle", svg)

    def test_log_scale_survives_a_hundredfold_move(self):
        svg = self.bt.spark([1, 10, 100, 500, 900, 1000], "#fff", log=True)
        self.assertNotIn("NaN", svg)


class TestRows(unittest.TestCase):
    def test_age_counts_only_the_current_unbroken_run(self):
        """A re-entered name must not claim credit for the earlier holding."""
        pos = {"TEST00001": position([100, 100, 0, 0, 50, 50],
                                     [10, 20, 30, 40, 50, 60])}
        bt = load_module(pos)
        self.assertEqual(bt.rows()[0]["age"], 2)

    def test_runup_is_suppressed_when_four_quarters_do_not_fit(self):
        """Clamping at the window edge measured three quarters, labelled four."""
        pos = {"TEST00001": position([0, 100, 100, 100, 100, 100],
                                     [10, 20, 30, 40, 50, 60])}
        bt = load_module(pos)
        self.assertIsNone(bt.rows()[0]["runup"])

    def test_runup_measures_the_four_quarters_before_entry(self):
        """Entry at index 5 looks back to index 1, not to the start of the window."""
        pos = {"TEST00001": position([0, 0, 0, 0, 0, 100],
                                     [999, 10, 30, 40, 50, 20])}
        bt = load_module(pos)
        self.assertAlmostEqual(bt.rows()[0]["runup"], 100.0)  # 10 -> 20

    def test_names_are_html_escaped(self):
        pos = {"TEST00001": position([100] * 6, [10] * 6)}
        pos["TEST00001"]["issuer"] = "Hims & Hers <b>"
        bt = load_module(pos)
        self.assertNotIn("<b>", bt.rows()[0]["name"])
        self.assertIn("&amp;", bt.rows()[0]["name"])


class TestNavLimits(unittest.TestCase):
    def test_summarises_a_fund_against_the_5_10_40_pattern(self):
        bt = load_module({"TEST00001": position([100] * 6, [10] * 6)})
        row = bt.nav_limits()[0]
        self.assertEqual(row["n_over5"], 1)
        self.assertEqual(row["n_over10"], 0)
        self.assertAlmostEqual(row["sum_over5"], 6.5)


class TestChurn(unittest.TestCase):
    def test_counts_distinct_names_and_carry_over(self):
        pos = {f"TEST0000{i}": position([100] * 6, [10] * 6, cusip=f"TEST0000{i}")
               for i in range(3)}
        bt = load_module(pos)
        ch = bt.churn()
        self.assertEqual(ch["ever"], 3)
        self.assertEqual(ch["carry_avg"], 3.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
