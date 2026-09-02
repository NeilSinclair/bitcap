"""Tests for announcement fetching and scoring.

The silent failures this suite exists to catch: a hallucinated mechanism id
reaching the register as though it were a real transmission path, a scoring
rule that quietly stops distinguishing signal from noise, and a date parser
that places an old release inside the window.
"""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "announcements"))

from fetch_announcements import date_from_page, date_from_slug, strip_html


@pytest.fixture(scope="module")
def rules():
    return yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())


def score_of(result, rules):
    """Import lazily: score_announcements imports the anthropic SDK."""
    from score_announcements import score_of as fn

    return fn(result, rules)


def mech(magnitude="high", confidence="high", mid="training_compute_up"):  # noqa: D103
    return {
        "id": mid,
        "sign": "positive",
        "magnitude": magnitude,
        "confidence": confidence,
        "reason": "r",
        "quote": "q",
    }


def result(event="frontier_model_release", signal=True, mechanisms=None):
    return {
        "is_signal": signal,
        "event_type": event,
        "mechanisms": mechanisms if mechanisms is not None else [mech()],
        "categories": [],
    }


class TestDateFromSlug:
    def test_six_digit_slug(self):
        assert date_from_slug("https://x/news/news260821") == "2026-08-21"

    def test_short_slug_is_undated_not_guessed(self):
        """A four-digit slug has no year; guessing one could place a 2024
        release inside a 2026 window."""
        assert date_from_slug("https://x/news/news0725") is None

    def test_non_news_url(self):
        assert date_from_slug("https://x/docs/intro") is None


class TestDateFromPage:
    def test_reads_printed_date(self):
        assert date_from_page("Introducing Claude Opus 5 Jul 24, 2026 It is") == "2026-07-24"

    def test_single_digit_day_padded(self):
        assert date_from_page("Posted Mar 3, 2026 today") == "2026-03-03"

    def test_absent_date(self):
        assert date_from_page("no date here at all") is None

    def test_ignores_non_date_numbers(self):
        assert date_from_page("We trained on 24, 2026 GPUs") is None

    def test_full_month_names_are_read(self):
        """Anthropic prints the article's own date in full and the "Related
        posts" footer in abbreviated form. Matching only abbreviations skipped
        the real date and took a footer link's, which put a ten-month-old
        article inside a three-month window -- 316 days off, in the gold set.
        """
        page = (
            "Introducing Agent Skills Category Product announcements "
            "Date October 16, 2025 Reading time 5 min "
            "... Related posts Aug 28, 2026 Claude for Teachers"
        )
        assert date_from_page(page) == "2025-10-16"

    def test_abbreviation_does_not_partially_match_a_full_name(self):
        """`Oct` matching the first three letters of `October` would leave the
        `\\s+` to match `ober`, silently failing the whole date."""
        for text, expected in [
            ("March 2, 2026", "2026-03-02"),
            ("September 3, 2026", "2026-09-03"),
            ("Sept 3, 2026", "2026-09-03"),
            ("Sep 3, 2026", "2026-09-03"),
            ("February 11, 2026", "2026-02-11"),
        ]:
            assert date_from_page(text) == expected, text

    def test_the_first_date_still_wins(self):
        """The header date precedes the footer; order is what makes this work."""
        assert date_from_page("June 1, 2026 body Aug 28, 2026 related") == "2026-06-01"


class TestStripHtml:
    def test_removes_script_and_style(self):
        out = strip_html("<p>keep</p><script>var x=1</script><style>a{}</style>")
        assert "keep" in out and "var x" not in out and "a{}" not in out

    def test_collapses_whitespace(self):
        assert strip_html("<p>a</p>\n\n   <p>b</p>") == "a b"


