"""The people register, and the merges it refuses to make.

Entity resolution is a named failure mode for this project, and the dangerous
direction is over-merging: two people collapsed into one produces a register
that looks cleaner and is wrong, with nothing to notice. Every test here that
asserts two rows rather than one is guarding that direction.

The silent failures these catch:

* **Cross-lab collision.** Two researchers sharing a name at different labs
  merged into one person, silently asserting that someone moved employer.
* **Cross-leg guessing.** A GitHub login matched to a paper byline on a name,
  which is the collision the brief names outright.
* **An LLM-asserted affiliation laundered into confirmation.** planning.md §11's
  hard rule; on Anthropic the extractor invented fellowship status for four
  people whose pages state no affiliation at all.
* **A person with no resolvable source.** "No citation, no insight" applied to
  people — a byline with no paper URL behind it is not evidence of anything.
* **Silent gaps.** Unresolved candidates dropped rather than recorded, which
  makes a thin register read as a complete one.
"""

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import models as m
from app.db import create_all
from app.pipeline import register


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


def paper(lab="google-deepmind", url="https://deepmind.google/p/1", authors=None, date="2026-08-26"):
    return {
        "lab": lab, "url": url, "title": "A paper", "date": date,
        "authors": authors if authors is not None else [
            {"name": "Ada Lovelace", "affiliations": ["Google DeepMind"], "is_lab_staff": True}
        ],
        "raw": {"url": url, "source_url": "https://arxiv.org/abs/1", "resolution": "exact_title"},
    }


def gh(login="ada", lab="google-deepmind", org="google-deepmind", **extra):
    return {
        "login": login, "lab": lab, "org": org, "commits": 42, "corp_commits": 42,
        "employment": "confirmed", "domains": {"deepmind.com": 42}, "names": ["Ada"],
        "merged_from": [], "repo_count": 2,
        "first_commit": "2026-01-05T00:00:00Z", "last_commit": "2026-08-20T00:00:00Z",
        **extra,
    }


class TestTheMergesItRefuses:
    def test_the_same_name_at_two_labs_is_two_people(self, session):
        """The collision the brief names. Merging asserts an employer change."""
        register.load_papers(session, [
            paper(lab="google-deepmind", url="https://deepmind.google/p/1"),
            paper(lab="deepseek", url="https://arxiv.org/abs/2"),
        ])
        session.commit()

        people = session.scalars(select(m.Person)).all()
        assert len(people) == 2
        assert {p.lab for p in people} == {"google-deepmind", "deepseek"}
        assert {p.canonical_name for p in people} == {"Ada Lovelace"}

    def test_a_github_login_is_not_matched_to_a_paper_byline(self, session):
        """Different identifier kinds. `Omar-V2` is not provably `Omar Khan`."""
        register.load_papers(session, [paper(authors=[
            {"name": "Ada", "affiliations": [], "is_lab_staff": True}
        ])])
        register.load_github(session, [gh(login="Ada")])
        session.commit()

        people = session.scalars(select(m.Person)).all()
        assert len(people) == 2
        kinds = {i.kind for i in session.scalars(select(m.PersonIdentity)).all()}
        assert kinds == {register.PAPER_NAME, register.GITHUB_LOGIN}

    def test_the_same_login_across_one_labs_two_orgs_is_one_person(self, session):
        """Meta owns facebookresearch and meta-llama. Same lab, same person."""
        register.load_github(session, [
            gh(login="zuck", lab="meta-ai", org="facebookresearch"),
            gh(login="zuck", lab="meta-ai", org="meta-llama"),
        ])
        session.commit()

        assert session.scalar(select(func.count()).select_from(m.Person)) == 1
        # Both orgs are kept as raw rows: contribution differs per org.
        assert session.scalar(select(func.count()).select_from(m.RawGithubPerson)) == 2

    def test_the_same_login_at_two_labs_is_not_merged(self, session):
        """Real on GitHub, but a cross-lab employment claim commit data cannot make."""
        register.load_github(session, [
            gh(login="drifter", lab="google-deepmind", org="google-deepmind"),
            gh(login="drifter", lab="meta-ai", org="meta-llama"),
        ])
        session.commit()
        assert session.scalar(select(func.count()).select_from(m.Person)) == 2


