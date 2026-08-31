"""Tests for the GitHub contributor aggregation.

The failure this suite exists to catch is a silent one: an alias rule that
merges two different people, or an employment signal that quietly promotes a
guess to evidence. Both would corrupt the register without raising anything.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "research" / "github"))

from aggregate_github import PRIVATE_DOMAIN, aggregate, alias_pairs, is_bot


def person(commits, emails=(), **kw):
    """Build a minimal per-login record for alias_pairs."""
    return {"commits": commits, "emails": set(emails), **kw}


class TestIsBot:
    @pytest.mark.parametrize(
        "login",
        [
            "actions-user",
            "github-actions",
            "stainless-app[bot]",
            "stainless-bot",
            "anthropic-sdks-writer[bot]",
            "claude[bot]",
            "dependabot",
            "web-flow",
        ],
    )
    def test_machine_accounts_rejected(self, login):
        assert is_bot(login)

    @pytest.mark.parametrize(
        "login", ["bcherny", "dtmeadows-ant", "RobertCraigie", "ashwin-ant", "robot-han"]
    )
    def test_humans_kept(self, login):
        assert not is_bot(login)

    def test_bot_substring_does_not_trigger(self):
        """'robotics' contains 'bot' but is not a bot account."""
        assert not is_bot("robotics-lab")


class TestAliasPairs:
    def test_shared_real_email_merges(self):
        people = {
            "dtmeadows": person(10, ["d@anthropic.com"]),
            "dtmeadows-ant": person(40, ["d@anthropic.com"]),
        }
        assert alias_pairs(people) == {"dtmeadows": "dtmeadows-ant"}

    def test_canonical_is_the_busier_account(self):
        people = {
            "quiet": person(2, ["x@anthropic.com"]),
            "busy": person(90, ["x@anthropic.com"]),
        }
        assert alias_pairs(people) == {"quiet": "busy"}

    def test_private_noreply_never_links_accounts(self):
        """The decisive case: no-reply addresses are per-account, not per-person.

        Trusting them would merge two strangers.
        """
        people = {
            "alice": person(5, [f"1+alice@{PRIVATE_DOMAIN}"]),
            "bob": person(5, [f"2+bob@{PRIVATE_DOMAIN}"]),
        }
        assert alias_pairs(people) == {}

    def test_identical_noreply_still_ignored(self):
        people = {
            "alice": person(5, [f"same@{PRIVATE_DOMAIN}"]),
            "bob": person(5, [f"same@{PRIVATE_DOMAIN}"]),
        }
        assert alias_pairs(people) == {}

    def test_work_suffix_merges_when_stem_present(self):
        people = {"craigie": person(3), "craigie-ant": person(9)}
        assert alias_pairs(people) == {"craigie-ant": "craigie"}

    def test_work_suffix_alone_does_not_merge(self):
        """A work handle with no matching personal handle stays as itself."""
        people = {"ashwin-ant": person(20), "bcherny": person(30)}
        assert alias_pairs(people) == {}

    def test_no_self_merge(self):
        people = {"solo": person(4, ["s@anthropic.com"])}
        assert alias_pairs(people) == {}

    def test_chains_collapse_to_one_canonical(self):
        """a -> b by email, b -> c by email: everything must land on c."""
        people = {
            "a": person(1, ["shared1@anthropic.com"]),
            "b": person(5, ["shared1@anthropic.com", "shared2@anthropic.com"]),
            "c": person(50, ["shared2@anthropic.com"]),
        }
        merges = alias_pairs(people)
        assert set(merges.values()) == {"c"}
        assert sorted(merges) == ["a", "b"]


class TestAggregate:
    @pytest.fixture
    def harvest(self, tmp_path, monkeypatch):
        """Write a synthetic harvest file and point the module at it."""
        import aggregate_github

        monkeypatch.setattr(aggregate_github, "DOCS", tmp_path)

        def write(repos):
            (tmp_path / "github_commits_testorg.json").write_text(json.dumps(repos))

        return write

    def test_bots_excluded_but_counted(self, harvest):
        harvest(
            {
                "repo": {
                    "total": 3,
                    "commits": [
                        {"date": "2026-01-01T00:00:00Z", "login": "actions-user",
                         "name": "bot", "email": "b@x.com"},
                        {"date": "2026-01-02T00:00:00Z", "login": "human",
                         "name": "H", "email": "h@anthropic.com"},
                        {"date": "2026-01-03T00:00:00Z", "login": "claude[bot]",
                         "name": "c", "email": "c@x.com"},
                    ],
                }
            }
        )
        r = aggregate("testorg")
        assert r["totals"]["bot_commits"] == 2
        assert [p["login"] for p in r["people"]] == ["human"]

    def test_unattributed_commits_counted_not_dropped_silently(self, harvest):
        harvest(
            {
                "repo": {
                    "total": 2,
                    "commits": [
                        {"date": "2026-01-01T00:00:00Z", "login": None,
                         "name": "Nobody", "email": "n@x.com"},
                        {"date": "2026-01-02T00:00:00Z", "login": "human",
                         "name": "H", "email": "h@anthropic.com"},
                    ],
                }
            }
        )
        assert aggregate("testorg")["totals"]["unattributed_commits"] == 1

    def test_employment_confirmed_only_from_corp_email(self, harvest):
        harvest(
            {
                "repo": {
                    "total": 3,
                    "commits": [
                        {"date": "2026-01-01T00:00:00Z", "login": "staff",
                         "name": "S", "email": "s@anthropic.com"},
                        {"date": "2026-01-01T00:00:00Z", "login": "guess-ant",
                         "name": "G", "email": f"g@{PRIVATE_DOMAIN}"},
                        {"date": "2026-01-01T00:00:00Z", "login": "outsider",
                         "name": "O", "email": "o@gmail.com"},
                    ],
                }
            }
        )
        by = {p["login"]: p["employment"] for p in aggregate("testorg")["people"]}
        assert by == {
            "staff": "confirmed",
            "guess-ant": "handle",
            "outsider": "unknown",
        }

    def test_vendor_domain_flagged_separately(self, harvest):
        harvest(
            {
                "repo": {
                    "total": 1,
                    "commits": [
                        {"date": "2026-01-01T00:00:00Z", "login": "v",
                         "name": "V", "email": "v@stainless.com"},
                    ],
                }
            }
        )
        p = aggregate("testorg")["people"][0]
        assert p["employment"] == "vendor"
        assert p["vendor_domains"] == ["stainless.com"]

    def test_merged_person_keeps_union_of_repos_and_dates(self, harvest):
        harvest(
            {
                "a": {
                    "total": 1,
                    "commits": [
                        {"date": "2026-01-01T00:00:00Z", "login": "me",
                         "name": "Me", "email": "me@anthropic.com"}
                    ],
                },
                "b": {
                    "total": 1,
                    "commits": [
                        {"date": "2026-06-01T00:00:00Z", "login": "me-ant",
                         "name": "Me", "email": "me@anthropic.com"}
                    ],
                },
            }
        )
        people = aggregate("testorg")["people"]
        assert len(people) == 1
        p = people[0]
        assert p["commits"] == 2
        assert p["repo_count"] == 2
        assert p["first_commit"] == "2026-01-01T00:00:00Z"
        assert p["last_commit"] == "2026-06-01T00:00:00Z"
        assert p["merged_from"] == ["me"] or p["merged_from"] == ["me-ant"]

    def test_commit_totals_survive_merging(self, harvest):
        """No commit may be lost or double-counted when accounts merge."""
        harvest(
            {
                "r": {
                    "total": 4,
                    "commits": [
                        {"date": "2026-01-01T00:00:00Z", "login": l,
                         "name": "X", "email": "x@anthropic.com"}
                        for l in ("x", "x-ant", "x", "x-ant")
                    ],
                }
            }
        )
        r = aggregate("testorg")
        assert len(r["people"]) == 1
        assert r["people"][0]["commits"] == 4


class TestMirrorExclusion:
    """Mirrored repos are the failure that most distorted the real run.

    GitHub reports fork=False and mirror_url=None for a push-created mirror, so
    only the description reveals it. One such repo out-committed every genuine
    repo in the org.
    """

    @pytest.fixture
    def harvest(self, tmp_path, monkeypatch):
        import aggregate_github

        monkeypatch.setattr(aggregate_github, "DOCS", tmp_path)

        def write(repos):
            (tmp_path / "github_commits_testorg.json").write_text(json.dumps(repos))

        return write

    def _repo(self, login, n, description=None):
        return {
            "total": n,
            "description": description,
            "commits": [
                {"date": "2026-01-01T00:00:00Z", "login": login,
                 "name": login, "email": f"{login}@elsewhere.org"}
                for _ in range(n)
            ],
        }

    def test_declared_mirror_excluded(self, harvest):
        harvest(
            {
                "real": self._repo("staff", 2, "A first-party project"),
                "mirror": self._repo("outsider", 50,
                                     "Read-only mirror of Upstream/thing"),
            }
        )
        r = aggregate("testorg")
        assert r["mirrors_excluded"] == ["mirror"]
        assert [p["login"] for p in r["people"]] == ["staff"]
        assert r["totals"]["repos"] == 1
        assert r["totals"]["commits"] == 2

    def test_internal_mirror_also_excluded(self, harvest):
        """An in-org mirror duplicates commits already counted elsewhere."""
        harvest(
            {
                "base": self._repo("staff", 3,
                                   "This repo is a mirror of the contents of x."),
                "real": self._repo("staff", 3, "The real one"),
            }
        )
        r = aggregate("testorg")
        assert r["mirrors_excluded"] == ["base"]
        assert r["people"][0]["commits"] == 3

    def test_missing_description_is_not_a_mirror(self, harvest):
        harvest({"r": self._repo("staff", 2, None)})
        assert aggregate("testorg")["mirrors_excluded"] == []

    def test_word_mirror_alone_does_not_exclude(self, harvest):
        """'mirror' appears in ordinary descriptions; only 'mirror of' counts."""
        harvest({"r": self._repo("staff", 2, "A mirror ball rendering demo")})
        assert aggregate("testorg")["mirrors_excluded"] == []


class TestPerLabWorkSuffix:
    """The suffix convention differs per lab.

    An Anthropic-only pattern run against OpenAI matches nothing and yields
    zero merges silently, which looks like clean data rather than a
    misconfiguration.
    """

    def test_openai_suffix_merges_when_supplied(self):
        people = {
            "pakrym": person(3),
            "pakrym-oai": person(9),
        }
        oai = __import__("re").compile(r"[-_](oai|openai)$", __import__("re").I)
        assert alias_pairs(people, oai) == {"pakrym-oai": "pakrym"}

    def test_wrong_lab_suffix_merges_nothing(self):
        people = {"pakrym": person(3), "pakrym-oai": person(9)}
        assert alias_pairs(people) == {}

    def test_every_configured_lab_has_domain_and_suffix(self):
        from aggregate_github import LABS

        for org, (domain, suffix) in LABS.items():
            assert "." in domain, org
            assert __import__("re").compile(suffix), org