class TestScore:
    def test_is_signal_does_not_gate_the_score(self, rules):
        """`is_signal` was a hard veto until a stability probe found it flipping
        on 9% of re-classified items, zeroing articles that carried
        maximum-strength, quote-backed tags. The evidence decides the score now;
        the flag is diagnostic only.
        """
        with_flag = score_of(result(signal=True), rules)
        without = score_of(result(signal=False), rules)
        assert with_flag == without
        assert without[0] > 0

    def test_no_mechanism_means_no_score(self, rules):
        """The load-bearing property. Every mechanism tag must quote the
        document, so a multiplicative rule makes "cites a primary source" a
        structural guarantee of any non-zero score rather than a convention.

        An additive variant was tried and abandoned precisely because it let an
        item score on its event label alone, with no tags and no quotes.
        """
        for event in ("corporate_finance", "frontier_model_release", "other"):
            assert score_of(result(event, mechanisms=[]), rules)[0] == 0.0, event

    def test_zero_weight_event_scores_zero_however_it_is_tagged(self, rules):
        """`other` is a true residual: it cannot score, even on a maximum tag."""
        assert score_of(result("other"), rules) == (0.0, "none")

    def test_vocabulary_not_arithmetic_fixed_the_known_failures(self, rules):
        """The two v1 failures were vocabulary gaps, not a formula fault.

        Both clear the high band under the original multiplicative rule once the
        right event type exists.
        """
        s1 = score_of(result("corporate_finance", mechanisms=[mech()]), rules)[0]
        export = score_of(result("regulatory_action", mechanisms=[mech()]), rules)[0]
        assert s1 >= 60 and export >= 60

    def test_maximum_case(self, rules):
        score, band = score_of(result("frontier_model_release", mechanisms=[mech()]), rules)
        assert score == 100.0 and band == "high"

    def test_neither_axis_can_veto_the_other(self, rules):
        """The v1 bug: multiplying let a low event weight crush a maximum
        mechanism. The export control case scored 20; it must now clear 60."""
        assert score_of(result("regulatory_action"), rules)[0] >= 60

    def test_incremental_release_scores_below_frontier(self, rules):
        """Splitting the release types must actually separate them."""
        inc = score_of(result("incremental_model_release", mechanisms=[mech("medium", "high")]), rules)[0]
        front = score_of(result("frontier_model_release", mechanisms=[mech("high", "high")]), rules)[0]
        assert inc < front

    def test_confidence_is_a_gate_not_a_discount(self, rules):
        """Confidence is a probability weight running 0 to 1.

        Changed in v4 on evidence: a hand review of a 20-article gold run found
        that every tag a human rejected as unsupported was already marked low
        confidence by the model, and the old 1-3 scale paid for them anyway.
        Magnitude keeps 1-3 -- a small effect is still an effect; an unsupported
        claim is not.
        """
        assert rules["confidence"] == {"high": 1.0, "medium": 0.5, "low": 0.0}
        assert rules["magnitude"] == {"high": 3, "medium": 2, "low": 1}
        assert rules["max_mechanism"] == 3

    def test_a_low_confidence_tag_scores_nothing(self, rules):
        """The whole point of the v4 change, pinned."""
        for magnitude in ("high", "medium", "low"):
            assert score_of(
                result(mechanisms=[mech(magnitude, "low")]), rules
            )[0] == 0.0, magnitude

    def test_one_confident_tag_rescues_an_otherwise_low_confidence_item(self, rules):
        """The gate is per tag, not per article -- max, not veto."""
        assert score_of(
            result(mechanisms=[mech("low", "low"), mech("high", "high")]), rules
        )[0] > 0

    def test_axes_normalised_by_their_own_maxima(self, rules):
        assert rules["max_event_weight"] == max(rules["event_weight"].values())
        assert rules["max_mechanism"] == (
            max(rules["magnitude"].values()) * max(rules["confidence"].values())
        )

    def test_a_small_certain_effect_beats_a_big_unsupported_one(self, rules):
        """The v3 rule made these equal. That was backwards.

        In a system whose main risk is fabricated evidence, "a big thing we may
        be wrong about" must not outrank "a small thing the document states".
        """
        big_unsure = score_of(result(mechanisms=[mech("high", "low")]), rules)[0]
        small_sure = score_of(result(mechanisms=[mech("low", "high")]), rules)[0]
        assert big_unsure == 0.0
        assert small_sure > big_unsure

    def test_confidence_scales_the_score(self, rules):
        hi = score_of(result(mechanisms=[mech(confidence="high")]), rules)[0]
        med = score_of(result(mechanisms=[mech(confidence="medium")]), rules)[0]
        low = score_of(result(mechanisms=[mech(confidence="low")]), rules)[0]
        assert hi > med > low
        assert low == 0.0
        assert med == hi / 2, "medium is half of high, not two thirds"

    def test_strongest_mechanism_wins(self, rules):
        """Score takes the max, not the sum: many weak tags must not out-score
        one strong one."""
        weak = [mech(magnitude="low", confidence="low") for _ in range(6)]
        strong = [mech(magnitude="high", confidence="high")]
        assert score_of(result(mechanisms=weak), rules)[0] < score_of(
            result(mechanisms=strong), rules
        )[0]

    def test_unknown_event_type_scores_zero(self, rules):
        """A model inventing an event type must get 0, never a default."""
        assert score_of(result("something_invented"), rules)[0] == 0.0

    def test_bands_are_ordered_and_cover_zero(self, rules):
        mins = [b["min"] for b in rules["bands"]]
        assert mins == sorted(mins, reverse=True)
        assert mins[-1] == 0


