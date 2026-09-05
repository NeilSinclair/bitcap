"""Tests for the repository ranking the releases leg is gated on.

Two things here fail silently and neither shows up downstream.

**Commit volume creeping back into the sort.** It was the first instinct, and
the data rejected it: a lab's most newsworthy repositories can be its least
committed, because a code dump pushed by CI looks dead and is news. A blended
score would look reasonable in review and quietly bury them.

**The age cut guessing.** `created_at` has to come from the API. "No commit
before X" means only "dormant until X", so inferring it marks decade-old
archives as newly published — and the error lands on precisely the famous quiet
repositories a star ranking floats to the top, which is where anyone would look
first.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "research" / "github"))

import rank_repos
from rank_repos import is_mirror, rank

NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)
CFG = {"window_days": 90, "new_within_days": 365, "min_stars": 0}


def commit(days_ago, login="alice", name="Alice", email="alice@example.com"):
    """One commit record in the shape `harvest_github.history` returns."""
    return {
        "date": (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z"),
        "login": login,
        "name": name,
        "email": email,
    }


def payload(stars, commits=(), description=None, created_at=None, **kw):
    """A `raw_github_repos.payload`, as the github leg stores it."""
    return {
        "total": len(commits),
        "commits": list(commits),
        "stars": stars,
        "description": description,
        "created_at": created_at,
        **kw,
    }


class TestStarsAreTheOnlyRanking:
    """The test that catches commit weighting creeping back in."""

    def test_a_busy_unstarred_repo_ranks_below_a_quiet_famous_one(self):
        rows = rank([
            ("xai-org", "xai-sdk-python",
             payload(565, [commit(i) for i in range(10)])),
            ("xai-org", "x-algorithm", payload(32610, [commit(1)])),
        ], CFG, NOW)
        assert [r["repo"] for r in rows] == ["x-algorithm", "xai-sdk-python"]

    def test_ten_thousand_commits_do_not_beat_five_thousand_stars(self):
        rows = rank([
            ("o", "busy", payload(5, [commit(1)] * 200)),
            ("o", "starred", payload(5000, [])),
        ], CFG, NOW)
        assert rows[0]["repo"] == "starred"

    def test_ties_break_on_name_so_the_order_is_stable(self):
        rows = rank([("o", "zeta", payload(10)), ("o", "alpha", payload(10))],
                    CFG, NOW)
        assert [r["repo"] for r in rows] == ["alpha", "zeta"]

    def test_commits_are_absent_from_the_row_entirely(self):
        """Not merely unweighted: a row carries no commit field at all, so
        there is nothing for a later sort key to reach for. The activity
        evidence returns with the page that displays it -- until then the
        null-login rule it needed lives in `aggregate_github`, which the people
        register still uses."""
        rows = rank([("o", "r", payload(10, [commit(1), commit(2)]))], CFG, NOW)
        assert not [k for k in rows[0] if "commit" in k or "author" in k]
        assert rows[0]["stars"] == 10

    def test_a_repo_below_the_floor_is_dropped(self):
        rows = rank([("o", "r", payload(3))], {**CFG, "min_stars": 5}, NOW)
        assert rows == []


class TestAgeCut:
    """New versus established, and why it cannot be inferred."""

    def test_a_recent_created_at_is_new(self):
        rows = rank([("o", "r", payload(10, created_at="2026-06-10T00:00:00Z"))],
                    CFG, NOW)
        assert rows[0]["age"] == "new"

    def test_an_old_created_at_is_established(self):
        rows = rank([("o", "r", payload(10, created_at="2022-09-16T00:00:00Z"))],
                    CFG, NOW)
        assert rows[0]["age"] == "established"

    def test_the_whisper_shape_is_established_not_new(self):
        """`openai/whisper` was created in 2022 and its earliest commit inside
        the harvest window is 2026-03-27, because it was dormant and got
        touched once. Inferring the date from that commit reported five of the
        ten most-starred repositories in the corpus as newly published."""
        rows = rank([("openai", "whisper",
                      payload(108390, [commit(160)],
                              created_at="2022-09-16T00:00:00Z"))], CFG, NOW)
        assert rows[0]["age"] == "established"

    def test_a_missing_created_at_is_unknown_not_guessed(self):
        """A repository last walked before the listing overlay existed has no
        date yet. Saying so is honest; guessing marks every dormant famous
        repository as new."""
        rows = rank([("o", "r", payload(10, [commit(5)]))], CFG, NOW)
        assert rows[0]["age"] == "unknown"



class TestMirrorExclusion:
    """Shared with the people register, not re-derived."""

    def test_a_declared_mirror_is_excluded(self):
        rows = rank([("anthropics", "OpenROAD-flow-scripts",
                      payload(0, description="Mirror of the OpenROAD flow"))],
                    CFG, NOW)
        assert rows == []

    def test_the_pattern_is_the_one_the_people_register_uses(self):
        """Two copies of this pattern would let the two views drift into
        excluding different repositories."""
        from aggregate_github import MIRROR_MARKER

        assert rank_repos.MIRROR_MARKER is MIRROR_MARKER

    def test_an_ordinary_description_is_not_a_mirror(self):
        assert not is_mirror("DeepSeek Harness: Everything is a Plugin.")

    def test_no_description_is_not_a_mirror(self):
        assert not is_mirror(None)
