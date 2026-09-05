"""Tests for the release fetch that feeds the announcements leg.

What fails silently here is the cursor. A cursor that never advances re-fetches
and re-classifies the same documents every run and costs real money; a cursor
that advances too far skips releases and the gap is invisible, because a missing
release looks exactly like a week with no release. Both directions are pinned
below.

The caps are the other silent failure. `openai/codex` cut 240 releases in 90
days, so a cap is unavoidable -- but a cap that drops documents without saying
so reads as "we covered everything".

This module holds no state: the cursor lives in `source_state.watermark` and the
documents in `raw_articles`, both in the database, because the deployed
container has no disk (D31). The tests for that persistence live with the
adapter that owns it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "research" / "github"))

import fetch_releases
from fetch_releases import as_announcement, labs, new_releases, next_cursor

TOKEN = "t"


def rel(tag, published, body="notes", draft=False, prerelease=False, name=None):
    """Build a raw release object in the shape the GitHub API returns."""
    return {
        "tag_name": tag,
        "name": name or tag,
        "published_at": published,
        "body": body,
        "draft": draft,
        "prerelease": prerelease,
        "html_url": f"https://github.com/openai/codex/releases/tag/{tag}",
    }


@pytest.fixture
def api(monkeypatch):
    """Serve canned pages to `_call` and record the URLs requested."""
    state = {"pages": [], "urls": []}

    def fake_call(url, token, data=None, retries=4):
        state["urls"].append(url)
        page = int(url.split("&page=")[1])
        body = state["pages"][page - 1] if page <= len(state["pages"]) else []
        return body, {}

    monkeypatch.setattr(fetch_releases, "_call", fake_call)
    return state


class TestCursor:
    """The cursor decides what is new, and both directions fail silently."""

    def test_only_releases_after_the_cursor_are_returned(self, api):
        api["pages"] = [[rel("v3", "2026-09-03T10:00:00Z"),
                         rel("v2", "2026-09-02T10:00:00Z"),
                         rel("v1", "2026-09-01T10:00:00Z")]]
        items, stats = new_releases("openai", "codex", TOKEN, "openai",
                                    cursor="2026-09-02T10:00:00Z",
                                    backfill=5, cap=20, max_pages=3)
        assert [i["tag"] for i in items] == ["v3"]
        assert stats["reached_cursor"] is True

    def test_the_cursor_boundary_is_exclusive_so_nothing_is_re_extracted(self, api):
        """A release exactly at the cursor was returned last run already."""
        api["pages"] = [[rel("v2", "2026-09-02T10:00:00Z")]]
        items, _ = new_releases("openai", "codex", TOKEN, "openai",
                                cursor="2026-09-02T10:00:00Z",
                                backfill=5, cap=20, max_pages=3)
        assert items == []

    def test_a_rerun_with_the_advanced_cursor_finds_nothing(self, api):
        """The whole point: run twice, pay once. Everything else here defends
        this one property from a different direction."""
        page = [rel("v3", "2026-09-03T10:00:00Z"), rel("v2", "2026-09-02T10:00:00Z")]
        api["pages"] = [page]
        first, _ = new_releases("openai", "codex", TOKEN, "openai", None,
                                backfill=5, cap=20, max_pages=3)
        again, _ = new_releases("openai", "codex", TOKEN, "openai",
                                cursor=first[0]["published_at"],
                                backfill=5, cap=20, max_pages=3)
        assert again == []

    def test_paging_stops_at_the_cursor_rather_than_walking_the_history(self, api):
        api["pages"] = [
            [rel(f"v{i}", f"2026-09-{i:02d}T10:00:00Z") for i in range(30, 0, -1)],
            [rel("old", "2025-01-01T10:00:00Z")],
        ]
        new_releases("openai", "codex", TOKEN, "openai",
                     cursor="2026-09-28T10:00:00Z", backfill=5, cap=20,
                     max_pages=3)
        assert len(api["urls"]) == 1

    def test_an_unreached_cursor_is_reported_not_assumed(self, api):
        """Three full pages without finding the cursor means releases were
        missed. Saying `reached_cursor` is a lie the caller cannot detect."""
        api["pages"] = [[rel(f"a{i}", "2026-09-03T10:00:00Z") for i in range(100)],
                        [rel(f"b{i}", "2026-09-02T10:00:00Z") for i in range(100)],
                        [rel(f"c{i}", "2026-09-01T10:00:00Z") for i in range(100)]]
        _, stats = new_releases("openai", "codex", TOKEN, "openai",
                                cursor="2020-01-01T00:00:00Z", backfill=5,
                                cap=20, max_pages=3)
        assert stats["reached_cursor"] is False


class TestCaps:
    """A cap that drops documents quietly reads as full coverage."""

    def test_first_run_takes_only_the_backfill(self, api):
        api["pages"] = [[rel(f"v{i}", f"2026-09-{i:02d}T10:00:00Z")
                         for i in range(20, 0, -1)]]
        items, stats = new_releases("openai", "codex", TOKEN, "openai", None,
                                    backfill=5, cap=20, max_pages=3)
        assert len(items) == 5
        assert stats["truncated"] == 15

    def test_the_backfill_keeps_the_newest_not_the_first_page_order(self, api):
        api["pages"] = [[rel("old", "2026-01-01T10:00:00Z"),
                         rel("new", "2026-09-03T10:00:00Z")]]
        items, _ = new_releases("openai", "codex", TOKEN, "openai", None,
                                backfill=1, cap=20, max_pages=3)
        assert [i["tag"] for i in items] == ["new"]

    def test_truncation_is_counted_so_the_caller_can_report_it(self, api):
        api["pages"] = [[rel(f"v{i}", f"2026-09-{i:02d}T10:00:00Z")
                         for i in range(25, 0, -1)]]
        _, stats = new_releases("openai", "codex", TOKEN, "openai",
                                cursor="2026-08-01T00:00:00Z", backfill=5,
                                cap=20, max_pages=3)
        assert stats["truncated"] == 5


class TestCursorAdvancesWithoutLosingAnything:
    """The cap and the cursor interact, and getting it wrong loses documents
    permanently.

    The first version kept the *newest* `cap` releases and then advanced the
    cursor to the newest of those. Everything the cap dropped sat below the new
    cursor and could never be fetched again: re-running filtered them out, and
    clearing the cursor fell into the backfill branch, which takes the newest
    few and does not reach them either. Nothing downstream could detect the
    gap, because a missing release looks exactly like a week with no release.
    """

    def test_the_cursor_never_advances_past_a_release_that_was_not_taken(self, api):
        api["pages"] = [[rel(f"v{i}", f"2026-09-{i:02d}T10:00:00Z")
                         for i in range(25, 0, -1)]]
        items, stats = new_releases("openai", "codex", TOKEN, "openai",
                                    cursor="2026-08-01T00:00:00Z", backfill=5,
                                    cap=20, max_pages=3)
        dropped = [f"2026-09-{i:02d}T10:00:00Z" for i in (21, 22, 23, 24, 25)]
        assert stats["truncated"] == 5
        assert all(next_cursor(items) < d for d in dropped)

    def test_two_capped_runs_collect_every_release(self, api):
        """The convergence property. Twenty-five releases, a cap of twenty:
        run twice and nothing is missing."""
        page = [rel(f"v{i}", f"2026-09-{i:02d}T10:00:00Z")
                for i in range(25, 0, -1)]
        api["pages"] = [page]
        cursor = "2026-08-01T00:00:00Z"
        collected = []
        for _ in range(2):
            items, _ = new_releases("openai", "codex", TOKEN, "openai", cursor,
                                    backfill=5, cap=20, max_pages=3)
            if not items:
                break
            collected += items
            cursor = next_cursor(items)
        assert len(collected) == 25
        assert len({i["tag"] for i in collected}) == 25

    def test_a_capped_run_takes_a_contiguous_block_from_the_cursor(self):
        """Contiguity is what makes the next run resumable: a gap in the middle
        would be skipped over when the cursor advances past it."""
        api_items = [as_announcement("openai", "codex", "openai",
                                     rel(f"v{i}", f"2026-09-{i:02d}T10:00:00Z"))
                     for i in range(1, 26)]
        oldest_20 = sorted(api_items, key=lambda i: i["published_at"])[:20]
        assert next_cursor(sorted(oldest_20, key=lambda i: i["published_at"],
                                  reverse=True)) == oldest_20[-1]["published_at"]

    def test_a_first_run_still_takes_the_newest(self):
        """Backfill is the deliberate exception: with no cursor there is a
        whole history below and replaying it is not the intent."""
        items = [as_announcement("openai", "codex", "openai",
                                 rel(f"v{i}", f"2026-09-{i:02d}T10:00:00Z"))
                 for i in (1, 2, 3)]
        assert next_cursor(sorted(items, key=lambda i: i["published_at"],
                                  reverse=True)) == "2026-09-03T10:00:00Z"


class TestPagingIsRobustToOutOfOrderReleases:
    """GitHub's release list is ordered by neither `created_at` nor
    `published_at` once a repository interleaves stable and prerelease tags --
    a single page of `openai/codex` carries 24 `published_at` inversions,
    confirmed against the live API. Stopping at the first old release steps
    over the newer ones below it.
    """

    def test_a_newer_release_below_an_older_one_is_still_taken(self, api):
        api["pages"] = [[rel("stable", "2026-09-01T10:00:00Z"),
                         rel("alpha", "2026-09-03T10:00:00Z")]]
        items, _ = new_releases("openai", "codex", TOKEN, "openai",
                                cursor="2026-09-02T00:00:00Z", backfill=5,
                                cap=20, max_pages=3)
        assert [i["tag"] for i in items] == ["alpha"]

    def test_paging_continues_while_a_page_still_holds_something_new(self, api):
        api["pages"] = [
            [rel(f"old{i}", "2026-08-01T10:00:00Z") for i in range(99)]
            + [rel("new", "2026-09-03T10:00:00Z")],
            [rel("newer", "2026-09-04T10:00:00Z")],
        ]
        items, _ = new_releases("openai", "codex", TOKEN, "openai",
                                cursor="2026-08-15T00:00:00Z", backfill=5,
                                cap=20, max_pages=3)
        assert sorted(i["tag"] for i in items) == ["new", "newer"]

    def test_paging_stops_on_a_page_with_nothing_new(self, api):
        api["pages"] = [[rel(f"a{i}", "2026-09-03T10:00:00Z") for i in range(100)],
                        [rel(f"b{i}", "2026-01-01T10:00:00Z") for i in range(100)],
                        [rel("never", "2026-09-04T10:00:00Z")]]
        new_releases("openai", "codex", TOKEN, "openai",
                     cursor="2026-08-01T00:00:00Z", backfill=5, cap=200,
                     max_pages=3)
        assert len(api["urls"]) == 2


class TestWhatCountsAsADocument:
    """Which releases are dropped, and -- more importantly -- which are not."""

    def test_an_empty_body_is_dropped_because_there_is_nothing_to_extract(self, api):
        api["pages"] = [[rel("v2", "2026-09-03T10:00:00Z", body=""),
                         rel("v1", "2026-09-02T10:00:00Z", body="   ")]]
        items, stats = new_releases("openai", "codex", TOKEN, "openai", None,
                                    backfill=5, cap=20, max_pages=3)
        assert items == []
        assert stats["empty"] == 2

    def test_a_short_body_is_kept(self, api):
        """The 359-character codex release carrying GPT-6-Astra is the best
        item the probe found; DeepSeek's 12,534-character release is mostly
        scrollbars. Any length filter throws away the signal."""
        astra = ("## New Features\n\n- Added support for configuring GPT-6-Astra "
                 "through the API without changing the default model or showing "
                 "it in the model picker. (#42605)")
        api["pages"] = [[rel("rust-v0.153.1", "2026-09-03T10:00:00Z", body=astra)]]
        items, _ = new_releases("openai", "codex", TOKEN, "openai", None,
                                backfill=5, cap=20, max_pages=3)
        assert len(items) == 1
        assert "GPT-6-Astra" in items[0]["text"]

    def test_drafts_are_skipped(self, api):
        api["pages"] = [[rel("v2", "2026-09-03T10:00:00Z", draft=True)]]
        items, stats = new_releases("openai", "codex", TOKEN, "openai", None,
                                    backfill=5, cap=20, max_pages=3)
        assert items == []
        assert stats["drafts"] == 1

    def test_prereleases_are_kept(self, api):
        """DeepSeek publishes nothing but -alpha and -rc tags. Filtering
        prereleases would drop the fastest-moving repository in the corpus."""
        api["pages"] = [[rel("dsh-v0.1.2-rc.1", "2026-09-03T10:00:00Z",
                             prerelease=True)]]
        items, _ = new_releases("deepseek-ai", "deepseek-harness", TOKEN,
                                "deepseek", None, backfill=5, cap=20, max_pages=3)
        assert [i["tag"] for i in items] == ["dsh-v0.1.2-rc.1"]
        assert items[0]["prerelease"] is True


class TestAnnouncementShape:
    """Releases enter the existing leg, so they must match its item shape."""

    def test_it_carries_the_announcements_corpus_keys(self):
        item = as_announcement("openai", "codex", "openai",
                               rel("v1", "2026-09-03T10:00:00Z"))
        for key in ("lab", "url", "date", "title", "text", "text_source",
                    "feed_category"):
            assert key in item

    def test_the_lab_is_the_register_id_not_the_org_login(self):
        """`anthropics` is a GitHub login; `anthropic` is the register id the
        announcements corpus uses. Mixing them splits one lab into two."""
        item = as_announcement("anthropics", "claude-code", labs()["anthropics"],
                               rel("v2.1.260", "2026-09-03T10:00:00Z"))
        assert item["lab"] == "anthropic"

    def test_every_tracked_org_maps_to_a_lab(self):
        mapping = labs()
        assert mapping["deepseek-ai"] == "deepseek"
        assert mapping["xai-org"] == "xai"
        assert all(mapping.values())

    def test_the_citation_is_the_release_permalink(self):
        item = as_announcement("openai", "codex", "openai",
                               rel("v1", "2026-09-03T10:00:00Z"))
        assert item["url"].startswith("https://github.com/openai/codex/releases/tag/")

    def test_the_date_is_the_publication_date_not_the_fetch_date(self):
        item = as_announcement("openai", "codex", "openai",
                               rel("v1", "2026-09-03T10:00:00Z"))
        assert item["date"] == "2026-09-03"