class TestAliasesMergeOnlyWhenHumanConfirmed:
    def test_a_confirmed_alias_merges(self, session, tmp_path):
        (tmp_path / "aliases.yaml").write_text(
            "version: 1\naliases:\n"
            "  - canonical: Christopher Olah\n    variant: Chris Olah\n"
            "    confirmed: true\n    verdict: review\n"
        )
        register.load_papers(session, [
            paper(url="https://x/1", authors=[{"name": "Christopher Olah", "is_lab_staff": True}]),
            paper(url="https://x/2", authors=[{"name": "Chris Olah", "is_lab_staff": True}]),
        ], config_dir=tmp_path)
        session.commit()

        people = session.scalars(select(m.Person)).all()
        assert len(people) == 1
        assert people[0].canonical_name == "Christopher Olah"
        values = {i.value for i in session.scalars(select(m.PersonIdentity)).all()}
        assert values == {"Christopher Olah", "Chris Olah"}

    def test_an_unconfirmed_alias_does_not_merge(self, session, tmp_path):
        """A proposal is a question, not an answer."""
        (tmp_path / "aliases.yaml").write_text(
            "version: 1\naliases:\n"
            "  - canonical: Christopher Olah\n    variant: Chris Olah\n"
            "    confirmed: false\n    verdict: review\n"
        )
        register.load_papers(session, [
            paper(url="https://x/1", authors=[{"name": "Christopher Olah", "is_lab_staff": True}]),
            paper(url="https://x/2", authors=[{"name": "Chris Olah", "is_lab_staff": True}]),
        ], config_dir=tmp_path)
        session.commit()
        assert session.scalar(select(func.count()).select_from(m.Person)) == 2

    def test_the_real_alias_file_is_all_confirmed_and_loadable(self):
        aliases = register.load_aliases()
        assert aliases["Chris Olah"] == "Christopher Olah"
        assert aliases["Clément Dumas"] == "Clement Dumas"

    def test_github_alias_merges_from_the_aggregator_are_carried(self, session):
        """`aggregate_github` already merged these on commit-email evidence."""
        register.load_github(session, [gh(login="ada-dm", merged_from=["ada", "alovelace"])])
        session.commit()

        person = session.scalars(select(m.Person)).one()
        values = {i.value for i in session.scalars(select(m.PersonIdentity)).all()}
        assert values == {"ada-dm", "ada", "alovelace"}
        assert person.canonical_name == "ada-dm"


class TestEvidence:
    def test_every_person_has_a_resolvable_source(self, session):
        register.load_papers(session, [paper()])
        register.load_github(session, [gh(login="someone")])
        session.commit()

        people = session.scalars(select(m.Person)).all()
        assert people
        for person in people:
            evidence = session.scalars(
                select(m.PersonEvidence).where(m.PersonEvidence.person_id == person.id)
            ).all()
            assert evidence, f"{person.canonical_name} has no evidence"
            assert all(e.ref_url.startswith("http") for e in evidence)

    def test_a_paper_with_no_url_yields_no_people(self, session):
        """No citation, no insight — and no person either."""
        stats = register.load_papers(session, [paper(url=None)])
        session.commit()
        assert stats["skipped_no_url"] == 1
        assert session.scalar(select(func.count()).select_from(m.Person)) == 0

    def test_the_labs_own_page_is_the_citation(self, session):
        """Not the arXiv page the byline was scraped from."""
        register.load_papers(session, [paper(url="https://ai.meta.com/research/p")])
        session.commit()
        evidence = session.scalars(select(m.PersonEvidence)).one()
        assert evidence.ref_url == "https://ai.meta.com/research/p"

    def test_a_model_read_affiliation_is_never_confirmation(self, session):
        """planning.md §11's hard rule, made structural.

        The extractor asserted fellowship status for four Anthropic people whose
        pages state no affiliation at all. `is_lab_staff: true` from an LLM is a
        lead; it must not share a tier with a commit from a lab-owned domain.
        """
        register.load_papers(session, [paper(authors=[
            {"name": "Ada Lovelace", "affiliations": ["Google DeepMind"], "is_lab_staff": True}
        ])])
        session.commit()

        evidence = session.scalars(select(m.PersonEvidence)).one()
        assert evidence.tier == register.MODEL_ASSERTED
        assert evidence.tier != "confirmed"

    def test_github_employment_tiers_pass_through_unflattened(self, session):
        """`confirmed` and `confirmed_org_wide` must never be conflated."""
        register.load_github(session, [
            gh(login="a", employment="confirmed"),
            gh(login="b", employment="confirmed_org_wide"),
            gh(login="c", employment="unknown"),
        ])
        session.commit()

        tiers = {
            session.get(m.Person, e.person_id).canonical_name: e.tier
            for e in session.scalars(select(m.PersonEvidence)).all()
        }
        assert tiers == {"a": "confirmed", "b": "confirmed_org_wide", "c": "unknown"}

    def test_first_and_last_seen_span_the_evidence(self, session):
        register.load_papers(session, [
            paper(url="https://x/1", date="2026-03-01"),
            paper(url="https://x/2", date="2026-08-26"),
        ])
        session.commit()
        person = session.scalars(select(m.Person)).one()
        assert str(person.first_seen) == "2026-03-01"
        assert str(person.last_seen) == "2026-08-26"


class TestUnresolvedItemsAreRecorded:
    def test_an_unresolved_candidate_is_kept_with_its_reason(self, session):
        register.load_unresolved(session, "papers", "mistral", [
            {"title": "Some announcement", "reason": "no arxiv match"},
        ])
        session.commit()

        row = session.scalars(select(m.UnresolvedItem)).one()
        assert row.identifier == "Some announcement"
        assert row.reason == "no arxiv match"
        assert row.source_id == "mistral"

    def test_re_running_does_not_duplicate(self, session):
        items = [{"title": "Some announcement", "reason": "no arxiv match"}]
        register.load_unresolved(session, "papers", "mistral", items)
        register.load_unresolved(session, "papers", "mistral", items)
        session.commit()
        assert session.scalar(select(func.count()).select_from(m.UnresolvedItem)) == 1


