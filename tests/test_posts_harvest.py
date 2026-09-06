"""The posts leg: budget, rate measurement, and the prefilter.

The silent failures this suite exists to catch:

  - A spend ceiling that does not bind. X bills per post returned, so a leg that
    paginates or mis-clamps spends real money with no error.
  - Reading "the probe returned fewer posts than we asked for" as "that is all
    there is". X applies `exclude=replies,retweets` after assembling a page, so
    a prolific replier returns few originals from a small page. This one already
    happened: @sama measured as 4 posts in 90 days when the true figure is
    ~223, because the count was trusted instead of the oldest post's date.
  - Budgeting an unknown handle at its page size rather than its expected
    return, which lets eight silent accounts reserve the whole balance and
    starve the handles that carry the volume.
  - A departed person's timeline read into their former lab's corpus, which
    config/people.yaml calls that file's most likely failure.
  - A prefilter that measures length before stripping links and mentions, so
    "congrats @someone <emoji> <link>" reads as substance.
  - A record reaching `transform` without the fields it hard-requires.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "research" / "posts"))

import harvest_x as hx  # noqa: E402
import prefilter  # noqa: E402
import x_client as xc  # noqa: E402

NOW = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def cfg():
    return xc.settings()


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def _post(pid="1", text="x" * 200, created="2026-08-01T10:00:00.000Z",
          urls=None, quoted=None):
    post = {"id": pid, "text": text, "created_at": created}
    if urls:
        post["entities"] = {"urls": [{"expanded_url": u} for u in urls]}
    if quoted:
        post["referenced_tweets"] = [{"type": "quoted", "id": quoted}]
    return post


PERSON = {"lab": "openai", "name": "Sam Altman", "role": "CEO",
          "handle": "sama", "x_evidence": "search_index", "role_contested": False}


class TestTheSpendCeilingBinds:
    """A ceiling that reports overspend instead of preventing it is a report."""

    def test_a_reservation_past_the_ceiling_is_refused(self):
        spend = xc.Spend(max_posts=100, max_users=10)
        spend.bill_posts(95)
        with pytest.raises(xc.SpendCeiling) as caught:
            spend.reserve_posts(10)
        assert caught.value.would_be == 105
        assert caught.value.limit == 100

    def test_reserving_exactly_to_the_ceiling_is_allowed(self):
        spend = xc.Spend(max_posts=100, max_users=10)
        spend.reserve_posts(100)
        with pytest.raises(xc.SpendCeiling):
            spend.reserve_posts(101)

    def test_user_lookups_have_their_own_ceiling(self):
        spend = xc.Spend(max_posts=1000, max_users=5)
        with pytest.raises(xc.SpendCeiling) as caught:
            spend.reserve_users(6)
        assert caught.value.resource == "users"

    def test_cost_uses_the_published_rates(self):
        spend = xc.Spend(1000, 100, post_usd=0.005, user_usd=0.010)
        spend.bill_posts(200)
        spend.bill_users(27)
        assert spend.usd == pytest.approx(200 * 0.005 + 27 * 0.010)

    def test_max_results_outside_the_api_range_is_rejected(self):
        spend = xc.Spend(1000, 10)
        for bad in (1, 4, 101):
            with pytest.raises(ValueError):
                xc.user_posts("1", "t", spend, max_results=bad,
                              start_time=NOW, exclude=[])

    def test_the_pull_stops_rather_than_overrunning_the_ceiling(self, cfg, monkeypatch):
        """The budget running out must be reported, not silently truncate."""
        def _billed(user_id, token, spend, *, max_results, **kw):
            # Emulate the real call: reserve, then bill what comes back.
            spend.reserve_posts(max_results)
            posts = [_post(f"{user_id}-{i}") for i in range(max_results)]
            spend.bill_posts(len(posts))
            return posts

        monkeypatch.setattr(xc, "user_posts", _billed)
        people = [{**PERSON, "handle": f"h{i}", "user_id": str(i)} for i in range(10)]
        caps = {f"h{i}": 100 for i in range(10)}
        spend = xc.Spend(max_posts=12, max_users=40)
        records, unresolved = hx.pull(people, caps, "token", spend, cfg, now=NOW)
        assert spend.posts <= 12
        # Handles it could not reach are named, not dropped.
        assert unresolved, "a truncated pull that looks complete is unrecoverable"
        assert all("budget" in u["reason"] for u in unresolved)
        assert {u["kind"] for u in unresolved} == {"post"}


class TestAPartialPageIsNotACompleteAnswer:
    """The @sama regression: trusting the count instead of the oldest date."""

    def test_a_sample_reaching_back_across_the_window_is_complete(self):
        created = [_ago(d) for d in (2, 40, 87)]
        assert hx.projected_count(created, 5, 90, NOW) == 3

    def test_a_full_page_of_recent_posts_extrapolates_rather_than_counting(self):
        # Five posts spanning two days is a busy account, not an account with
        # five posts in ninety days.
        created = [_ago(d) for d in (0.1, 0.5, 1.0, 1.5, 2.0)]
        assert hx.projected_count(created, 5, 90, NOW) > 100

    def test_four_posts_dated_yesterday_are_not_four_posts_in_the_window(self):
        # Verbatim the shape that produced the wrong number in the live probe.
        created = [_ago(d) for d in (0.2, 0.7, 1.2, 1.6)]
        assert hx.projected_count(created, 5, 90, NOW) > 50

    def test_an_empty_probe_is_unknown_and_not_zero(self):
        # A first page consisting entirely of replies is indistinguishable from
        # silence at this sample size, and must not be recorded as silence.
        assert hx.projected_count([], 5, 90, NOW) is None

    def test_a_burst_inside_one_minute_cannot_project_infinity(self):
        created = [NOW - timedelta(seconds=s) for s in (1, 2, 3, 4, 5)]
        assert hx.projected_count(created, 5, 90, NOW) < 500_000


class TestTheBudgetGoesWhereTheVolumeIs:
    """Allocation decides what the money buys; it must not be self-defeating."""

    def test_an_unknown_handle_gets_a_full_page_because_silence_is_free(self, cfg):
        caps = hx.allocate({"quiet": None}, 800, cfg)
        assert caps["quiet"] == 100

    def test_unknown_handles_do_not_consume_the_budget_they_will_not_spend(self, cfg):
        """Eight silent accounts must not starve the prolific ones."""
        projections = {f"silent{i}": None for i in range(8)}
        projections["loud"] = 500
        caps = hx.allocate(projections, 826, cfg)
        assert caps["loud"] >= 100, "prolific handle was starved by silent ones"

    def test_a_prolific_handle_is_capped_at_the_api_maximum(self, cfg):
        caps = hx.allocate({"loud": 5000}, 800, cfg)
        assert caps["loud"] == 100

    def test_no_handle_is_asked_for_more_than_it_is_projected_to_have(self, cfg):
        # Reservation is what binds against the ceiling, so asking 100 of an
        # account projected at 40 spends 60 posts of headroom on nothing.
        caps = hx.allocate({"a": 40, "b": 5000}, 800, cfg)
        assert caps["a"] == 40

    def test_a_handle_with_nothing_is_skipped_entirely(self, cfg):
        # The API floor is 5, so the only way to spend zero is not to call.
        assert "dormant" not in hx.allocate({"dormant": 0}, 800, cfg)

    def test_the_api_floor_is_respected_for_a_nearly_silent_handle(self, cfg):
        assert hx.allocate({"rare": 1}, 800, cfg)["rare"] == 5

    def test_allocation_is_deterministic(self, cfg):
        projections = {"a": 500, "b": None, "c": 12, "d": 0}
        assert hx.allocate(projections, 826, cfg) == hx.allocate(projections, 826, cfg)


class TestTheRegisterDecidesWhoIsRead:
    """Handles come from config/people.yaml, so there is one list of who is where."""

    def test_departed_people_are_never_read(self):
        people, _ = hx.register()
        register = yaml.safe_load((ROOT / "config" / "people.yaml").read_text())
        departed = {p["name"] for block in register["labs"].values()
                    for p in (block.get("departed") or [])}
        assert not {p["name"] for p in people} & departed

    def test_a_configured_exclusion_is_recorded_not_silently_dropped(self):
        _, skipped = hx.register()
        assert skipped, "an excluded handle must still be visible as a choice"
        assert all(s["reason"] for s in skipped)

    def test_excluded_handles_are_absent_from_the_read_list(self, cfg):
        people, _ = hx.register()
        excluded = {h.lower() for h in cfg["exclude_handles"]}
        assert not {p["handle"].lower() for p in people} & excluded

    def test_every_person_read_carries_the_evidence_for_their_handle(self):
        # An attribution with no recorded evidence tier cannot be rendered
        # honestly, so it must not reach the corpus at all.
        people, _ = hx.register()
        assert people
        assert all(p["x_evidence"] for p in people)


class TestTheRecordSatisfiesTheSpine:
    """`transform` hard-requires five fields; a missing date aborts the run."""

    def test_a_record_carries_every_field_transform_reads(self):
        record = hx.to_record(_post(), PERSON)
        for field in ("lab", "title", "date", "text", "text_source"):
            assert record[field], f"transform hard-requires {field}"

    def test_the_date_is_parseable_because_an_unparseable_one_is_a_poison_pill(self):
        from datetime import date
        assert date.fromisoformat(hx.to_record(_post(), PERSON)["date"])

    def test_the_url_is_the_post_permalink_so_a_quote_resolves_where_cited(self):
        record = hx.to_record(_post(pid="123"), PERSON)
        assert record["url"] == "https://x.com/sama/status/123"

    def test_the_text_is_stored_as_written(self):
        # Quotes are enforced against the stored text, so it may never be a
        # cleaned-up version of what the author posted.
        raw = "we shipped it 🎉 https://t.co/abc @someone"
        assert hx.to_record(_post(text=raw), PERSON)["text"] == raw

    def test_the_evidence_tier_travels_with_the_post(self):
        assert hx.to_record(_post(), PERSON)["x_evidence"] == "search_index"

    def test_a_quote_post_is_marked_as_one(self):
        assert hx.to_record(_post(quoted="99"), PERSON)["is_quote"] is True
        assert hx.to_record(_post(), PERSON)["is_quote"] is False


class TestThePrefilterMeasuresSubstanceNotCharacters:
    """Length before stripping counts furniture as content."""

    def test_links_mentions_and_emoji_are_not_substance(self):
        assert prefilter.substance("congrats @someone 🎉 https://t.co/abc") == "congrats"

    def test_a_link_only_post_is_dropped(self, cfg):
        record = {"text": "https://t.co/abc", "links": ["https://openai.com/x"]}
        assert prefilter.verdict(record, cfg["prefilter"]) == "link only: no text of its own"

    def test_a_short_congratulation_is_dropped(self, cfg):
        record = {"text": "check our model out! https://t.co/abc",
                  "links": ["https://openai.com/x"]}
        assert prefilter.verdict(record, cfg["prefilter"])

    def test_a_substantive_post_survives_even_carrying_a_link(self, cfg):
        record = {"text": "Today we are launching Gemini 3.8 Flash Cyber, our most "
                          "capable cybersecurity model for finding and fixing "
                          "vulnerabilities. It sits on the Pareto Frontier on CWE "
                          "benchmarks. https://t.co/abc",
                  "links": ["https://blog.google/x"]}
        assert prefilter.verdict(record, cfg["prefilter"]) is None


class TestTheOverlapNumberIsAnExactMatch:
    """The unique-signal claim rests on this, so it may not be fuzzy."""

    def test_a_post_linking_a_known_document_is_marked_a_duplicate(self, cfg):
        record = hx.to_record(_post(urls=["https://openai.com/index/gpt-6"]), PERSON)
        kept, _ = prefilter.apply([record], cfg["prefilter"],
                                  {"https://openai.com/index/gpt-6"})
        assert kept[0]["duplicates_announcement"] is True
        assert kept[0]["duplicate_of"] == "https://openai.com/index/gpt-6"

    def test_a_trailing_slash_does_not_hide_a_duplicate(self, cfg):
        record = hx.to_record(_post(urls=["https://openai.com/index/gpt-6/"]), PERSON)
        kept, _ = prefilter.apply([record], cfg["prefilter"],
                                  {"https://openai.com/index/gpt-6"})
        assert kept[0]["duplicates_announcement"] is True

    def test_an_unrelated_link_is_not_a_duplicate(self, cfg):
        record = hx.to_record(_post(urls=["https://example.com/other"]), PERSON)
        kept, _ = prefilter.apply([record], cfg["prefilter"], {"https://openai.com/x"})
        assert kept[0]["duplicates_announcement"] is False

    def test_dropped_posts_keep_their_reason(self, cfg):
        record = hx.to_record(_post(text="nice!"), PERSON)
        _, dropped = prefilter.apply([record], cfg["prefilter"], set())
        # "we filtered this" and "we never saw this" must not look the same.
        assert dropped[0]["dropped"]


class TestTheConfigTheCodeReadsIsReal:
    """A validator passing over a config the code does not actually read."""

    def test_the_committed_config_carries_every_block_the_code_reads(self, cfg):
        for block in ("provider", "window_days", "exclude", "budget", "probe",
                      "allocation", "prefilter"):
            assert block in cfg

    def test_the_ceiling_is_a_real_number_not_a_placeholder(self, cfg):
        assert cfg["budget"]["max_posts_total"] > 0

    def test_replies_and_retweets_are_excluded_server_side(self, cfg):
        # These are what make the leg affordable; dropping them from config
        # silently multiplies the bill.
        assert set(cfg["exclude"]) == {"replies", "retweets"}

    def test_the_host_interval_is_configured_where_the_other_fetcher_reads_it(self):
        pipeline = yaml.safe_load((ROOT / "config" / "pipeline.yaml").read_text())
        assert "api.x.com" in pipeline["fetch"]["min_interval_seconds"]
        assert xc.interval("api.x.com") > 0


class TestTheWindowIsBoundedByWhatWeAlreadyHave:
    """The leg re-bought its whole window every firing.

    `start_time` is server-side and X bills per post *returned*, so the window
    is the bill. `fetch_posts` wrote `max_published` on every firing and never
    read it back, which at a weekly cadence meant paying for the same 90 days
    of posts once a week for ever. These pin the read, not the write.
    """

    def test_a_watermark_narrows_the_window_it_does_not_widen_it(self, cfg, monkeypatch):
        seen = {}

        def _capture(user_id, token, spend, *, max_results, start_time, **kw):
            seen["start"] = start_time
            return []

        monkeypatch.setattr(xc, "user_posts", _capture)
        spend = xc.Spend(max_posts=100, max_users=40)
        hx.pull([PERSON | {"user_id": "1"}], {"sama": 10}, "t", spend, cfg,
                now=NOW, since=_ago(3))
        assert seen["start"] == _ago(3), "the watermark must bound the pull"

    def test_a_stale_watermark_cannot_widen_the_window(self, cfg, monkeypatch):
        """A mark older than the window would re-buy more than config allows."""
        seen = {}

        def _capture(user_id, token, spend, *, max_results, start_time, **kw):
            seen["start"] = start_time
            return []

        monkeypatch.setattr(xc, "user_posts", _capture)
        spend = xc.Spend(max_posts=100, max_users=40)
        hx.pull([PERSON | {"user_id": "1"}], {"sama": 10}, "t", spend, cfg,
                now=NOW, since=_ago(9999))
        assert seen["start"] == xc.window_start(cfg["window_days"], NOW)

    def test_no_watermark_reads_the_whole_window(self, cfg, monkeypatch):
        """First firing, or a leg whose state was lost, must still backfill."""
        seen = {}

        def _capture(user_id, token, spend, *, max_results, start_time, **kw):
            seen["start"] = start_time
            return []

        monkeypatch.setattr(xc, "user_posts", _capture)
        spend = xc.Spend(max_posts=100, max_users=40)
        hx.pull([PERSON | {"user_id": "1"}], {"sama": 10}, "t", spend, cfg, now=NOW)
        assert seen["start"] == xc.window_start(cfg["window_days"], NOW)

    def test_a_narrower_window_actually_costs_less(self, cfg, monkeypatch):
        """The whole point. Billing is per post returned, so fewer posts is less money."""
        def _server_side(user_id, token, spend, *, max_results, start_time, **kw):
            # Emulate X: `start_time` is applied before the page is assembled.
            everything = [_post(f"p{d}", created=_ago(d).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
                          for d in (1, 10, 40, 80)]
            posts = [p for p in everything
                     if datetime.strptime(p["created_at"], "%Y-%m-%dT%H:%M:%S.000Z")
                     .replace(tzinfo=timezone.utc) >= start_time][:max_results]
            spend.reserve_posts(max_results)
            spend.bill_posts(len(posts))
            return posts

        monkeypatch.setattr(xc, "user_posts", _server_side)
        people, caps = [PERSON | {"user_id": "1"}], {"sama": 100}

        full = xc.Spend(max_posts=1000, max_users=40)
        hx.pull(people, caps, "t", full, cfg, now=NOW)

        incremental = xc.Spend(max_posts=1000, max_users=40)
        hx.pull(people, caps, "t", incremental, cfg, now=NOW, since=_ago(5))

        assert full.posts == 4
        assert incremental.posts == 1
        assert incremental.usd < full.usd, "a bounded window must cost less"
