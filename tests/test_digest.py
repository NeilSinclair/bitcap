"""The digest: the cut, the grouping, and the record of what was said.

The silent failures these catch, all of which produce a plausible-looking
digest:

* **The cut stops cutting.** A threshold read wrong, or a rule that matches
  everything, turns the digest back into the dashboard. The `suppressed` count
  is the only thing that would say so, so it is asserted directly rather than
  inferred from the item list.
* **Fan-out returns.** One article that touches fourteen holdings must be one
  row naming a few and counting the rest. Rendered per connection it is fourteen
  rows for one fact — the exact noise the digest exists to remove
  (docs/insights.md), and it would look like a *fuller* digest, not a broken one.
* **The citation guarantee lapses in the payload.** A published digest has to
  resolve on its own, after the corpus has moved on. An item without its quote
  and source URL still renders and still reads fine.
* **A republished edition silently forks.** Re-running a firing must update the
  edition it already published; a second row for the same window means two
  different answers to "what did you say on Tuesday", both true-looking.
* **The AI side surfaces non-decisions.** A `watch` tag is the null action.
  Admitting it pads the digest with items whose own analysis says do nothing.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import digest, models as m
from app.db import create_all

# `config` is a namespace package spanning the working tree and the copy
# force-included into the installed distribution, so its resolution order is not
# stable under pytest. Import the module by path, as tests/test_validate.py does.
sys.path.insert(0, str(Path(__file__).parent.parent / "config"))
from validate import check_digest  # noqa: E402

END = datetime(2026, 9, 4, tzinfo=timezone.utc)
V = "v7"

CONFIG = {
    "version": 1,
    "window_hours": 48,
    "investment": {"min_strength": 0.5, "always_band": "high", "max_items": 8},
    "ai": {"actions": ["adopt", "investigate"], "min_band": "medium", "max_items": 8},
    "max_holdings_shown": 4,
}


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        s.add_all([
            m.RefLab(id="xai", label="xAI", config_version=1),
            m.RefMechanism(id="training_compute_up", label="Training compute up",
                           description="d", polarity_note="p", config_version=2),
            m.RefPractice(id="evals", label="Evaluation technique", description="d",
                          dimensions={}, config_version=2),
        ])
        s.flush()
        yield s


def _holding(session, isin, name):
    session.add(m.Holding(isin=isin, name=name, custodian_name=name, aliases=[],
                          ticker=name[:4].upper(), weight_pct=1.0, ai_role="primary",
                          holdings_version=1, companies_version=1))


def _article(session, *, published, title="Grok 4.5", score=80.0, band="high",
             ai_score=50.0, ai_band="medium", url=None):
    """One classified article. Returns (article, classification)."""
    url = url or f"https://x.ai/{title.replace(' ', '-').lower()}"
    raw = m.RawArticle(url=url, payload={}, content_hash=url, source_file="a.json")
    session.add(raw)
    session.flush()
    art = m.Article(url=url, raw_article_id=raw.id, lab="xai", title=title,
                    published_on=published, text="body", text_source="full_text")
    session.add(art)
    session.flush()
    cls = m.Classification(article_id=art.id, prompt_version=V, scoring_version=4,
                           event_type="model_release", summary="s", is_signal=True,
                           notable=True, notable_reason="r", dropped_tags=[],
                           score=score, band=band, ai_score=ai_score, ai_band=ai_band)
    session.add(cls)
    session.flush()
    return art, cls


def _connect(session, art, isin, strength):
    session.add(m.Connection(article_id=art.id, isin=isin, route="mechanism",
                             via="training_compute_up", direction="positive",
                             strength=strength, article_sign="positive",
                             holding_sign="positive", holding_why="why"))


class TestTheCutIsTheProduct:
    """Suppression is the claim the digest makes; it has to be counted."""

    def test_an_item_below_strength_and_below_band_is_suppressed(self, session):
        _holding(session, "US1", "NVIDIA")
        art, _ = _article(session, published=date(2026, 9, 3), score=20.0, band="low")
        _connect(session, art, "US1", 0.2)
        session.flush()

        out = digest.build(session, "investment", V, END, CONFIG)

        # `collapsed` is separate from `suppressed` on purpose: these lost on
        # merit, a collapsed item was never a candidate because another row in
        # the edition already says the same thing.
        assert out["stats"] == {"considered": 1, "surfaced": 0, "suppressed": 1,
                                "matched_rule": 0, "collapsed": 0}
        assert out["items"] == []

    def test_suppressed_counts_items_lost_to_the_cap_not_just_to_the_rule(self, session):
        """A cap is a cut too. A reader asking "what did you not tell me" gets one number."""
        _holding(session, "US1", "NVIDIA")
        for i in range(10):
            art, _ = _article(session, published=date(2026, 9, 3), title=f"Post {i}")
            _connect(session, art, "US1", 0.9)
        session.flush()

        out = digest.build(session, "investment", V, END, CONFIG)

        assert out["stats"]["considered"] == 10
        assert out["stats"]["matched_rule"] == 10   # every one passed the rule
        assert out["stats"]["surfaced"] == 8        # the cap cut two
        assert out["stats"]["suppressed"] == 2

    def test_editions_partition_the_timeline_under_the_deployed_cadence(self, session):
        """The cron is daily (render.yaml) and the window is 48h. Spacing the
        two editions exactly one window apart tests a cadence nobody runs; over
        the real one, an unquantised window made every article appear twice."""
        _holding(session, "US1", "NVIDIA")
        for d in (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)):
            art, _ = _article(session, published=d, title=f"On {d}")
            _connect(session, art, "US1", 0.9)
        session.flush()

        # Six consecutive *daily* firings, as deployed.
        seen: dict[int, list] = {}
        for day in range(2, 8):
            at = datetime(2026, 9, day, 3, 0, tzinfo=timezone.utc)
            out = digest.build(session, "investment", V, at, CONFIG)
            for item in out["items"]:
                seen.setdefault(item["id"], []).append(out["window_end"])

        for article_id, window_ends in seen.items():
            assert len(set(window_ends)) == 1, (
                f"article {article_id} appeared in {len(set(window_ends))} editions"
            )

    def test_firings_inside_one_period_resolve_to_the_same_edition(self, session):
        """Two firings a day apart share a 48h period, so they are one edition."""
        first = digest.window_for(datetime(2026, 9, 4, 3, 0, tzinfo=timezone.utc), CONFIG)
        second = digest.window_for(datetime(2026, 9, 5, 3, 0, tzinfo=timezone.utc), CONFIG)

        assert first == second

    def test_the_period_grid_is_anchored_not_relative_to_the_run(self, session):
        """The bug: `run.started_at - 48h` is microsecond-unique per firing, so
        the idempotence key could never collide and every run forked an edition."""
        a = digest.window_for(datetime(2026, 9, 4, 3, 0, 7, 412000, tzinfo=timezone.utc), CONFIG)
        b = digest.window_for(datetime(2026, 9, 4, 3, 41, 55, 9, tzinfo=timezone.utc), CONFIG)

        assert a == b
        assert a[1] - a[0] == timedelta(hours=CONFIG["window_hours"])
        # Anchored on the grid, so both edges are exact multiples of the width.
        assert (a[0] - digest.EPOCH) % timedelta(hours=CONFIG["window_hours"]) == timedelta(0)

    def test_a_48h_window_spans_two_calendar_days_not_three(self, session):
        _holding(session, "US1", "NVIDIA")
        for d in (date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4)):
            art, _ = _article(session, published=d, title=f"On {d}")
            _connect(session, art, "US1", 0.9)
        session.flush()

        out = digest.build(session, "investment", V, END, CONFIG)

        assert out["stats"]["considered"] == 2

    def test_an_article_outside_the_window_is_never_considered(self, session):
        _holding(session, "US1", "NVIDIA")
        art, _ = _article(session, published=date(2026, 8, 1))
        _connect(session, art, "US1", 0.9)
        session.flush()

        out = digest.build(session, "investment", V, END, CONFIG)

        assert out["stats"]["considered"] == 0


class TestTheUnitIsTheEventNotTheConnection:
    """One sentence firing against seven holdings is one row, not seven."""

    def test_one_article_touching_many_holdings_is_one_item(self, session):
        for i in range(7):
            _holding(session, f"US{i}", f"Holding {i}")
        art, _ = _article(session, published=date(2026, 9, 3))
        for i in range(7):
            _connect(session, art, f"US{i}", 0.6 + i / 100)
        session.flush()

        out = digest.build(session, "investment", V, END, CONFIG)

        assert len(out["items"]) == 1
        holdings = out["items"][0]["holdings"]
        assert holdings["total"] == 7
        assert len(holdings["named"]) == CONFIG["max_holdings_shown"]
        assert holdings["more"] == 3
        # Named strongest-first, so the collapsed remainder is the weakest tail.
        assert [h["strength"] for h in holdings["named"]] == sorted(
            [h["strength"] for h in holdings["named"]], reverse=True)

    def test_a_holding_linked_by_two_routes_is_named_once_at_its_strongest(self, session):
        _holding(session, "US1", "NVIDIA")
        art, _ = _article(session, published=date(2026, 9, 3))
        _connect(session, art, "US1", 0.6)
        session.add(m.Connection(article_id=art.id, isin="US1", route="category",
                                 via="semis", direction="positive", strength=0.9))
        session.flush()

        out = digest.build(session, "investment", V, END, CONFIG)
        holdings = out["items"][0]["holdings"]

        assert holdings["total"] == 1
        assert holdings["named"][0]["strength"] == 0.9


class TestInvestmentSelection:
    def test_a_high_band_article_with_no_connection_still_surfaces(self, session):
        """The early signal: a lab-level shift that has not reached the book yet."""
        art, _ = _article(session, published=date(2026, 9, 3), score=90.0, band="high")
        session.flush()

        out = digest.build(session, "investment", V, END, CONFIG)

        assert len(out["items"]) == 1
        assert out["items"][0]["holdings"]["total"] == 0

    def test_a_band_only_item_does_not_name_holdings_below_the_threshold(self, session):
        """It was selected *because* it touches nothing yet. Rendering its weak
        links reads as "this hits NVIDIA" beside a peakStrength of 0.0."""
        _holding(session, "US1", "NVIDIA")
        _holding(session, "US2", "AMD")
        art, _ = _article(session, published=date(2026, 9, 3), score=90.0, band="high")
        _connect(session, art, "US1", 0.2)
        _connect(session, art, "US2", 0.1)
        session.flush()

        [item] = digest.build(session, "investment", V, END, CONFIG)["items"]

        assert item["holdings"]["named"] == []
        assert item["holdings"]["total"] == 0
        assert item["peakStrength"] == 0.0
        # Counted, so the card can say they exist without claiming them.
        assert item["belowThreshold"] == 2

    def test_a_low_band_article_with_no_connection_does_not(self, session):
        _article(session, published=date(2026, 9, 3), score=10.0, band="low")
        session.flush()

        assert digest.build(session, "investment", V, END, CONFIG)["items"] == []

    def test_items_rank_on_connection_strength_before_score(self, session):
        """A weaker headline that reaches the book harder outranks a louder one."""
        _holding(session, "US1", "NVIDIA")
        loud, _ = _article(session, published=date(2026, 9, 3), title="Loud", score=95.0)
        _connect(session, loud, "US1", 0.55)
        close, _ = _article(session, published=date(2026, 9, 3), title="Close", score=61.0)
        _connect(session, close, "US1", 0.95)
        session.flush()

        out = digest.build(session, "investment", V, END, CONFIG)

        assert [i["title"] for i in out["items"]] == ["Close", "Loud"]


class TestAiSelection:
    def _with_practice(self, session, action, ai_band="medium"):
        art, cls = _article(session, published=date(2026, 9, 3),
                            ai_score=55.0, ai_band=ai_band)
        session.add(m.ArticlePractice(classification_id=cls.id, practice_id="evals",
                                      action=action, impact="high", confidence="high",
                                      dimensions=["evals"], reason="r",
                                      quote="a verbatim sentence", ordinal=0))
        session.flush()
        return art

    def test_watch_is_the_null_action_and_is_suppressed(self, session):
        self._with_practice(session, "watch")

        out = digest.build(session, "ai", V, END, CONFIG)

        assert out["items"] == []
        assert out["stats"]["suppressed"] == 1

    @pytest.mark.parametrize("action", ["adopt", "investigate"])
    def test_an_actionable_practice_surfaces(self, session, action):
        self._with_practice(session, action)

        out = digest.build(session, "ai", V, END, CONFIG)

        assert len(out["items"]) == 1
        assert out["items"][0]["practices"][0]["action"] == action

    def test_an_actionable_practice_below_the_band_floor_does_not(self, session):
        self._with_practice(session, "adopt", ai_band="low")

        assert digest.build(session, "ai", V, END, CONFIG)["items"] == []

    def test_the_two_audiences_read_the_same_article_differently(self, session):
        """One core, two renderers — not two pipelines over two corpora."""
        _holding(session, "US1", "NVIDIA")
        art = self._with_practice(session, "adopt")
        _connect(session, art, "US1", 0.9)
        session.flush()

        inv = digest.build(session, "investment", V, END, CONFIG)["items"][0]
        ai = digest.build(session, "ai", V, END, CONFIG)["items"][0]

        assert inv["id"] == ai["id"]
        assert "holdings" in inv and "practices" not in inv
        assert "practices" in ai and "holdings" not in ai


class TestAPublishedDigestResolvesOnItsOwn:
    """Non-negotiable #1, applied to the report rather than to the insight."""

    def test_every_investment_item_carries_its_quote_and_source_url(self, session):
        _holding(session, "US1", "NVIDIA")
        art, cls = _article(session, published=date(2026, 9, 3))
        session.add(m.ArticleMechanism(
            classification_id=cls.id, mechanism_id="training_compute_up",
            sign="positive", magnitude="high", confidence="high", reason="r",
            quote="trained across tens of thousands of GB300 GPUs", ordinal=0))
        _connect(session, art, "US1", 0.9)
        session.flush()

        item = digest.build(session, "investment", V, END, CONFIG)["items"][0]

        assert item["sourceUrl"].startswith("https://")
        assert item["mechanism"]["quote"] == "trained across tens of thousands of GB300 GPUs"

    def test_the_stored_payload_holds_the_evidence_not_a_reference_to_it(self, session):
        _holding(session, "US1", "NVIDIA")
        art, cls = _article(session, published=date(2026, 9, 3))
        session.add(m.ArticleMechanism(
            classification_id=cls.id, mechanism_id="training_compute_up",
            sign="positive", magnitude="high", confidence="high", reason="r",
            quote="a verbatim sentence", ordinal=0))
        _connect(session, art, "US1", 0.9)
        session.flush()

        digest.publish(session, V, END, config=CONFIG)
        session.commit()

        stored = session.scalar(select(m.Digest).where(m.Digest.kind == "investment"))
        assert stored.payload["items"][0]["mechanism"]["quote"] == "a verbatim sentence"
        assert stored.payload["items"][0]["sourceUrl"].startswith("https://")