class TestDropUnknownTags:
    def test_removes_hallucinated_ids(self):
        from score_announcements import drop_unknown_tags

        r = {
            "mechanisms": [mech(mid="training_compute_up"), mech(mid="invented_thing")],
            "categories": [{"id": "memory_storage"}, {"id": "not_a_category"}],
        }
        dropped = drop_unknown_tags(r, {"training_compute_up"}, {"memory_storage"})
        assert [m["id"] for m in r["mechanisms"]] == ["training_compute_up"]
        assert [c["id"] for c in r["categories"]] == ["memory_storage"]
        assert sorted(dropped) == [
            "categories:not_a_category",
            "mechanisms:invented_thing",
        ]

    def test_keeps_everything_valid(self):
        from score_announcements import drop_unknown_tags

        r = {"mechanisms": [mech()], "categories": []}
        assert drop_unknown_tags(r, {"training_compute_up"}, set()) == []


class TestConfigIntegrity:
    def test_every_event_type_in_prompt_has_a_weight(self, rules):
        """A type the prompt offers but the rule omits would score zero
        silently."""
        from score_announcements import PROMPT

        prompt = PROMPT.read_text()
        block = prompt.split("## Event type", 1)[1].split("## Output", 1)[0]
        offered = set(__import__("re").findall(r"`([a-z_]+)`", block))
        assert offered, "no event types found in prompt"
        assert offered <= set(rules["event_weight"]), (
            offered - set(rules["event_weight"])
        )

    def test_crypto_is_not_routable(self):
        cats = yaml.safe_load((ROOT / "config" / "categories.yaml").read_text())
        crypto = next(c for c in cats["categories"] if c["id"] == "crypto")
        assert crypto["lab_signal_routable"] is False

    def test_sources_declare_a_known_method(self):
        cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
        for lab in cfg["labs"]:
            assert lab["method"] in {"sitemap", "rss"}
            assert lab["text_source"] in {"full_text", "rss_summary"}


class _LazyScoreAnnouncements:
    """Defer importing score_announcements until a test actually touches it.

    Same reason as the wrapper functions above: the module imports the Anthropic
    SDK at module scope, and the fetch/parse tests must run without it.
    """

    def __getattr__(self, name):
        import score_announcements

        return getattr(score_announcements, name)


sa = _LazyScoreAnnouncements()

