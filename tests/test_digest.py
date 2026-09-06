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
    # Deliberately not 48. The two windows are independent (D79), and a fixture
    # where they agree lets a test read the wrong key and still pass.
    "preview_window_hours": 72,
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
        """The cron is daily (render.yaml) and this fixture's window is 48h --
        WIDER than the shipped 24h, which is deliberate: the property under test
        is that consecutive editions never share an article, and it has to hold
        at every width. It is the harder case, too. At the shipped 24h the grid
        and the cron coincide, so a firing closes exactly one period and an
        overlap bug has nowhere to show; at 48h a daily cron fires twice inside
        one period, which is what made every article appear twice before the
        window was quantised."""
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


class TestTheEditionIsChosenOnMeritAndReadByDate:
    """Two sorts either side of `max_items`, and merging them loses items.

    Display is newest-first so a reader opening the digest sees the latest day
    at the top. Selection stays on `rank`, because the cut happens between the
    two: sort by date before it and the edition fills with whatever is most
    recent, silently dropping a higher-scoring launch from earlier in the
    window. At 48h those were nearly the same set; at 168h they are not.
    """

    def _ai_article(self, session, *, published, title, ai_score):
        art, cls = _article(session, published=published, title=title,
                            ai_score=ai_score, ai_band="high")
        session.add(m.ArticlePractice(
            classification_id=cls.id, practice_id="evals", action="adopt",
            impact="high", confidence="high", dimensions=["evals"], reason="r",
            quote="a verbatim sentence", ordinal=0))
        session.flush()
        return art

    def test_the_latest_day_is_read_first_even_when_it_scores_lower(self, session):
        self._ai_article(session, published=date(2026, 9, 3), title="Older louder",
                         ai_score=90.0)
        self._ai_article(session, published=date(2026, 9, 4), title="Newer quieter",
                         ai_score=60.0)

        items = digest.build(session, "ai", V, END, CONFIG)["items"]

        assert [i["title"] for i in items] == ["Newer quieter", "Older louder"]

    def test_score_still_orders_within_one_day(self, session):
        self._ai_article(session, published=date(2026, 9, 4), title="Quiet",
                         ai_score=55.0)
        self._ai_article(session, published=date(2026, 9, 4), title="Loud",
                         ai_score=95.0)

        items = digest.build(session, "ai", V, END, CONFIG)["items"]

        assert [i["title"] for i in items] == ["Loud", "Quiet"]

    def test_the_cap_keeps_the_best_item_not_the_most_recent(self, session):
        """The regression a single date-first sort would introduce."""
        self._ai_article(session, published=date(2026, 9, 3), title="Astra",
                         ai_score=100.0)
        self._ai_article(session, published=date(2026, 9, 4), title="Minor patch",
                         ai_score=30.0)
        config = {**CONFIG, "ai": {**CONFIG["ai"], "max_items": 1}}

        out = digest.build(session, "ai", V, END, config)

        assert [i["title"] for i in out["items"]] == ["Astra"]
        assert out["stats"]["suppressed"] == 1

    def test_the_investment_edition_reads_by_date_too(self, session):
        _holding(session, "US1", "NVIDIA")
        older, _ = _article(session, published=date(2026, 9, 3), title="Older",
                            score=95.0)
        _connect(session, older, "US1", 0.95)
        newer, _ = _article(session, published=date(2026, 9, 4), title="Newer",
                            score=61.0)
        _connect(session, newer, "US1", 0.55)
        session.flush()

        items = digest.build(session, "investment", V, END, CONFIG)["items"]

        assert [i["title"] for i in items] == ["Newer", "Older"]

    def test_no_item_leaks_its_private_rank_key(self, session):
        # The second sort reads `rank`, so it has to run before the key is
        # deleted; reordering those two lines would ship it to the client.
        self._ai_article(session, published=date(2026, 9, 4), title="One",
                         ai_score=70.0)

        items = digest.build(session, "ai", V, END, CONFIG)["items"]

        assert items and all("rank" not in i for i in items)


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

    def test_a_narrower_window_ending_the_same_midnight_is_a_NEW_edition(self, session):
        """The defect migration 0013 exists for, and it destroyed real data.

        A 48-hour edition covering 04->06 Sep and a 24-hour one covering
        05->06 Sep are different reports over different periods. The idempotence
        key was `(kind, window_end, prompt_version)` — width-blind — so they were
        the same row. Changing `window_hours` from 48 to 24 (D79) therefore did
        not start a new series alongside the archive; it walked *through* it.

        What made it silent rather than merely wrong: `publish` assigns
        `window_start` only when it CREATES a row. Matching an existing one, it
        rewrote the payload and left the old start in place, so the edition went
        on claiming 48 hours while holding 24 hours of content. Measured on the
        live database before the fix, editions 97 and 98 went from 1 item each
        to 0 — a published record quietly restated, which is the one thing the
        frozen-payload design exists to prevent.

        Not an edge case at a 24-hour grid: every 48-hour edition ends on a
        midnight that is also a 24-hour boundary, so a daily cron collides with
        the archive once per old edition, indefinitely.
        """
        _article(session, published=date(2026, 9, 3), score=90.0, band="high")
        session.flush()

        wide = digest.publish(session, V, END, config={**CONFIG, "window_hours": 48})
        session.commit()
        wide_ids = sorted(r.id for r in wide)
        wide_spans = {r.kind: (r.window_start, r.window_end) for r in wide}

        narrow = digest.publish(session, V, END, config={**CONFIG, "window_hours": 24})
        session.commit()

        assert sorted(r.id for r in narrow) != wide_ids, (
            "the 24-hour edition claimed the 48-hour edition's row")
        assert len(session.scalars(select(m.Digest)).all()) == 2 * len(digest.KINDS)

        # And the older edition is untouched — same span, same payload.
        for row in session.scalars(select(m.Digest).where(m.Digest.id.in_(wide_ids))):
            assert (row.window_start, row.window_end) == wide_spans[row.kind]
            assert row.window_end - row.window_start == timedelta(hours=48)

    def test_every_published_edition_spans_the_width_it_claims(self, session):
        """The property the overwrite broke, stated directly.

        A row whose `window_end - window_start` disagrees with the window it was
        built for is a report labelled with a period it does not cover. Nothing
        else looks at that, and the digest page renders it without complaint.
        """
        _article(session, published=date(2026, 9, 3), score=90.0, band="high")
        session.flush()
        for hours in (24, 48, 168):
            digest.publish(session, V, END, config={**CONFIG, "window_hours": hours})
        session.commit()

        widths = sorted({
            round((r.window_end - r.window_start).total_seconds() / 3600)
            for r in session.scalars(select(m.Digest))
        })
        assert widths == [24, 48, 168], (
            f"three widths were published, {widths} survive — editions are "
            "overwriting each other across window changes")

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

    Since D79 they are also different *widths*, read from different keys, which
    is what `test_the_two_windows_do_not_read_each_others_setting` pins.
    """

    def test_the_rolling_window_ends_at_the_moment_asked_for(self, session):
        at = datetime(2026, 9, 5, 11, 30, tzinfo=timezone.utc)

        out = digest.build(session, "investment", V, at, CONFIG, quantise=False)

        assert out["window_end"] == at
        assert out["window_start"] == at - timedelta(
            hours=CONFIG["preview_window_hours"])

    def test_the_two_windows_do_not_read_each_others_setting(self, session):
        """THE POINT OF D79, and the one thing no other test in this file can
        catch.

        `window_hours` and `preview_window_hours` are two keys in one dict read
        by two branches of one function. Swapping them produces a published
        edition and a preview that are both perfectly well-formed, both
        correctly windowed, both rendering without complaint — just the wrong
        way round. Every other test here passes against that swap, because the
        fixture used to carry one width and both branches read it.

        So the widths are deliberately different in `CONFIG` (48 published, 72
        preview) and each branch is asserted against its own.
        """
        at = datetime(2026, 9, 5, 11, 30, tzinfo=timezone.utc)

        published = digest.build(session, "investment", V, at, CONFIG)
        preview = digest.build(session, "investment", V, at, CONFIG,
                               quantise=False)

        assert published["window_end"] - published["window_start"] == timedelta(
            hours=CONFIG["window_hours"]), "the published edition read the preview's width"
        assert preview["window_end"] - preview["window_start"] == timedelta(
            hours=CONFIG["preview_window_hours"]), "the preview read the grid's width"

    def test_the_preview_ignores_the_published_grid_entirely(self, session):
        """Not merely a different number — a different *mechanism*.

        A preview built with an absurd grid width must be unaffected: it does
        not snap, so `window_hours` has no way to reach it. If this fails,
        something is still calling `window_for` on the live path.
        """
        at = datetime(2026, 9, 5, 11, 30, tzinfo=timezone.utc)
        absurd = {**CONFIG, "window_hours": 24 * 30}

        out = digest.build(session, "investment", V, at, absurd, quantise=False)

        assert out["window_end"] == at
        assert out["window_start"] == at - timedelta(
            hours=CONFIG["preview_window_hours"])

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

    def test_the_shipped_windows_are_pinned(self):
        """A tripwire, and the only thing that reads the deployed numbers.

        Every other test in this file runs against the `CONFIG` fixture at the
        top, which carries its own widths. So the shipped values have no guard
        at all: `window_hours` was moved 48 -> 168 -> 24 across three changes
        and the entire suite stayed green each time — which means either can be
        moved back, by a revert or a merge, with nothing going red either.

        This asserts nothing about 24 or 168 being *correct*.
        `config/digest.yaml` says plainly that 168 is not a measured optimum and
        that 24 empties nearly half of all investment editions. It asserts only
        that the numbers change deliberately, alongside these lines and the
        reasoning beside them, rather than drifting.
        """
        settings = digest.settings()
        assert settings["window_hours"] == 24
        assert settings["preview_window_hours"] == 168

    def test_the_two_windows_are_not_the_same_setting(self):
        """The archive being narrower than the live view is the whole point.

        If they are ever equal again the product silently loses the shape D79
        built: a daily dated archive read behind a rolling week. Nothing else
        would report that — both surfaces would keep rendering perfectly.
        """
        settings = digest.settings()
        assert settings["preview_window_hours"] > settings["window_hours"], (
            "the live view must be wider than one published edition, or the "
            "archive and the landing view are the same report twice")

    def test_the_shipped_item_caps_are_pinned(self):
        """The same tripwire, for the same reason, on the other shipped number.

        `CONFIG` at the top of this file carries its own `max_items`, so nothing
        here reads the deployed one. It was raised from 8 to 16 with the whole
        suite green, which means it can be lowered again just as quietly -- and
        on the `ai` audience the cap is what actually bounds the edition (15
        items passed the rule, 8 were shown), so a silent revert halves it.

        Asserts nothing about 16 being right. `config/digest.yaml` records what
        the old cut was measured to be dropping -- seven items, every one of
        them `medium`, five of them X posts -- and says which lever to reach for
        if an edition reads as noisy. This asserts only that the number moves
        deliberately, next to that reasoning.
        """
        settings = digest.settings()
        assert settings["investment"]["max_items"] == 16
        assert settings["ai"]["max_items"] == 16

    def test_the_shipped_ai_merit_cut_is_pinned(self):
        """`min_band` is what actually bounds the AI edition now.

        It was raised to `high` because the edition read as too long, and it is
        the lever that cuts on merit -- `max_items` cuts on position. Measured
        when it was set: `medium` admitted 15 items, `high` admits 8, and the 7
        it removes are the 44.4 and 33.3 scorers, five of them X posts.

        Pinned for the same reason as the window and the caps: nothing else in
        this suite reads the deployed value, so it can be lowered by a revert or
        a merge with everything green, and a digest that quietly doubles is not
        a failure anything would report.
        """
        assert digest.settings()["ai"]["min_band"] == "high"

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

class TestNearDuplicatesDoNotFillTheEdition:
    """An edition carries eight items; two of them saying one thing is a waste.

    The silent failures here:

    * **Publishing a duplicate.** Half the point of the collapse. If folding
      stops, the launch post and its forum restatement both take a slot and the
      cut looks like it worked.
    * **Publishing nothing instead.** The opposite, and worse, because it reads
      as "nothing happened". Both cases below come from trusting
      `ArticleGroup.is_anchor`, which is chosen once over the whole corpus on a
      single score — while an edition is one window and one audience.
    """

    def _group(self, session, group_id, articles, anchor):
        for art in articles:
            session.add(m.ArticleGroup(
                article_id=art.id, group_id=group_id, is_anchor=art is anchor,
                group_size=len(articles), method="embedding", reason="cosine 0.91",
            ))
        session.flush()

    def test_only_one_member_of_a_group_is_published(self, session):
        _holding(session, "US1", "NVIDIA")
        launch, _ = _article(session, published=date(2026, 9, 3), title="Launch",
                             score=90.0, band="high")
        echo, _ = _article(session, published=date(2026, 9, 3), title="Launch echo",
                           score=90.0, band="high")
        _connect(session, launch, "US1", 0.9)
        _connect(session, echo, "US1", 0.9)
        self._group(session, "g1", [launch, echo], launch)

        out = digest.build(session, "investment", V, END, CONFIG)

        assert [i["title"] for i in out["items"]] == ["Launch"]
        assert out["stats"]["collapsed"] == 1

    def test_a_group_whose_corpus_anchor_is_out_of_window_still_surfaces(self, session):
        """The release-train shape, and it publishes an empty section if wrong.

        A train spans days, so its corpus-wide anchor can sit outside this
        window. Folding everything against it drops every release that actually
        shipped in the window and the reader sees nothing for a repo that
        shipped twice.
        """
        _holding(session, "US1", "NVIDIA")
        old, _ = _article(session, published=date(2026, 8, 20), title="v1",
                          score=95.0, band="high")
        recent, _ = _article(session, published=date(2026, 9, 3), title="v2",
                             score=60.0, band="high")
        _connect(session, recent, "US1", 0.9)
        self._group(session, "g1", [old, recent], old)   # anchor is out of window

        out = digest.build(session, "investment", V, END, CONFIG)

        assert [i["title"] for i in out["items"]] == ["v2"]

    def test_each_audience_picks_its_own_member(self, session):
        """One anchor for two cuts can delete an item from one of them.

        `event_type` is a multiplicative term in the investment score and absent
        from the AI score, so the member that ranks highest overall can score
        zero on the axis being published — while the member carrying that axis'
        signal is the one folded away. The item then vanishes from that edition
        and is counted as collapsed, so the loss reads as intentional.
        """
        _holding(session, "US1", "NVIDIA")
        investment_side, _ = _article(
            session, published=date(2026, 9, 3), title="Investment side",
            score=90.0, band="high", ai_score=0.0, ai_band="none")
        ai_side, _ = _article(
            session, published=date(2026, 9, 3), title="AI side",
            score=0.0, band="none", ai_score=90.0, ai_band="high")
        _connect(session, investment_side, "US1", 0.9)
        session.add(m.ArticlePractice(
            classification_id=session.scalars(
                select(m.Classification).where(m.Classification.article_id == ai_side.id)
            ).one().id,
            practice_id="evals", action="adopt", impact="high", confidence="high",
            dimensions=[], reason="r", quote="q", ordinal=0))
        self._group(session, "g1", [investment_side, ai_side], investment_side)

        investment = digest.build(session, "investment", V, END, CONFIG)
        ai = digest.build(session, "ai", V, END, CONFIG)

        assert [i["title"] for i in investment["items"]] == ["Investment side"]
        assert [i["title"] for i in ai["items"]] == ["AI side"]


class TestAFoldedCardSaysWhatItStandsFor:
    """The merge is a decision made on the reader's behalf; it has to carry why.

    Before this, the only trace of folding in a published digest was the
    edition-level `collapsed` count — a reader could see that eleven rows were
    folded somewhere and not which card ate what. The dashboard has rendered
    `groupReason` since grouping shipped, so the string already existed; the
    digest simply never passed it through.

    The silent failure guarded here is the *count*, not the presence: reporting
    `ArticleGroup.group_size` instead of the number this edition actually folded
    makes the card claim it speaks for documents that were never candidates in
    this window.
    """

    def _group(self, session, group_id, articles, anchor, reason="cosine 0.91"):
        for art in articles:
            session.add(m.ArticleGroup(
                article_id=art.id, group_id=group_id, is_anchor=art is anchor,
                group_size=len(articles), method="embedding", reason=reason,
            ))
        session.flush()

    def test_a_card_standing_for_two_documents_says_so_and_says_why(self, session):
        _holding(session, "US1", "NVIDIA")
        launch, _ = _article(session, published=date(2026, 9, 3), title="Launch",
                             score=90.0, band="high")
        echo, _ = _article(session, published=date(2026, 9, 3), title="Launch echo",
                           score=90.0, band="high")
        _connect(session, launch, "US1", 0.9)
        _connect(session, echo, "US1", 0.9)
        self._group(session, "g1", [launch, echo], launch)

        item = digest.build(session, "investment", V, END, CONFIG)["items"][0]

        assert item["groupSize"] == 2
        assert item["groupMethod"] == "embedding"
        assert item["groupReason"] == "cosine 0.91"

    def test_a_card_that_folded_nothing_makes_no_claim_at_all(self, session):
        """Absent, not `groupSize: 1`. A card that stands for one document
        should not render a merge badge saying so."""
        _holding(session, "US1", "NVIDIA")
        art, _ = _article(session, published=date(2026, 9, 3), title="Alone",
                          score=90.0, band="high")
        _connect(session, art, "US1", 0.9)
        session.flush()

        item = digest.build(session, "investment", V, END, CONFIG)["items"][0]

        assert "groupSize" not in item
        assert "groupReason" not in item

    def test_the_count_is_what_this_edition_folded_not_the_stored_group(self, session):
        """The regression `ArticleGroup.group_size` would introduce.

        A group can span days — a release train is the standing case — so its
        corpus-wide size counts members published outside this window that were
        never candidates here. Three in the group, two in the window: the card
        stands for the two a reader can actually open, and saying "one of 3"
        would point at a document that is not in this edition.
        """
        _holding(session, "US1", "NVIDIA")
        old, _ = _article(session, published=date(2026, 8, 20), title="v1",
                          score=95.0, band="high")
        recent, _ = _article(session, published=date(2026, 9, 3), title="v2",
                             score=90.0, band="high")
        echo, _ = _article(session, published=date(2026, 9, 3), title="v2 echo",
                           score=60.0, band="high")
        _connect(session, recent, "US1", 0.9)
        _connect(session, echo, "US1", 0.9)
        self._group(session, "g1", [old, recent, echo], old)  # stored size == 3

        out = digest.build(session, "investment", V, END, CONFIG)
        item = out["items"][0]

        assert item["title"] == "v2"
        assert item["groupSize"] == 2, "counted a member outside this window"
        assert out["stats"]["collapsed"] == 1

    def test_one_member_in_window_makes_no_claim_even_inside_a_real_group(self, session):
        """The same rule at the boundary: nothing folded, so nothing claimed."""
        _holding(session, "US1", "NVIDIA")
        old, _ = _article(session, published=date(2026, 8, 20), title="v1",
                          score=95.0, band="high")
        recent, _ = _article(session, published=date(2026, 9, 3), title="v2",
                             score=60.0, band="high")
        _connect(session, recent, "US1", 0.9)
        self._group(session, "g1", [old, recent], old)   # stored group_size == 2

        out = digest.build(session, "investment", V, END, CONFIG)
        item = out["items"][0]

        assert item["title"] == "v2"
        assert "groupSize" not in item, "claimed a fold that this window did not make"
        assert out["stats"]["collapsed"] == 0

    def test_the_group_fields_survive_into_the_published_payload(self, session):
        """`publish` stores what `build` rendered; a field added to the item and
        dropped on the way to the row would show in preview and vanish in the
        archive."""
        _holding(session, "US1", "NVIDIA")
        launch, _ = _article(session, published=date(2026, 9, 3), title="Launch",
                             score=90.0, band="high")
        echo, _ = _article(session, published=date(2026, 9, 3), title="Echo",
                           score=90.0, band="high")
        _connect(session, launch, "US1", 0.9)
        _connect(session, echo, "US1", 0.9)
        self._group(session, "g1", [launch, echo], launch)

        rows = digest.publish(session, V, END, config=CONFIG)
        stored = next(r for r in rows if r.kind == "investment")

        assert stored.payload["items"][0]["groupSize"] == 2
        assert stored.payload["items"][0]["groupReason"] == "cosine 0.91"


class TestTheGroupIsRepresentedByAMemberThatPasses:
    """Two losses the first anchoring rewrite introduced, both reproduced in review.

    The rewrite fixed "one anchor cannot serve two audiences" by choosing the
    speaker on the audience's score. That is not the same as choosing the member
    the audience's *rule* accepts, and the gap swallowed real items.
    """

    def _group(self, session, group_id, articles, method="embedding"):
        for art in articles:
            session.add(m.ArticleGroup(
                article_id=art.id, group_id=group_id, is_anchor=False,
                group_size=len(articles), method=method, reason="grouped",
            ))
        session.flush()

    def test_a_holding_link_is_not_lost_behind_a_higher_scoring_member(self, session):
        """The reproduction: a 0.9 NVIDIA connection vanishing from the digest.

        `_investment_item` gates on connection strength or band; the speaker was
        chosen on `score`. So a group whose top scorer carried no holding link
        emitted nothing, while the member holding the link was already folded —
        and `collapsed` reported it as "another row says this", when no row did.
        """
        _holding(session, "US1", "NVIDIA")
        top, _ = _article(session, published=date(2026, 9, 3), title="Launch",
                          score=60.0, band="medium")
        linked, _ = _article(session, published=date(2026, 9, 3), title="Deep dive",
                             score=50.0, band="medium")
        _connect(session, linked, "US1", 0.9)
        self._group(session, "g1", [top, linked])

        out = digest.build(session, "investment", V, END, CONFIG)

        assert [i["title"] for i in out["items"]] == ["Deep dive"]

    def test_a_release_train_is_represented_by_its_newest_release(self, session):
        """`prefer="latest"` was dead code once no surface read `is_anchor`.

        Within a train every member usually scores the same, so the date
        tie-break decides all 380 release rows. Anchoring earliest names the
        version the repo has left rather than the one it is on.

        This asserts on what the digest publishes, not on `ArticleGroup`. The
        test it replaces checked the `is_anchor` column, which no consumer reads
        any more — it passed while the product did the wrong thing.
        """
        old, _ = _article(session, published=date(2026, 9, 2), title="claude-code v2.1.258",
                          score=0.0, band="none", ai_score=70.0, ai_band="high")
        new, _ = _article(session, published=date(2026, 9, 3), title="claude-code v2.1.260",
                          score=0.0, band="none", ai_score=70.0, ai_band="high")
        for art in (old, new):
            cls = session.scalars(
                select(m.Classification).where(m.Classification.article_id == art.id)
            ).one()
            session.add(m.ArticlePractice(
                classification_id=cls.id, practice_id="evals", action="adopt",
                impact="high", confidence="high", dimensions=[], reason="r",
                quote="q", ordinal=0))
        self._group(session, "g1", [old, new], method="release_train")

        out = digest.build(session, "ai", V, END, CONFIG)

        assert [i["title"] for i in out["items"]] == ["claude-code v2.1.260"]

    def test_a_non_release_group_still_prefers_the_earliest_on_a_tie(self, session):
        """Being early is the product's claim everywhere else."""
        first, _ = _article(session, published=date(2026, 9, 3), title="Launch",
                            score=80.0, band="high")
        later, _ = _article(session, published=date(2026, 9, 4), title="Docs page",
                            score=80.0, band="high")
        self._group(session, "g1", [first, later])

        out = digest.build(session, "investment", V, END, CONFIG)

        assert [i["title"] for i in out["items"]] == ["Launch"]

    def test_a_group_nobody_accepts_is_suppressed_not_collapsed(self, session):
        """"Collapsed" is a claim that another row said it. It has to be true."""
        a, _ = _article(session, published=date(2026, 9, 3), title="Weak one",
                        score=10.0, band="low")
        b, _ = _article(session, published=date(2026, 9, 3), title="Weak two",
                        score=10.0, band="low")
        self._group(session, "g1", [a, b])

        out = digest.build(session, "investment", V, END, CONFIG)

        assert out["items"] == []
        assert out["stats"]["suppressed"] == 1