class TestPublishing:
    def test_publish_writes_one_edition_per_audience(self, session):
        _article(session, published=date(2026, 9, 3), score=90.0, band="high")
        session.flush()

        rows = digest.publish(session, V, END, config=CONFIG)
        session.commit()

        assert {r.kind for r in rows} == set(digest.KINDS)
        assert session.scalars(select(m.Digest)).all() == rows

    def test_republishing_the_same_window_updates_rather_than_forks(self, session):
        """A re-run must not leave two different answers for one Tuesday."""
        art, _ = _article(session, published=date(2026, 9, 3), score=90.0, band="high")
        session.flush()
        first = digest.publish(session, V, END, config=CONFIG)
        session.commit()
        first_ids = [r.id for r in first]

        _article(session, published=date(2026, 9, 3), title="Second", score=95.0, band="high")
        session.flush()
        again = digest.publish(session, V, END, config=CONFIG)
        session.commit()

        assert [r.id for r in again] == first_ids
        assert len(session.scalars(select(m.Digest)).all()) == len(digest.KINDS)
        investment = next(r for r in again if r.kind == "investment")
        assert investment.stats["considered"] == 2

    def test_a_different_prompt_version_is_a_new_edition_not_a_correction(self, session):
        """Two classifier versions are not comparable, so they are not one report."""
        _article(session, published=date(2026, 9, 3), score=90.0, band="high")
        session.flush()
        digest.publish(session, V, END, config=CONFIG)
        digest.publish(session, "v8", END, config=CONFIG)
        session.commit()

        assert len(session.scalars(select(m.Digest)).all()) == 2 * len(digest.KINDS)

    def test_history_returns_newest_first_with_its_payload(self, session):
        _article(session, published=date(2026, 9, 3), score=90.0, band="high")
        session.flush()
        digest.publish(session, V, END - timedelta(days=2), config=CONFIG)
        digest.publish(session, V, END, config=CONFIG)
        session.commit()

        rows = digest.history(session, kind="investment")

        assert len(rows) == 2
        assert rows[0]["windowEnd"] > rows[1]["windowEnd"]
        assert "items" in rows[0] and "stats" in rows[0]


