"""Join rules: sign composition, strength, gates, and the mention matcher.

Fixture values are copied from the real corpus and config (Jalapeño ×
custom_silicon_substitution edges; the export directive × lab exposure), so a
rule change that silently flips a real connection fails here first.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import models as m
from app.connect import connections_for, match_name, mentions, sign_product, weight

RULES = yaml.safe_load((Path(__file__).parent.parent / "config" / "scoring.yaml").read_text())


class TestSignProduct:
    @pytest.mark.parametrize("a,b,want", [
        ("positive", "positive", "positive"),
        ("positive", "negative", "negative"),
        ("negative", "positive", "negative"),
        ("negative", "negative", "positive"),
        ("mixed", "negative", "mixed"),
        ("positive", "mixed", "mixed"),
    ])
    def test_composition(self, a, b, want):
        assert sign_product(a, b) == want


class TestWeight:
    def test_high_high_is_full(self):
        assert weight("high", "high", RULES) == 1.0

    def test_medium_medium(self):
        assert weight("medium", "medium", RULES) == pytest.approx(1 / 3)

    def test_low_confidence_gates_to_zero(self):
        assert weight("high", "low", RULES) == 0.0


def article(**kw):
    defaults = dict(id=1, url="u", lab="openai", title="t", text="")
    defaults.update(kw)
    return m.Article(**defaults)


def mech_tag(mid, sign="positive", magnitude="high", confidence="high"):
    return m.ArticleMechanism(mechanism_id=mid, sign=sign, magnitude=magnitude,
                              confidence=confidence, reason="r", quote="q")


def mech_edge(isin, mid, sign, magnitude, confidence):
    return m.HoldingMechanism(isin=isin, mechanism_id=mid, sign=sign,
                              magnitude=magnitude, confidence=confidence,
                              why="w", source=None)


def holding(isin, name, ticker=None, aliases=None):
    return m.Holding(isin=isin, name=name, ticker=ticker, aliases=aliases or [],
                     custodian_name=name, weight_pct=1.0, ai_role="primary",
                     holdings_version=1, companies_version=2)


def refs(**kw):
    base = {"holding_mechanisms": [], "holding_categories": {}, "lab_edges": [],
            "holdings": [], "routable": {"memory_storage"}}
    base.update(kw)
    return base


class TestMechanismRoute:
    def test_jalapeno_fixture(self):
        """Real values: article tag +/high/high × the four real edge shapes."""
        edges = [
            mech_edge("NVDA", "custom_silicon_substitution", "negative", "medium", "medium"),
            mech_edge("MRVL", "custom_silicon_substitution", "positive", "high", "high"),
            mech_edge("TSM", "custom_silicon_substitution", "positive", "medium", "high"),
        ]
        rows = connections_for(article(), {"mechanisms": [mech_tag("custom_silicon_substitution")],
                                           "categories": []}, 100.0,
                               refs(holding_mechanisms=edges), RULES)
        by_isin = {r.isin: r for r in rows}
        assert (by_isin["NVDA"].direction, by_isin["NVDA"].strength) == ("negative", 0.3333)
        assert (by_isin["MRVL"].direction, by_isin["MRVL"].strength) == ("positive", 1.0)
        assert (by_isin["TSM"].direction, by_isin["TSM"].strength) == ("positive", 0.6667)
        assert all(r.route == "mechanism" and r.holding_why == "w" for r in rows)

    def test_low_confidence_tag_emits_nothing(self):
        rows = connections_for(article(),
                               {"mechanisms": [mech_tag("x", confidence="low")], "categories": []}, 100.0,
                               refs(holding_mechanisms=[mech_edge("A", "x", "positive", "high", "high")]),
                               RULES)
        assert rows == []

    def test_duplicate_tags_keep_strongest(self):
        tags = [mech_tag("x", magnitude="medium"), mech_tag("x", magnitude="high")]
        rows = connections_for(article(), {"mechanisms": tags, "categories": []}, 100.0,
                               refs(holding_mechanisms=[mech_edge("A", "x", "positive", "high", "high")]),
                               RULES)
        assert len(rows) == 1 and rows[0].strength == 1.0


class TestCategoryRoute:
    def cat_tag(self, cid, sign="positive", confidence="high"):
        return m.ArticleCategory(category_id=cid, sign=sign, confidence=confidence,
                                 reason="r", quote="q")

    def test_membership_carries_article_direction(self):
        rows = connections_for(article(), {"mechanisms": [], "categories": [self.cat_tag("memory_storage")]}, 100.0,
                               refs(holding_categories={"memory_storage": ["MU", "SNDK"]}), RULES)
        assert {r.isin for r in rows} == {"MU", "SNDK"}
        assert all(r.direction == "positive" and r.strength == 0.6
                   and r.holding_sign is None for r in rows)

    def test_unroutable_category_skipped(self):
        rows = connections_for(article(), {"mechanisms": [], "categories": [self.cat_tag("crypto")]}, 100.0,
                               refs(holding_categories={"crypto": ["IREN"]}), RULES)
        assert rows == []


class TestLabExposureRoute:
    def lab_edge(self, isin, lab, kind, dormant=False):
        return m.HoldingLabExposure(isin=isin, lab=lab, kind=kind, sign="positive",
                                    magnitude="high", confidence="high", why="w",
                                    source=None, is_dormant=dormant)

    def test_export_directive_fixture(self):
        edges = [self.lab_edge("WULF", "anthropic", "revenue_contract"),
                 self.lab_edge("AMZN", "anthropic", "equity"),
                 self.lab_edge("AMZN", "anthropic", "revenue_contract")]
        rows = connections_for(article(lab="anthropic"), {"mechanisms": [], "categories": []},
                               40.0, refs(lab_edges=edges), RULES)
        assert len(rows) == 3  # Amazon's two edges are two rows, distinct via
        assert {r.via for r in rows} == {"anthropic:revenue_contract", "anthropic:equity"}
        assert all(r.direction == "mixed" and r.strength == 0.6 for r in rows)

    def test_other_lab_and_dormant_edges_do_not_fire(self):
        edges = [self.lab_edge("WULF", "anthropic", "revenue_contract"),
                 self.lab_edge("IREN", "microsoft", "revenue_contract", dormant=True)]
        rows = connections_for(article(lab="openai"), {"mechanisms": [], "categories": []},
                               40.0, refs(lab_edges=edges), RULES)
        assert rows == []

    def test_zero_scoring_article_is_gated_out(self):
        """A routine lab post must not reach its counterparties.

        The edge fires on the publisher, so without this gate every "office
        opening in Brazil" would connect OpenAI's contract partners.
        """
        edges = [self.lab_edge("WULF", "anthropic", "revenue_contract")]
        rows = connections_for(article(lab="anthropic"), {"mechanisms": [], "categories": []},
                               0.0, refs(lab_edges=edges), RULES)
        assert rows == []

    def test_gate_does_not_block_the_route_it_exists_for(self):
        """A funding round scores through its event weight with no mechanism.

        Anthropic's S-1 scores 40 in the real corpus, so the case the route was
        built for — lab capital access moving TeraWulf — survives the gate.
        """
        edges = [self.lab_edge("WULF", "anthropic", "revenue_contract")]
        rows = connections_for(article(lab="anthropic"), {"mechanisms": [], "categories": []},
                               40.0, refs(lab_edges=edges), RULES)
        assert [r.isin for r in rows] == ["WULF"]

    def test_gate_applies_only_to_this_route(self):
        """A zero score must not suppress mechanism, category or named rows."""
        rows = connections_for(
            article(title="NVIDIA news"), {"mechanisms": [mech_tag("x")], "categories": []}, 0.0,
            refs(holding_mechanisms=[mech_edge("A", "x", "positive", "high", "high")],
                 holdings=[holding("NVDA", "NVIDIA Corporation")]),
            RULES)
        assert {r.route for r in rows} == {"mechanism", "named"}


class TestMentions:
    """Names come from companies.yaml (legal), not holdings.yaml (custodian)."""

    @pytest.mark.parametrize("registered,want", [
        ("Micron Technology, Inc.", "Micron"),
        ("NVIDIA Corporation", "NVIDIA"),
        # Dotted registry forms must strip like the bare ones.
        ("BE Semiconductor Industries N.V.", "BE Semiconductor Industries"),
        ("Sociedad Quimica y Minera de Chile S.A.", "Sociedad Quimica y Minera de Chile"),
        ("Taiwan Semiconductor Manufacturing Company Limited", "Taiwan Semiconductor"),
    ])
    def test_suffix_stripping(self, registered, want):
        assert match_name(registered) == want

    def test_empty_token_no_longer_halts_the_strip(self):
        """A trailing "/4" reduces to nothing; it used to strand the tail behind it."""
        got = match_name("Silicon Motion Technology Corp Reg. Shs(Sp. AD Rs)/4")
        assert not got.endswith("/4")
        assert got == "Silicon Motion Technology Corp Reg. Shs Sp."

    @pytest.mark.parametrize("custodian", [
        "Silicon Motion Technology Corp Reg. Shs(Sp. AD Rs)/4",
        "FT Inter Inc. Reg. Shares Cl. Ao. N.",
        "Soc. Quimicay Min.de Chile SAB (Sp. AD Rs)",
    ])
    def test_custodian_strings_are_why_the_legal_name_is_used(self, custodian):
        """No suffix rule rescues these, which is why Holding.name is companies.yaml.

        These are real holdings.yaml values. They are kept as `custodian_name`
        for the tie back to the fund statement, and never matched against.
        """
        derived = match_name(custodian) or ""
        assert derived != "" and " " in derived  # a mangled phrase, not a company
        assert derived not in ("Silicon Motion", "Figure Technology Solutions",
                               "Sociedad Quimica y Minera de Chile")

    def test_word_boundary_prevents_intel_in_intelligence(self):
        hits = mentions("the age of artificial intelligence", [holding("INTC", "Intel Corp.")])
        assert hits == []
        assert mentions("a deal with Intel today", [holding("INTC", "Intel Corp.")]) == \
            [("INTC", "name:Intel")]

    def test_short_tickers_never_fire(self):
        hits = mentions("MU and ON are words", [holding("X", "Zzzz Qqqq Vvvv", ticker="MU")])
        assert hits == []

    def test_ticker_matches_uppercase_word(self):
        assert mentions("results for WULF holders", [holding("W", "Zzzz Qqqq", ticker="WULF")]) == \
            [("W", "ticker:WULF")]

    def test_alias_catches_what_the_legal_name_cannot(self):
        """The real miss: 3 corpus articles say "Amazon", 46 say "AWS", 0 say "Amazon.com"."""
        amzn = holding("AMZN", "Amazon.com, Inc.", aliases=["Amazon", "AWS"])
        assert match_name("Amazon.com, Inc.") == "Amazon.com"
        assert mentions("now available on AWS", [amzn]) == [("AMZN", "alias:AWS")]
        assert mentions("Amazon said today", [amzn]) == [("AMZN", "alias:Amazon")]

    def test_one_hit_per_holding(self):
        """Name and alias both present must not double-count the same company."""
        amzn = holding("AMZN", "Amazon.com, Inc.", aliases=["Amazon", "AWS"])
        assert mentions("Amazon.com and Amazon and AWS", [amzn]) == [("AMZN", "name:Amazon.com")]

    def test_named_route_rows(self):
        rows = connections_for(article(title="Partnership with NVIDIA", text="..."),
                               {"mechanisms": [], "categories": []}, 0.0,
                               refs(holdings=[holding("NVDA", "NVIDIA Corporation")]), RULES)
        assert len(rows) == 1
        # 0.3, not 1.0: a string match must not outrank a corroborated mechanism.
        assert (rows[0].route, rows[0].direction, rows[0].strength) == ("named", "mixed", 0.3)

    def test_route_ceilings_order_the_routes(self):
        """A perfect instance of each route, ranked. This is what the digest sorts on."""
        ceil = RULES["join"]["route_ceiling"]
        assert ceil["mechanism"] > ceil["category"] == ceil["lab_exposure"] > ceil["named"]