class TestEverySurfaceReadsTheSameCorpora:
    """The digest's version list exists in two places, and they can disagree.

    `api/main.py:277` reads `DIGEST_VERSIONS`; `app/pipeline/worker.py` builds
    its own tuple, because it honours a `--prompt` override. D69 widened both to
    admit posts. Widening only one would have shown posts in
    `/api/digests/preview` while the published digest omitted them -- two
    surfaces disagreeing while each looks correct, which is the failure D67 and
    D69 are both about.
    """

    def test_the_worker_publishes_the_versions_the_api_previews(self):
        """Drift between the two lists is invisible until someone compares the
        preview with what was actually sent."""
        import inspect

        from app import cli
        from app.pipeline import worker

        call = inspect.getsource(worker._phases).split(
            "digest_mod.publish(")[1].split("run.started_at")[0]
        # The worker names the constants rather than the values, and passes
        # `prompt_version` for the announcements one so `--prompt` still works.
        expected = {cli.PROMPT_VERSION: "prompt_version",
                    cli.PAPER_PROMPT_VERSION: "PAPER_PROMPT_VERSION",
                    cli.POST_PROMPT_VERSION: "POST_PROMPT_VERSION"}
        for version in cli.DIGEST_VERSIONS:
            assert expected[version] in call, (
                f"{version!r} is in DIGEST_VERSIONS but the worker does not "
                f"publish it -- the preview and the published digest disagree")

    def test_posts_are_admitted_to_the_digest(self):
        """D63 held `t1` out while the corpus was unproven; D69 admits it. If
        this reverts, posts silently stop reaching the digest."""
        from app.cli import DIGEST_VERSIONS, POST_PROMPT_VERSION

        assert POST_PROMPT_VERSION in DIGEST_VERSIONS

    def test_posts_still_cannot_raise_a_content_alert(self):
        """Two surfaces, two switches. Reaching a digest a reader chooses to
        open is not the same permission as paging them, and D69 granted only
        the first."""
        import yaml

        from app.cli import POST_PROMPT_VERSION

        config = yaml.safe_load(
            (Path(__file__).parent.parent / "config" / "pipeline.yaml")
            .read_text(encoding="utf-8"))
        assert POST_PROMPT_VERSION in config["alerts"]["content_mute_prompt_versions"], (
            "posts entered the digest; keeping them muted for alerts is the "
            "separate decision D69 deliberately did not take")


