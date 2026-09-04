"""The register's read side: the moves in it, and the gate that keeps noise out.

The silent failures these catch, all of which leave a page that renders fine:

* **The evidence gate stops gating.** On the real register it takes GitHub's 46
  cross-lab logins to zero. A gate that admits them would report drive-by
  open-source contributors as researcher moves — more rows, more confident, and
  wrong. Nothing about the page would look broken.
* **Cross-lab merging creeps back in.** The whole reason a move is visible is
  that the register keeps one record per (lab, person) and refuses to merge. A
  change that merged them would make this list *empty* and look like good news.
* **A candidate loses its sources.** These are claims about named people. One
  without both sides' primary URLs cannot be checked, and an unfalsifiable
  candidate is worse than no candidate.
* **The totals go bare.** A raw head-count overstates what the register knows by
  about four to one; the tier breakdown is what makes it honest.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as m, people
from app.db import create_all


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        s.add_all([
            m.RefLab(id="openai", label="OpenAI", config_version=1),
            m.RefLab(id="anthropic", label="Anthropic", config_version=1),
            m.RefLab(id="deepseek", label="DeepSeek", config_version=1),
        ])
        s.flush()
        yield s


def _person(session, lab, name, kind="paper", first=None, last=None,
            tier="unknown", url=None):
    p = m.Person(lab=lab, source_kind=kind, canonical_name=name,
                 first_seen=first or date(2026, 1, 1), last_seen=last or date(2026, 6, 1))
    session.add(p)
    session.flush()
    session.add(m.PersonEvidence(
        person_id=p.id, source_kind=kind,
        ref_url=url or f"https://arxiv.org/abs/{lab}-{name.replace(' ', '')}",
        observed_on=last or date(2026, 6, 1), tier=tier, payload={}))
    session.add(m.PersonIdentity(
        person_id=p.id, kind=f"{kind}_name", value=name, lab=lab))
    session.flush()
    return p


class TestMoveCandidatesFallOutOfTheRefusalToMerge:
    def test_a_paper_byline_at_two_labs_is_a_candidate(self, session):
        _person(session, "openai", "Jeffrey Wu", last=date(2023, 3, 15))
        _person(session, "anthropic", "Jeffrey Wu", last=date(2026, 8, 21))

        [c] = people.move_candidates(session)

        assert c["name"] == "Jeffrey Wu"
        assert c["readsAs"] == "OpenAI → Anthropic"
        assert c["sourceKind"] == "paper"

    def test_a_name_at_one_lab_only_is_not(self, session):
        _person(session, "openai", "Ada Lovelace")
        _person(session, "openai", "Ada Lovelace", kind="github")

        assert people.move_candidates(session) == []

    def test_candidates_rank_most_recent_first(self, session):
        _person(session, "openai", "Old Name", last=date(2024, 1, 1))
        _person(session, "anthropic", "Old Name", last=date(2024, 6, 1))
        _person(session, "openai", "New Name", last=date(2026, 1, 1))
        _person(session, "deepseek", "New Name", last=date(2026, 8, 1))

        assert [c["name"] for c in people.move_candidates(session)] == ["New Name", "Old Name"]

    def test_the_gap_between_sides_is_reported_not_filtered_on(self, session):
        """A three-year gap is usually a common name on a landmark paper. The
        reader decides; a threshold here would be a guess."""
        _person(session, "openai", "Wide Gap", first=date(2023, 3, 15), last=date(2023, 3, 15))
        _person(session, "anthropic", "Wide Gap", first=date(2026, 8, 21), last=date(2026, 8, 21))

        [c] = people.move_candidates(session)

        assert c["gapDays"] == 1255
        assert len(c["sides"]) == 2

    def test_the_gap_is_between_the_labs_not_across_both_tenures(self, session):
        """The live case this got wrong: Yao Li publishes at DeepSeek from
        2024-01-05 to 2026-04-26, then at OpenAI on 2026-08-18. The gap is 114
        days. Measured first-to-last it reads 956 — rendered "2.6 years apart",
        which is exactly the shape the page tells a reader to discount."""
        _person(session, "deepseek", "Yao Li",
                first=date(2024, 1, 5), last=date(2026, 4, 26))
        _person(session, "openai", "Yao Li",
                first=date(2026, 8, 18), last=date(2026, 8, 18))

        [c] = people.move_candidates(session)

        assert c["gapDays"] == 114

    def test_overlapping_tenures_report_a_negative_gap_rather_than_zero(self, session):
        """Publishing at both labs in the same period is a different and more
        interesting thing than a clean move, so it is not clamped away."""
        _person(session, "openai", "Both At Once",
                first=date(2026, 1, 1), last=date(2026, 8, 1))
        _person(session, "anthropic", "Both At Once",
                first=date(2026, 3, 1), last=date(2026, 9, 1))

        [c] = people.move_candidates(session)

        assert c["gapDays"] < 0


class TestTheEvidenceGate:
    """On the real register this takes GitHub's 46 cross-lab logins to zero."""

    def test_a_github_login_at_two_labs_with_no_employment_evidence_is_dropped(self, session):
        _person(session, "openai", "driveby", kind="github", tier="unknown")
        _person(session, "anthropic", "driveby", kind="github", tier="unknown")

        assert people.move_candidates(session) == []

    def test_it_is_kept_when_both_sides_carry_a_lab_domain(self, session):
        _person(session, "openai", "realstaff", kind="github", tier="confirmed")
        _person(session, "anthropic", "realstaff", kind="github", tier="confirmed")

        [c] = people.move_candidates(session)

        assert c["name"] == "realstaff"
        assert c["assertsEmployment"] is True

    def test_a_third_unverified_lab_disqualifies_the_whole_candidate(self, session):
        """The gate counted employment-tier *records*, not sides. At three labs
        it published as soon as any two were confirmed — with the unverified
        third attached, and `readsAs` able to point straight at it. The register
        really does hold logins at three, four and five labs."""
        _person(session, "openai", "multi", kind="github", tier="confirmed",
                last=date(2026, 1, 1))
        _person(session, "anthropic", "multi", kind="github", tier="confirmed",
                last=date(2026, 2, 1))
        _person(session, "deepseek", "multi", kind="github", tier="unknown",
                last=date(2026, 3, 1))

        assert people.move_candidates(session) == []
        assert people.move_candidate_stats(session)["github"] == {"raw": 1, "kept": 0}

    def test_three_labs_all_verified_is_still_a_candidate(self, session):
        for lab, day in (("openai", 1), ("anthropic", 2), ("deepseek", 3)):
            _person(session, lab, "multi", kind="github", tier="confirmed",
                    last=date(2026, day, 1))

        [c] = people.move_candidates(session)

        assert len(c["sides"]) == 3
        assert c["assertsEmployment"] is True

    def test_employment_evidence_on_one_side_only_is_not_enough(self, session):
        """One lab-domain commit and one drive-by is a contributor, not a move."""
        _person(session, "openai", "halfknown", kind="github", tier="confirmed")
        _person(session, "anthropic", "halfknown", kind="github", tier="unknown")

        assert people.move_candidates(session) == []

    def test_a_paper_byline_needs_no_employment_tier(self, session):
        """A byline is a published claim of authorship; a commit is not."""
        _person(session, "openai", "Byline Only", tier="unknown")
        _person(session, "anthropic", "Byline Only", tier="unknown")

        assert len(people.move_candidates(session)) == 1

    def test_the_gate_reports_what_it_dropped(self, session):
        _person(session, "openai", "Real Move")
        _person(session, "anthropic", "Real Move")
        _person(session, "openai", "driveby", kind="github", tier="unknown")
        _person(session, "anthropic", "driveby", kind="github", tier="unknown")

        stats = people.move_candidate_stats(session)

        assert stats["paper"] == {"raw": 1, "kept": 1}
        assert stats["github"] == {"raw": 1, "kept": 0}


