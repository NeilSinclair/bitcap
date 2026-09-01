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

    def test_confidence_is_thirds_not_a_tuned_curve(self, rules):
        """Confidence must share magnitude's 1-3 scale.

        v1 used 1.0/0.7/0.4, which is not a constant multiple of thirds and
        overpaid low-confidence tags by up to 20%.
        """
        assert rules["confidence"] == {"high": 3, "medium": 2, "low": 1}
        assert rules["magnitude"] == {"high": 3, "medium": 2, "low": 1}
        assert rules["max_mechanism"] == 9

    def test_axes_normalised_by_their_own_maxima(self, rules):
        assert rules["max_event_weight"] == max(rules["event_weight"].values())
        assert rules["max_mechanism"] == (
            max(rules["magnitude"].values()) * max(rules["confidence"].values())
        )

    def test_expected_magnitude_symmetry(self, rules):
        """magnitude x confidence is an expected magnitude, so a big uncertain
        effect equals a small certain one."""
        big_unsure = score_of(result(mechanisms=[mech("high", "low")]), rules)[0]
        small_sure = score_of(result(mechanisms=[mech("low", "high")]), rules)[0]
        assert big_unsure == small_sure

    def test_confidence_scales_the_score(self, rules):
        hi = score_of(result(mechanisms=[mech(confidence="high")]), rules)[0]
        med = score_of(result(mechanisms=[mech(confidence="medium")]), rules)[0]
        low = score_of(result(mechanisms=[mech(confidence="low")]), rules)[0]
        assert hi > med > low > 0

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