class TestPracticeAxis:
    """The AI-team axis: tag validation and the second scoring rule.

    Written before the first paid run over the gold set, so a wiring error shows
    up here rather than in $0.30 of classifications.
    """

    @staticmethod
    def _tag(**kw):
        base = {
            "id": "orchestration", "action": "watch", "impact": "low",
            "confidence": "low", "dimensions": [], "reason": "r", "quote": "q",
        }
        base.update(kw)
        return base

    def test_no_practice_tag_scores_zero(self, rules):
        """The citation guarantee, mirrored onto the AI axis."""
        assert sa.ai_score_of({"practices": []}, rules) == (0.0, "none")

    def test_a_missing_practices_key_scores_zero(self, rules):
        """Results classified before v5 have no practices key at all."""
        assert sa.ai_score_of({}, rules) == (0.0, "none")

    def test_adopt_high_high_is_the_maximum(self, rules):
        score, band = sa.ai_score_of(
            {"practices": [self._tag(action="adopt", impact="high", confidence="high")]},
            rules,
        )
        assert (score, band) == (100.0, "high")

    def test_neither_axis_dominates_the_other(self, rules):
        """`action` is an axis, not an override.

        A high-impact, clearly-real development with nothing to do about it yet
        outranks a trivial thing that happens to be adoptable today. That is the
        multiplicative form doing its job, the same way event weight does not
        dominate mechanism strength on the investment side. Pinned because the
        tempting alternative -- sorting by action first -- would bury exactly the
        articles an AI team most wants to see early.
        """
        adopt_trivial = sa.ai_score_of(
            {"practices": [self._tag(action="adopt", impact="low", confidence="low")]},
            rules,
        )[0]
        watch_major = sa.ai_score_of(
            {"practices": [self._tag(action="watch", impact="high", confidence="high")]},
            rules,
        )[0]
        # Under the v4 confidence gate a low-confidence tag scores zero on
        # either axis, so the comparison is made at medium confidence.
        adopt_trivial = sa.ai_score_of(
            {"practices": [self._tag(action="adopt", impact="low", confidence="medium")]},
            rules,
        )[0]
        assert adopt_trivial == 16.7
        assert watch_major == 33.3
        assert watch_major > adopt_trivial

    def test_action_separates_two_otherwise_identical_tags(self, rules):
        """Holding impact and confidence fixed, action orders the result."""
        scores = [
            sa.ai_score_of(
                {"practices": [self._tag(action=a, impact="high", confidence="high")]},
                rules,
            )[0]
            for a in ("watch", "investigate", "adopt")
        ]
        assert scores == sorted(scores) and len(set(scores)) == 3, scores

    def test_the_strongest_tag_wins_not_the_sum(self, rules):
        """Same max-not-sum rule as the investment score."""
        one = sa.ai_score_of(
            {"practices": [self._tag(action="adopt", impact="high", confidence="high")]},
            rules,
        )[0]
        many = sa.ai_score_of(
            {"practices": [
                self._tag(action="adopt", impact="high", confidence="high"),
                self._tag(id="evaluation"),
                self._tag(id="integration"),
            ]},
            rules,
        )[0]
        assert one == many == 100.0

    def test_the_two_scores_are_independent(self, rules):
        """An article can be pure noise to investors and top of the AI list."""
        result = {
            "event_type": "other", "mechanisms": [],
            "practices": [self._tag(action="adopt", impact="high", confidence="high")],
        }
        assert sa.score_of(result, rules)[0] == 0.0
        assert sa.ai_score_of(result, rules)[0] == 100.0