class TestACandidateCanBeChecked:
    def test_both_sides_carry_their_primary_sources(self, session):
        _person(session, "openai", "Checkable", url="https://arxiv.org/abs/1")
        _person(session, "anthropic", "Checkable", url="https://arxiv.org/abs/2")

        [c] = people.move_candidates(session)

        assert [s["sources"] for s in c["sides"]] == [
            ["https://arxiv.org/abs/1"], ["https://arxiv.org/abs/2"]
        ]

    def test_every_side_carries_its_dates_and_tiers(self, session):
        _person(session, "openai", "Dated", first=date(2025, 1, 1), last=date(2025, 6, 1))
        _person(session, "anthropic", "Dated", first=date(2026, 1, 1), last=date(2026, 6, 1))

        [c] = people.move_candidates(session)

        for side in c["sides"]:
            assert side["firstSeen"] and side["lastSeen"] and side["tiers"]


class TestOverviewNeverQuotesABareHeadCount:
    def test_totals_separate_employment_evidence_from_the_raw_count(self, session):
        _person(session, "openai", "Staff", kind="github", tier="confirmed")
        _person(session, "openai", "Drive By", kind="github", tier="unknown")
        _person(session, "openai", "Parent Co", kind="github", tier="confirmed_org_wide")

        totals = people.overview(session)["totals"]

        assert totals["people"] == 3
        assert totals["employment_evidenced"] == 2

    def test_every_tier_is_labelled_with_whether_it_asserts_employment(self, session):
        _person(session, "openai", "A", tier="confirmed")
        _person(session, "openai", "B", tier="unknown")

        tiers = {t["tier"]: t["asserts_employment"] for t in people.overview(session)["tiers"]}

        assert tiers == {"confirmed": True, "unknown": False}

    def test_labs_break_down_by_leg(self, session):
        _person(session, "openai", "P")
        _person(session, "openai", "G", kind="github", tier="confirmed")

        [row] = people.overview(session)["labs"]

        assert (row["lab"], row["paper"], row["github"], row["total"]) == ("openai", 1, 1, 2)
        assert row["employment_evidenced"] == 1


class TestSearchGoesThroughIdentitiesNotJustNames:
    def test_a_github_login_finds_the_person(self, session):
        p = _person(session, "openai", "Ada Lovelace", kind="github", tier="confirmed")
        session.add(m.PersonIdentity(person_id=p.id, kind="github_login",
                                     value="ada-l", lab="openai"))
        session.flush()

        [hit] = people.search(session, "ada-l")

        assert hit["name"] == "Ada Lovelace"

    def test_search_is_case_insensitive_and_partial(self, session):
        _person(session, "openai", "Yonglong Tian")

        assert len(people.search(session, "YONGLONG")) == 1
        assert len(people.search(session, "tian")) == 1

    def test_an_empty_query_returns_nothing_rather_than_everything(self, session):
        _person(session, "openai", "Someone")

        assert people.search(session, "   ") == []

    def test_a_name_at_two_labs_returns_both_records(self, session):
        """Search must not merge either — it shows the same anomaly the
        candidate list is built from."""
        _person(session, "openai", "Jeffrey Wu")
        _person(session, "anthropic", "Jeffrey Wu")

        assert {r["lab"] for r in people.search(session, "jeffrey")} == {"openai", "anthropic"}