class TestTheLiveWindowEndsNow:
    """The preview's window is rolling; the published edition's is quantised.

    The silent failure: the grid that makes `publish` idempotent also holds the
    *live* view a period behind. Quantised, the newest day a preview can name is
    the last one that closed — so a reader opening the page on the 5th read
    "the 48 hours to the 3rd" and reasonably concluded the pipeline had stalled.
    Nothing errored; the page just quietly described yesterday's yesterday.
    """

    def test_the_rolling_window_ends_at_the_moment_asked_for(self, session):
        at = datetime(2026, 9, 5, 11, 30, tzinfo=timezone.utc)

        out = digest.build(session, "investment", V, at, CONFIG, quantise=False)

        assert out["window_end"] == at
        assert out["window_start"] == at - timedelta(hours=48)

    def test_it_includes_something_published_today(self, session):
        """The whole point. Quantised, today's article is in no window yet."""
        _holding(session, "US1", "NVIDIA")
        art, _ = _article(session, published=date(2026, 9, 5), title="Today")
        _connect(session, art, "US1", 0.9)
        session.flush()
        at = datetime(2026, 9, 5, 11, 30, tzinfo=timezone.utc)

        rolling = digest.build(session, "investment", V, at, CONFIG, quantise=False)
        quantised = digest.build(session, "investment", V, at, CONFIG)

        assert [i["title"] for i in rolling["items"]] == ["Today"]
        assert quantised["items"] == []

    def test_publishing_still_quantises(self, session):
        """`quantise` defaults True, so nothing that writes picks up the rolling
        window by accident — an unquantised `window_end` is microsecond-unique
        and would fork a new edition on every firing, forever."""
        at = datetime(2026, 9, 5, 11, 30, tzinfo=timezone.utc)

        out = digest.build(session, "investment", V, at, CONFIG)

        assert (out["window_start"], out["window_end"]) == digest.window_for(at, CONFIG)

    def test_a_naive_end_is_read_as_utc(self, session):
        """The endpoint passes an aware datetime, but `window_for` accepts naive
        ones and a rolling branch that did not would compare aware to naive and
        raise — in the one code path the digest page always hits."""
        out = digest.build(session, "investment", V, datetime(2026, 9, 5), CONFIG,
                           quantise=False)

        assert out["window_end"] == datetime(2026, 9, 5, tzinfo=timezone.utc)


