"""app.ranking: an item's place on the investment surfaces, and its justification.

The silent failures this catches: the two scores combined in the wrong order (a
frontier launch with a thin book link sinking below a solid all-rounder, or a
tie at 100 left unbroken), a publisher-only or name-match link moving an item,
the justification citing a different tag from the one that set the score, and a
holding-led justification losing its company half — which would read as the lab
having said something about the company.
"""

from pathlib import Path
from types import SimpleNamespace as N

import yaml

from app import ranking

RULES = yaml.safe_load(
    (Path(__file__).parent.parent / "config" / "scoring.yaml").read_text(encoding="utf-8"))
ROUTES = RULES["ranking"]["holding_routes"]


def _tag(ordinal, magnitude="high", confidence="high", mechanism_id="memory_intensity_up"):
    return N(ordinal=ordinal, mechanism_id=mechanism_id, sign="positive", magnitude=magnitude,
             confidence=confidence, reason=f"reason {ordinal}", quote=f"quote {ordinal}")


def _conn(strength, route="mechanism", isin="US1", via="memory_intensity_up", **overrides):
    row = dict(isin=isin, route=route, via=via, direction="positive", strength=strength,
               article_sign="positive", article_magnitude="high", article_confidence="high",
               article_reason="article reason", article_quote="1,050,000 context window",
               holding_sign="positive", holding_magnitude="high", holding_confidence="high",
               holding_why="memory content per accelerator", holding_source="https://sec.gov/x")
    row.update(overrides)
    return N(**row)


class TestRankKey:
    def test_the_trial_cases_order_by_the_higher_score_then_the_other(self):
        """A/B/C tie at 100 and split on their other score; D's best is only 80."""
        items = {"A": (100, 67), "B": (40, 100), "C": (100, 17), "D": (80, 80)}
        order = sorted(items, key=lambda k: ranking.rank_key(*items[k]), reverse=True)
        assert order == ["A", "B", "C", "D"]

    def test_the_lead_is_whichever_score_is_higher_and_a_tie_is_the_event(self):
        assert ranking.lead(80, 100) == ranking.HOLDING
        assert ranking.lead(100, 17) == ranking.EVENT
        assert ranking.lead(100, 100) == ranking.EVENT


class TestHoldingLink:
    def test_publisher_and_name_links_do_not_set_the_holding_score(self):
        link = ranking.holding_link(
            [_conn(0.6, route="lab_exposure"), _conn(0.3, route="named"), _conn(0.17)], ROUTES)
        assert link.route == "mechanism"
        assert ranking.holding_score(link) == 17.0

    def test_no_qualifying_link_scores_zero(self):
        link = ranking.holding_link([_conn(0.6, route="lab_exposure")], ROUTES)
        assert link is None
        assert ranking.holding_score(link) == 0.0

    def test_mechanism_beats_category_at_equal_strength(self):
        link = ranking.holding_link(
            [_conn(0.3, route="category", via="memory_storage"), _conn(0.3)], ROUTES)
        assert link.route == "mechanism"

    def test_the_shipped_routes_are_mechanism_and_category(self):
        """Pinned: adding lab_exposure lets Amazon's contract rank every OpenAI post."""
        assert sorted(ROUTES) == ["category", "mechanism"]


class TestStrongestTag:
    def test_the_strongest_tag_wins_not_the_first(self):
        tags = [_tag(0, "low", "medium"), _tag(1, "high", "high")]
        assert ranking.strongest_tag(tags, RULES).ordinal == 1

    def test_a_tie_goes_to_the_lowest_ordinal_whatever_the_row_order(self):
        assert ranking.strongest_tag([_tag(2), _tag(0), _tag(1)], RULES).ordinal == 0

    def test_a_low_confidence_tag_earns_nothing(self):
        assert ranking.strongest_tag([_tag(0, "high", "low")], RULES) is None


class TestRankFields:
    LABELS = dict(
        mech_labels={"capability_jump": "Step change in model capability",
                     "memory_intensity_up": "Memory content per accelerator rises"},
        cat_labels={"memory_storage": "Memory and storage"},
        names={"US1": "Micron"},
    )

    def _fields(self, score, tags, conns, event_type="frontier_model_release"):
        cls = N(score=score, event_type=event_type)
        return ranking.rank_fields(cls, tags, conns, RULES, **self.LABELS)

    def test_an_event_led_item_cites_the_tag_that_set_the_score(self):
        """GPT-6 Astra's shape: capability_jump sets 100, memory links the book at 17."""
        tags = [_tag(0, "medium", "high"), _tag(1, mechanism_id="capability_jump")]
        f = self._fields(100.0, tags, [_conn(0.17)])

        assert (f["rankLead"], f["rankValue"], f["rankBand"], f["holdingScore"]) == \
            ("event", 100.0, "high", 17.0)
        assert f["eventBasis"]["eventWeight"] == 5
        assert f["eventBasis"]["tag"]["id"] == "capability_jump"

    def test_a_holding_led_item_carries_both_halves(self):
        f = self._fields(40.0, [_tag(0, "medium", "high")], [_conn(1.0)])

        assert (f["rankLead"], f["rankValue"]) == ("holding", 100.0)
        basis = f["holdingBasis"]
        assert (basis["holding"], basis["label"]) == ("Micron", "Memory content per accelerator rises")
        assert basis["article"]["quote"] == "1,050,000 context window"
        assert basis["company"]["why"] == "memory content per accelerator"
        assert basis["company"]["source"] == "https://sec.gov/x"
        assert (basis["article"]["weight"], basis["company"]["weight"], basis["routeCeiling"]) == \
            (1.0, 1.0, 1.0)

    def test_a_category_link_has_no_company_half(self):
        conn = _conn(0.6, route="category", via="memory_storage", article_magnitude=None,
                     holding_sign=None, holding_magnitude=None, holding_confidence=None,
                     holding_why=None, holding_source=None)
        f = self._fields(20.0, [_tag(0, "low", "high")], [conn])

        basis = f["holdingBasis"]
        assert (basis["label"], basis["company"]) == ("Memory and storage", None)
        assert (basis["article"]["weight"], basis["routeCeiling"]) == (1.0, 0.6)

    def test_every_holding_tied_at_the_top_strength_is_named_once(self):
        """Navier–Stokes' shape: one tag at 1.00 on three holdings, not "Amazon"."""
        conns = [_conn(1.0, isin="US3"), _conn(1.0, isin="US1"),
                 _conn(1.0, isin="US1", route="category", via="memory_storage"),
                 _conn(1.0, isin="US2", route="category", via="memory_storage"),
                 _conn(0.33, isin="US4")]
        basis = self._fields(60.0, [_tag(0)], conns)["holdingBasis"]

        assert (basis["holding"], basis["route"]) == ("Micron", "mechanism")
        assert [(t["isin"], t["route"]) for t in basis["tiedWith"]] == \
            [("US3", "mechanism"), ("US2", "category")]

    def test_a_lone_top_link_has_no_ties(self):
        basis = self._fields(60.0, [_tag(0)], [_conn(1.0), _conn(0.99, isin="US2")])["holdingBasis"]
        assert basis["tiedWith"] == []

    def test_no_qualifying_link_means_no_holding_basis(self):
        f = self._fields(60.0, [_tag(0)], [_conn(0.6, route="lab_exposure")])
        assert f["holdingBasis"] is None
        assert (f["holdingScore"], f["rankLead"]) == (0.0, "event")