class TestPracticeTagValidation:
    """drop_unknown_tags on the practice axis."""

    @staticmethod
    def _result(practices):
        return {"mechanisms": [], "categories": [], "practices": practices}

    def test_an_unknown_practice_id_is_dropped(self):
        r = self._result([{"id": "vibes", "dimensions": []}])
        dropped = sa.drop_unknown_tags(r, set(), set(), {"orchestration"}, {})
        assert r["practices"] == []
        assert dropped == ["practices:vibes"]

    def test_an_unknown_dimension_is_dropped_but_the_tag_survives(self):
        r = self._result([{"id": "orchestration", "dimensions": ["telepathy"]}])
        dropped = sa.drop_unknown_tags(
            r, set(), set(), {"orchestration"}, {"orchestration": set()}
        )
        assert len(r["practices"]) == 1
        assert r["practices"][0]["dimensions"] == []
        assert dropped == ["practices:orchestration/dimension:telepathy"]

    def test_model_capability_without_a_valid_dimension_is_dropped_entirely(self):
        """An undifferentiated 'it got better' is what the field exists to stop."""
        r = self._result([{"id": "model_capability", "dimensions": []}])
        dropped = sa.drop_unknown_tags(
            r, set(), set(), {"model_capability"}, {"model_capability": {"coding"}}
        )
        assert r["practices"] == []
        assert "no valid dimension" in dropped[0]

    def test_a_valid_dimension_is_kept(self):
        r = self._result([{"id": "model_capability", "dimensions": ["coding", "x"]}])
        dropped = sa.drop_unknown_tags(
            r, set(), set(), {"model_capability"}, {"model_capability": {"coding"}}
        )
        assert r["practices"][0]["dimensions"] == ["coding"]
        assert dropped == ["practices:model_capability/dimension:x"]

    def test_dimensions_are_capped_and_the_excess_is_reported(self):
        """Three big launches named 8, 7 and 7 of 9 dimensions on the first
        run, which says only "this is a big launch". The prompt asks for three;
        the cap is enforced here because the prompt asking did not work."""
        r = {"mechanisms": [], "categories": [], "practices": [{
            "id": "model_capability",
            "dimensions": ["coding", "reasoning", "latency", "cost", "reliability"],
        }]}
        dropped = sa.drop_unknown_tags(
            r, set(), set(), {"model_capability"},
            {"model_capability": {"coding", "reasoning", "latency", "cost", "reliability"}},
            3,
        )
        assert r["practices"][0]["dimensions"] == ["coding", "reasoning", "latency"]
        assert dropped == [
            "practices:model_capability/over-cap:cost",
            "practices:model_capability/over-cap:reliability",
        ], "the excess must be recorded, not silently truncated"

    def test_a_tag_at_the_cap_is_untouched(self):
        r = {"mechanisms": [], "categories": [], "practices": [{
            "id": "model_capability", "dimensions": ["coding", "reasoning", "cost"],
        }]}
        allowed = {"model_capability": {"coding", "reasoning", "cost"}}
        assert sa.drop_unknown_tags(r, set(), set(), {"model_capability"}, allowed, 3) == []
        assert len(r["practices"][0]["dimensions"]) == 3

    def test_no_cap_configured_means_no_truncation(self):
        r = {"mechanisms": [], "categories": [], "practices": [{
            "id": "model_capability", "dimensions": ["coding", "reasoning", "cost"],
        }]}
        allowed = {"model_capability": {"coding", "reasoning", "cost"}}
        sa.drop_unknown_tags(r, set(), set(), {"model_capability"}, allowed, None)
        assert len(r["practices"][0]["dimensions"]) == 3

    def test_the_cap_is_read_from_config_not_hardcoded(self):
        assert sa.dimension_cap() == 3

    def test_omitting_practice_ids_leaves_the_axis_untouched(self):
        """Callers that predate v5 must not have their practices silently wiped."""
        r = self._result([{"id": "anything", "dimensions": []}])
        sa.drop_unknown_tags(r, set(), set())
        assert len(r["practices"]) == 1


class TestPracticeVocabulary:
    """The config the prompt is built from."""

    def test_every_placeholder_is_filled(self):
        system, _ = sa.build_prompt({
            "lab": "l", "date": "d", "url": "u",
            "text_source": "full_text", "text": "t",
        })
        for placeholder in ("{mechanisms}", "{categories}", "{practices}"):
            assert placeholder not in system, f"{placeholder} left unfilled"

    def test_the_schema_asks_for_practices(self):
        assert "practices" in sa.build_schema()["properties"]
        assert "practices" in sa.build_schema()["required"]

    def test_model_capability_declares_dimensions(self):
        dims = sa.practice_dimensions()
        assert dims["model_capability"], "model_capability has no dimensions"
        assert dims["orchestration"] == set(), "only model_capability takes dimensions"

    def test_practice_ids_never_collide_with_mechanism_ids(self):
        _, _, _, mech_ids, _, prac_ids = sa.vocabularies()
        assert not (mech_ids & prac_ids)


class TestStripHtmlEntities:
    """Entity decoding, added after a real failure.

    The stored corpus held literal `&#x27;` and `&amp;` because strip_html never
    unescaped. The model reads that text and quotes the decoded form, so a
    verbatim quote check reported 9 false hallucinations out of 90 tags. The
    quotes were correct; the corpus was wrong.
    """

    def test_common_entities_are_decoded(self):
        assert strip_html("<p>compute &amp; memory</p>") == "compute & memory"
        assert strip_html("<p>it&#x27;s</p>") == "it's"
        assert strip_html("<p>&quot;quoted&quot;</p>") == '"quoted"'

    def test_double_encoded_entities_are_decoded(self):
        """Anthropic's pages carry these; one unescape pass is not enough."""
        assert strip_html("<p>it&amp;#x27;s</p>") == "it's"

    def test_comments_are_removed(self):
        assert "secret" not in strip_html("<p>keep</p><!-- secret -->")

    def test_a_quote_from_stripped_text_matches_the_stripped_text(self):
        """The property that actually matters for the citation guarantee."""
        page = "<article><p>reduced compute &amp; memory costs</p></article>"
        text = strip_html(page)
        assert "reduced compute & memory costs" in text