class TestGuards:
    def test_an_unknown_audience_raises_rather_than_returning_an_empty_digest(self, session):
        with pytest.raises(ValueError, match="unknown digest kind"):
            digest.build(session, "marketing", V, END, CONFIG)

    def test_an_unrecognised_band_quietens_the_digest_rather_than_crashing_the_run(self, session):
        """A vocabulary change must not take down the firing that publishes."""
        _article(session, published=date(2026, 9, 3), score=90.0, band="stratospheric")
        session.flush()

        assert digest.build(session, "investment", V, END, CONFIG)["items"] == []

    def test_the_shipped_config_passes_its_own_validator(self):
        """The guard that replaced a permissive one.

        The previous version of this test passed against four edits that each
        emptied a digest permanently: `actions: []` (satisfies `<= {...}` — the
        empty set is a subset), `min_band: med`, `always_band: higgh` (both go to
        rank 99 and reject everything), and `window_hours: 6` (below date
        resolution, so `start.date() == end.date()` and nothing is ever in
        window). Vocabulary is now checked by `config/validate.py`.
        """
        assert check_digest() == []

    @pytest.mark.parametrize("mutation,reason", [
        ({"ai": {**CONFIG["ai"], "actions": []}}, "no action can ever match"),
        ({"ai": {**CONFIG["ai"], "min_band": "med"}}, "typo rejects every band"),
        ({"investment": {**CONFIG["investment"], "always_band": "higgh"}}, "typo"),
        ({"window_hours": 6}, "below date resolution — nothing is ever in window"),
    ])
    def test_the_validator_rejects_configs_that_silently_empty_the_digest(
        self, mutation, reason, tmp_path
    ):
        import yaml as _yaml

        path = tmp_path / "digest.yaml"
        path.write_text(_yaml.safe_dump({**CONFIG, **mutation}))

        assert check_digest(path), f"accepted a config that {reason}"