class TestTheDigestPageOpensOnTheLiveWindow:
    """The landing view is the rolling week, not the newest published edition.

    This was true before D79 and untested, which made it a default rather than
    a requirement — and D79 turns it into load-bearing behaviour. The published
    archive is now daily 24-hour editions, which `config/digest.yaml` records as
    empty on ~48% of days for the investment audience. That is an acceptable
    archive and an unacceptable landing page. If the page ever opens on the
    newest *edition* instead, roughly every other visit shows a blank digest,
    the pipeline looks broken, and nothing anywhere reports a fault.

    Read from the frontend source, as the class below does. A contract test:
    it checks the wiring exists, not that React honours it.
    """

    FRONTEND = Path(__file__).parent.parent / "frontend" / "app"

    def _source(self):
        return (self.FRONTEND / "digest" / "page.js").read_text(encoding="utf-8")

    def test_the_initial_edition_is_the_unpublished_window(self):
        source = self._source()
        assert 'useState("current")' in source, (
            "the digest page no longer opens on the live window; with a daily "
            "archive that lands a reader on an empty edition every other day")

    def test_switching_audience_returns_to_the_live_window(self):
        """Otherwise a reader who picked an old edition, then switched audience,
        stays on an id belonging to the audience they left."""
        source = self._source()
        assert 'setEditionId("current")' in source

    def test_the_archive_is_read_deep_enough_to_be_an_archive(self):
        """Daily editions halve the calendar depth a fixed row count buys. This
        is not a correctness bound, it is the difference between a dropdown
        covering a month and one covering three weeks -- pinned because the
        number silently means something different since D79."""
        source = self._source()
        assert "limit=30" in source