class TestIdempotence:
    def test_a_second_load_changes_nothing(self, session):
        """The property that makes a scheduled re-run free and safe."""
        items_p = [paper(url="https://x/1"), paper(url="https://x/2")]
        items_g = [gh(login="a"), gh(login="b", merged_from=["b-old"])]

        register.load_papers(session, items_p)
        register.load_github(session, items_g)
        session.commit()
        before = {
            t.__tablename__: session.scalar(select(func.count()).select_from(t))
            for t in (m.Person, m.PersonIdentity, m.PersonEvidence,
                      m.RawPaper, m.RawGithubPerson)
        }

        register.load_papers(session, items_p)
        register.load_github(session, items_g)
        session.commit()
        after = {
            t.__tablename__: session.scalar(select(func.count()).select_from(t))
            for t in (m.Person, m.PersonIdentity, m.PersonEvidence,
                      m.RawPaper, m.RawGithubPerson)
        }

        assert before == after
        assert before["people"] == 3  # one paper author across both papers, two logins


class TestAliasesMergeRetroactively:
    """A confirmed alias must work on a corpus that is already loaded.

    The operator's only entity-resolution control is: notice a duplicate,
    confirm it in `config/aliases.yaml`, re-run. Once both spellings had their
    own identity rows, `_person` resolved on the identity and never consulted
    the alias — so the control did nothing, silently, in exactly the situation
    it exists for. Reproduced by review before the fix.
    """

    def _aliases(self, tmp_path, confirmed=True):
        (tmp_path / "aliases.yaml").write_text(
            "version: 1\naliases:\n"
            "  - canonical: John Smith\n    variant: J. Smith\n"
            f"    confirmed: {str(confirmed).lower()}\n    verdict: review\n"
        )
        return tmp_path

    def test_an_alias_confirmed_after_the_fact_still_merges(self, session, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "aliases.yaml").write_text("version: 1\naliases: []\n")
        papers = [
            paper(url="https://x/1", authors=[{"name": "John Smith", "is_lab_staff": True}]),
            paper(url="https://x/2", authors=[{"name": "J. Smith", "is_lab_staff": True}]),
        ]

        register.load_papers(session, papers, config_dir=empty)
        session.commit()
        assert session.scalar(select(func.count()).select_from(m.Person)) == 2

        # The operator notices, confirms the alias, and re-runs.
        register.load_papers(session, papers, config_dir=self._aliases(tmp_path))
        session.commit()

        person = session.scalars(select(m.Person)).one()
        assert person.canonical_name == "John Smith"
        assert {i.value for i in session.scalars(select(m.PersonIdentity)).all()} == {
            "John Smith", "J. Smith"
        }

    def test_the_merged_persons_evidence_is_carried_over_not_lost(self, session, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "aliases.yaml").write_text("version: 1\naliases: []\n")
        papers = [
            paper(url="https://x/1", date="2026-03-01",
                  authors=[{"name": "John Smith", "is_lab_staff": True}]),
            paper(url="https://x/2", date="2026-08-26",
                  authors=[{"name": "J. Smith", "is_lab_staff": True}]),
        ]
        register.load_papers(session, papers, config_dir=empty)
        session.commit()

        register.load_papers(session, papers, config_dir=self._aliases(tmp_path))
        session.commit()

        person = session.scalars(select(m.Person)).one()
        urls = {e.ref_url for e in session.scalars(
            select(m.PersonEvidence).where(m.PersonEvidence.person_id == person.id))}
        assert urls == {"https://x/1", "https://x/2"}
        # The merged span covers both papers.
        assert str(person.first_seen) == "2026-03-01"
        assert str(person.last_seen) == "2026-08-26"
        # No orphaned rows left pointing at the deleted person.
        assert session.scalar(select(func.count()).select_from(m.PersonEvidence)) == 2

    def test_an_unconfirmed_alias_still_does_not_merge_retroactively(self, session, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "aliases.yaml").write_text("version: 1\naliases: []\n")
        papers = [
            paper(url="https://x/1", authors=[{"name": "John Smith", "is_lab_staff": True}]),
            paper(url="https://x/2", authors=[{"name": "J. Smith", "is_lab_staff": True}]),
        ]
        register.load_papers(session, papers, config_dir=empty)
        session.commit()

        register.load_papers(session, papers,
                             config_dir=self._aliases(tmp_path, confirmed=False))
        session.commit()
        assert session.scalar(select(func.count()).select_from(m.Person)) == 2

    def test_people_counts_are_per_load_not_whole_table(self, session):
        first = register.load_papers(session, [paper(url="https://x/1")])
        session.commit()
        second = register.load_papers(session, [paper(url="https://x/2")])
        session.commit()

        assert first["people"] == 1
        assert second["people"] == 0  # same author, already known