class TestTheDigestOpensTheSameRecordAsTheDashboard:
    """One panel, imported twice — not two renderings of one article. D72.

    The digest is the surface a reader is meant to live in, and its cards are
    summaries. Before this, the *thinner* view was the one they spent their time
    in, and the full record was a page away. Reproducing the panel inside
    `digest/page.js` would have fixed that by creating two copies to keep in
    step, which is the same failure one step later.

    These read the frontend source, as `tests/test_posts_spine.py` already does
    for the dashboard's doc-type filter. They are contract tests, not render
    tests: what they check is that the wiring exists and that the duplication
    has not come back.

    **They are not the only guard, and an earlier version of this docstring
    wrongly said they were.** `tests/smoke_dashboard_render.js` and
    `tests/smoke_digest_render.js` evaluate both component bodies with the hooks
    stubbed, load `frontend/app/detail.js` for real, and run the decoration over
    a synthetic article — so the extracted logic is executed, not merely
    grepped. Both are required CI steps. The first of them caught this branch:
    the extraction left `decorateItems` undefined in its sandbox and it went red
    while everything here stayed green.
    """

    FRONTEND = Path(__file__).parent.parent / "frontend" / "app"

    def _read(self, *parts):
        return (self.FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")

    def test_the_panel_lives_in_one_file_and_both_pages_import_it(self):
        shared = self._read("detail.js")
        assert "export function DetailPanel(" in shared

        for page in (("page.js",), ("digest", "page.js")):
            source = self._read(*page)
            assert "DetailPanel" in source, f"{page} does not use the shared panel"
            assert "function DetailPanel(" not in source, (
                f"{page} defines its own panel; the two will drift")

    def test_no_page_reimplements_the_panel_body(self):
        """The headings are the cheapest fingerprint of a copied panel."""
        for heading in ("Portfolio impact", "What to do", "Why flagged"):
            hits = [p for p in self.FRONTEND.rglob("*.js")
                    if heading in p.read_text(encoding="utf-8")]
            assert [p.name for p in hits] == ["detail.js"], (
                f'"{heading}" is rendered in {[p.name for p in hits]}, not only detail.js')

    def test_a_digest_card_opens_the_record(self):
        source = self._read("digest", "page.js")
        # Both card types take the opener, and the page fetches the corpus the
        # panel needs — a card wired to a handler with no data behind it is the
        # failure this pair catches.
        assert source.count("onOpen") >= 4
        assert '"/api/items"' in source
        assert "decorateItems(" in source

    def test_a_card_whose_article_left_the_corpus_is_not_clickable(self):
        """Published payloads are frozen; the corpus is not. Measured on the
        live database: 4 of 65 published items name a document that has gone,
        so the guard is load-bearing rather than defensive."""
        source = self._read("digest", "page.js")
        assert "const live = resolve(item);" in source
        assert "live ? () => setSelectedId(live.id) : null" in source

    def test_a_card_resolves_by_url_before_id(self):
        """The id is the stale half of the payload.

        `articles.id` is a surrogate autoincrement key reassigned on every
        rebuild; `articles.url` is unique and is what the document *is*. Both
        are already in the payload. Measured across all 30 published editions,
        65 items: 9 resolve by id (13%), 61 by url (93%) — and editions
        published the same day already resolved zero by id, because another
        session had rebuilt in between.

        Order matters, so this pins it rather than merely checking both are
        mentioned: id-first would silently return the wrong article whenever an
        id had been recycled onto a different document.
        """
        source = self._read("digest", "page.js")
        assert "byUrl[item.sourceUrl] || (isLiveEdition ? byId[item.id] : null)" in source, (
            "url must be tried first; an id-first lookup can hit a recycled id")

    def test_the_id_fallback_is_confined_to_the_live_preview(self):
        """An archived payload's ids are not merely stale, they are *recycled*.

        The autoincrement counter is reused across rebuilds, so id 4454 today may
        be a different document than the one an old card names. Falling back to
        the id there would open the wrong article while looking like it worked —
        strictly worse than the dead card the url lookup set out to fix. A
        preview is built by the process now serving the corpus, so its ids
        cannot be stale and the fallback is safe there.
        """
        source = self._read("digest", "page.js")
        assert 'const isLiveEdition = editionId === "current";' in source

    def test_the_digest_payload_carries_the_url_the_lookup_needs(self, session):
        """The frontend fix is only free while the backend keeps sending it."""
        _holding(session, "US1", "NVIDIA")
        art, _ = _article(session, published=date(2026, 9, 3), score=90.0, band="high")
        _connect(session, art, "US1", 0.9)
        session.flush()

        item = digest.build(session, "investment", V, END, CONFIG)["items"][0]

        assert item["sourceUrl"] == art.url

    def test_the_shared_palette_is_the_dashboards_not_the_digests(self):
        """The bug this nearly shipped.

        `digest/page.js` carries a three-branch `bandStyle` that folds `low` and
        `none` into one style, which is fine where nothing below medium renders.
        The dashboard shows the whole corpus and distinguishes four. Moving the
        digest's version into the shared file would have silently restyled every
        low and unbanded row on the dashboard.
        """
        shared = self._read("detail.js")
        band = shared[shared.index("export function bandStyle"):]
        band = band[:band.index("\n}")]
        assert 'band === "low"' in band, "the four-band dashboard palette was lost"

        action = shared[shared.index("export function actionStyle"):]
        action = action[:action.index("\n}")]
        assert 'action === "investigate"' in action, "the three-action palette was lost"


class TestAlertsSaysWhenItCannotOpenACard:
    """A card that will not open must say why. D75.

    THE FAILURE, and it was mine. The corpus behind the detail panel is fetched
    separately and was caught with `.catch(() => setCorpus([]))`. When that fetch
    fails — an expired session, a restarted API, a 500 — the corpus is empty, so
    `resolve` returns null for every card, every card loses its opener, and the
    page renders perfectly while nothing on it can be clicked.

    No error, no console line, and no visible difference from the legitimate
    state where a document has genuinely left the corpus. To a reader it looks
    exactly like the feature having been broken by whatever changed most
    recently, which is precisely how it was reported.

    Still failure-tolerant: a digest that cannot open its panels is worth more
    than an error page, so the fetch failure is recorded rather than raised. The
    difference is that the page now says which of the two states it is in.
    """

    FRONTEND = Path(__file__).parent.parent / "frontend" / "app"

    def _read(self, *parts):
        return (self.FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")

    def _code(self, *parts):
        """The file with its comments stripped.

        The first version of this test asserted the old silent catch was absent
        and failed against the comment that *quotes* it while explaining why it
        was removed — a test that read the prose instead of the code, and would
        have gone red on an accurate docstring. The fix and the explanation both
        belong in the file, so the test stops reading the explanation.
        """
        out = []
        for line in self._read(*parts).splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*") \
                    or stripped.startswith("/*"):
                continue
            out.append(line)
        return "\n".join(out)

    def test_the_corpus_fetch_failure_is_captured_not_discarded(self):
        code = self._code("digest", "page.js")
        assert "setCorpusError" in code, (
            "the corpus fetch swallows its error; a failed load is "
            "indistinguishable from a document that has left the corpus")
        # The catch must do something with it, not merely have the setter in
        # scope: the fetch chain itself has to reference it.
        chain = code[code.index('apiFetch("/api/items")'):]
        chain = chain[:chain.index("}, []);")]
        assert "setCorpusError" in chain, "the catch still discards the reason"

    def test_the_failure_is_rendered_where_the_reader_will_see_it(self):
        """Captured and not shown is the same outcome one variable later."""
        source = self._read("digest", "page.js")
        assert "corpusError ?" in source
        assert "Cards cannot be opened." in source

    def test_the_error_state_is_distinct_from_still_loading(self):
        """`null` while loading and once loaded, a string only on failure — or
        the banner flashes on every page load."""
        source = self._read("digest", "page.js")
        assert "useState(null)" in source
        # Cleared on success, so a recovered fetch does not leave the banner up.
        assert "setCorpusError(null)" in source

    def test_the_digest_itself_still_renders_without_the_corpus(self):
        """The tolerance half. The cards carry their own summary, quote and
        source URL — a published digest resolves on its own by design — so
        losing the panel data must not cost the reader the edition."""
        source = self._read("digest", "page.js")
        # The items list is gated on `loading`/`items.length`, never on corpus.
        assert "items.length ? (" in source
        assert "corpus.length ? (" not in source

    def test_a_clickable_card_looks_clickable(self):
        """D75. Behaviour without affordance reads as a broken feature.

        The dashboard's rows have carried `className="card"` all along — the
        class holds `cursor: pointer`, a transition, and the hover highlight in
        globals.css. D72 made the Alerts cards open a panel and left them a bare
        `<article>` with inline styles, so they opened when clicked and gave no
        sign they would. Reported as "nothing happens when I mouse over them,
        like the cards do on the dashboard", which is exactly right.

        Conditional on `onOpen`, and that is not decoration: the class promises
        a click unconditionally, so a card whose document has left the corpus
        must not wear it.
        """
        code = self._code("digest", "page.js")
        assert code.count('className={onOpen ? "card" : undefined}') == 2, (
            "both card types must take the shared card class, and only when "
            "they actually open")
        # The class supplies the cursor; an inline one would fight it and mask
        # a missing class.
        assert 'cursor: onOpen ? "pointer" : "default"' not in code

    def test_the_card_class_still_carries_the_hover_state(self):
        """The other half of the pair: the class has to be worth applying."""
        css = (self.FRONTEND / "globals.css").read_text(encoding="utf-8")
        assert ".card:hover" in css
        block = css[css.index(".card:hover"):]
        assert "border-color" in block[:120] and "background" in block[:120]
