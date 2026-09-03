"""Papers and GitHub become a register of people, not a second insight stream.

Both legs were harvested months ago and nothing ever read them. The question
they answer is not "what happened" — announcements answer that — but **"who
works at this lab, and since when"**, which is the thing a departure or a
stealth-startup formation is measured against. That is why they land here as
people and evidence rather than getting their own scoring and holdings join.

Three rules govern every merge in this module, and all three are refusals:

1. **No cross-lab merge.** A person is scoped to a lab. Two researchers sharing
   a name at two labs are two people until something says otherwise; merging
   them is an employment claim, and this register is not entitled to make it
   from a matching string.
2. **No cross-leg merge.** A GitHub login and a paper byline are different kinds
   of identifier. Guessing that `Omar-V2` is the `Omar Khan` on a paper is
   exactly the entity-resolution collision this project names as a failure mode.
3. **No LLM-asserted employment.** A byline affiliation read by a model is
   recorded at tier ``model_asserted`` and never as ``confirmed``, per the hard
   rule in planning.md §11 — on Anthropic the extractor asserted fellowship
   status for four people whose pages state no affiliation at all.

What *is* merged: human-confirmed aliases from `config/aliases.yaml` within the
papers leg, and the evidence-based alias merges `aggregate_github` has already
computed within an org.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as m
from app.models import utcnow

CONFIG = Path(__file__).parent.parent.parent / "config"

PAPER, GITHUB = "paper", "github"
PAPER_NAME, GITHUB_LOGIN = "paper_name", "github_login"

# GitHub's own tiers pass through; the papers leg gets its own, kept separate on
# purpose so a model's reading of a byline is never counted as confirmation.
MODEL_ASSERTED = "model_asserted"
UNKNOWN = "unknown"


def _person_count(session: Session) -> int:
    """How many people exist. Used to report *new* people per load.

    The counts were previously whole-table totals advertised as per-load
    figures, so `run.stats["register"]` claimed a firing had found 4,927 people
    every night — and the github figure silently included the papers people
    loaded moments earlier.
    """
    return int(session.scalar(select(func.count()).select_from(m.Person)) or 0)


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def load_aliases(config_dir: Path = CONFIG) -> dict[str, str]:
    """Human-confirmed name merges, as ``{variant: canonical}``.

    Only `confirmed: true` entries are honoured. `config/aliases.yaml` is
    human-owned and never written by code — a generator overwrote it once and
    destroyed ten confirmed merges, because once an alias takes effect the
    duplicate spelling stops appearing and regeneration correctly proposes
    nothing, then writes that nothing over the file.
    """
    data = yaml.safe_load((config_dir / "aliases.yaml").read_text(encoding="utf-8"))
    return {
        entry["variant"]: entry["canonical"]
        for entry in (data.get("aliases") or [])
        if entry.get("confirmed")
    }


def _absorb(session: Session, loser: m.Person, winner: m.Person) -> None:
    """Move a person's identities and evidence onto another, then delete them.

    The operator's only entity-resolution control is: notice a duplicate,
    confirm it in `config/aliases.yaml`, re-run. That did nothing once both
    spellings had been loaded — `_person` resolved on the identity, found the
    variant's own row, and never consulted the alias. The merge has to be
    retroactive or the control is decorative.
    """
    if loser.id == winner.id:
        return
    for identity in session.scalars(
        select(m.PersonIdentity).where(m.PersonIdentity.person_id == loser.id)
    ):
        identity.person_id = winner.id
    for evidence in session.scalars(
        select(m.PersonEvidence).where(m.PersonEvidence.person_id == loser.id)
    ):
        clash = session.scalar(
            select(m.PersonEvidence).where(
                m.PersonEvidence.person_id == winner.id,
                m.PersonEvidence.source_kind == evidence.source_kind,
                m.PersonEvidence.ref_url == evidence.ref_url,
            )
        )
        if clash is None:
            evidence.person_id = winner.id
        else:
            session.delete(evidence)
    for field in ("first_seen", "last_seen"):
        mine, theirs = getattr(winner, field), getattr(loser, field)
        if theirs and (mine is None or (field == "first_seen") == (theirs < mine)):
            setattr(winner, field, theirs)
    session.flush()
    session.delete(loser)
    session.flush()


def _person(
    session: Session, lab: str, source_kind: str, name: str, identity: tuple[str, str]
) -> m.Person:
    """Resolve a person by identifier, creating them on first sight.

    Resolution goes through :class:`~app.models.PersonIdentity`, not through the
    name. Looking up by name alone merged a GitHub login and a paper byline that
    happened to be the same string — the cross-leg guess this module refuses to
    make. `source_kind` in the person key makes that collision impossible even
    when two legs produce an identical canonical name at the same lab.

    Args:
        session: Open session.
        lab: Lab the person belongs to.
        source_kind: Which leg observed them (`paper` | `github`).
        name: Canonical display name.
        identity: The (kind, value) pair that identifies them.

    Returns:
        The person row.
    """
    kind, value = identity
    existing = session.scalar(
        select(m.PersonIdentity).where(
            m.PersonIdentity.kind == kind,
            m.PersonIdentity.value == value,
            m.PersonIdentity.lab == lab,
        )
    )
    if existing is not None:
        return session.get(m.Person, existing.person_id)

    person = session.scalar(
        select(m.Person).where(
            m.Person.lab == lab,
            m.Person.source_kind == source_kind,
            m.Person.canonical_name == name,
        )
    )
    if person is None:
        person = m.Person(lab=lab, source_kind=source_kind, canonical_name=name)
        session.add(person)
        session.flush()
    return person


def _identity(session: Session, person: m.Person, kind: str, value: str) -> bool:
    """Attach an identifier to a person, idempotently.

    Returns:
        True if this identifier was new.
    """
    existing = session.scalar(
        select(m.PersonIdentity).where(
            m.PersonIdentity.kind == kind,
            m.PersonIdentity.value == value,
            m.PersonIdentity.lab == person.lab,
        )
    )
    if existing is not None:
        return False
    session.add(
        m.PersonIdentity(person_id=person.id, kind=kind, value=value, lab=person.lab)
    )
    session.flush()
    return True


def _evidence(
    session: Session, person: m.Person, source_kind: str, ref_url: str,
    tier: str, observed_on: date | None, payload: dict,
) -> None:
    """Record what proves this person, idempotently on (person, kind, url)."""
    existing = session.scalar(
        select(m.PersonEvidence).where(
            m.PersonEvidence.person_id == person.id,
            m.PersonEvidence.source_kind == source_kind,
            m.PersonEvidence.ref_url == ref_url,
        )
    )
    if existing is None:
        session.add(
            m.PersonEvidence(
                person_id=person.id, source_kind=source_kind, ref_url=ref_url,
                tier=tier, observed_on=observed_on, payload=payload,
            )
        )
    else:
        existing.tier, existing.payload = tier, payload

    if observed_on:
        if person.first_seen is None or observed_on < person.first_seen:
            person.first_seen = observed_on
        if person.last_seen is None or observed_on > person.last_seen:
            person.last_seen = observed_on


def _as_date(value) -> date | None:
    """Parse an ISO date, tolerating the several shapes the legs produce."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_papers(
    session: Session, items: list[dict], run_id: int | None = None,
    config_dir: Path = CONFIG,
) -> dict:
    """Load harvested papers and the people on their bylines.

    Args:
        session: Open session; the caller owns the commit.
        items: Normalised paper records from the papers adapter.
        run_id: Run to attribute the load to.
        config_dir: Where aliases.yaml lives.

    Returns:
        ``{papers, people, identities, evidence, skipped_no_url, merged}`` —
        `people` counts people *new to this load*, not the table total.
    """
    aliases = load_aliases(config_dir)
    before = _person_count(session)
    stats = {"papers": 0, "people": 0, "identities": 0, "evidence": 0,
             "skipped_no_url": 0, "merged": 0}

    for item in items:
        url, lab = item.get("url"), item.get("lab")
        if not url:
            # No citation, no insight — and no person either.
            stats["skipped_no_url"] += 1
            continue

        raw = session.scalar(select(m.RawPaper).where(m.RawPaper.url == url))
        payload = item.get("raw", item)
        if raw is None:
            session.add(m.RawPaper(
                url=url, lab=lab, payload=payload, content_hash=_hash(payload),
                load_run_id=run_id,
            ))
            stats["papers"] += 1
        else:
            raw.payload, raw.content_hash, raw.last_seen_at = payload, _hash(payload), utcnow()

        published = _as_date(item.get("date"))
        for author in item.get("authors") or []:
            name = (author.get("name") or "").strip()
            if not name:
                continue
            canonical = aliases.get(name, name)
            person = _person(session, lab, PAPER, canonical, (PAPER_NAME, name))
            if canonical != name and person.canonical_name != canonical:
                # Both spellings were loaded before the alias was confirmed.
                # Fold the variant's row into the canonical one, retroactively.
                winner = _person(session, lab, PAPER, canonical,
                                 (PAPER_NAME, canonical))
                _absorb(session, person, winner)
                person = winner
                stats["merged"] = stats.get("merged", 0) + 1
            stats["identities"] += _identity(session, person, PAPER_NAME, name)
            # The canonical spelling is an identity too, so a later lookup by it
            # resolves without re-reading the alias file.
            if canonical != name:
                stats["identities"] += _identity(session, person, PAPER_NAME, canonical)

            _evidence(
                session, person, PAPER, url,
                # An LLM read this affiliation. It is a lead, never confirmation.
                MODEL_ASSERTED if author.get("is_lab_staff") else UNKNOWN,
                published,
                {
                    "affiliations": author.get("affiliations") or [],
                    "is_lab_staff": bool(author.get("is_lab_staff")),
                    "title": item.get("title"),
                },
            )
            stats["evidence"] += 1
    session.flush()
    stats["people"] = _person_count(session) - before
    return stats


def load_github_repos(
    session: Session, items: list[dict], run_id: int | None = None
) -> int:
    """Upsert freshly harvested repository histories into bronze.

    Keyed on (org, repo) and upsert-only, like every other raw table: a
    repository the adapter did not fetch this run — because its `pushed_at` had
    not moved — keeps the row it already had rather than being deleted as
    missing.

    Args:
        session: Open session; the caller owns the commit.
        items: ``{org, repo, pushed_at, payload}`` records from the adapter.
        run_id: Run to attribute the load to.

    Returns:
        How many repository rows were written or refreshed.
    """
    for item in items:
        org, repo = item.get("org"), item.get("repo")
        if not org or not repo:
            continue
        payload = item.get("payload") or {}
        row = session.scalar(
            select(m.RawGithubRepo).where(
                m.RawGithubRepo.org == org, m.RawGithubRepo.repo == repo
            )
        )
        if row is None:
            session.add(m.RawGithubRepo(
                org=org, repo=repo, pushed_at=item.get("pushed_at") or "",
                payload=payload, content_hash=_hash(payload), load_run_id=run_id,
            ))
        else:
            row.payload, row.content_hash = payload, _hash(payload)
            row.pushed_at = item.get("pushed_at") or row.pushed_at
            row.last_seen_at = utcnow()

    session.flush()
    return len(items)


def load_github(
    session: Session, items: list[dict], run_id: int | None = None
) -> dict:
    """Load an org's people aggregate into the register.

    `aggregate_github` has already merged aliases within the org on commit-email
    evidence, so `merged_from` is carried as additional identities on the same
    person rather than re-derived here.

    Returns:
        ``{people_rows, people, identities, evidence}``.
    """
    before = _person_count(session)
    stats = {"people_rows": 0, "people": 0, "identities": 0, "evidence": 0}

    for item in items:
        login, org, lab = item.get("login"), item.get("org"), item.get("lab")
        if not login or not lab:
            continue

        raw = session.scalar(
            select(m.RawGithubPerson).where(
                m.RawGithubPerson.org == org, m.RawGithubPerson.login == login
            )
        )
        if raw is None:
            session.add(m.RawGithubPerson(
                org=org, login=login, lab=lab, payload=item,
                content_hash=_hash(item), load_run_id=run_id,
            ))
            stats["people_rows"] += 1
        else:
            raw.payload, raw.content_hash, raw.last_seen_at = item, _hash(item), utcnow()

        # The login is the canonical name: it is the identifier GitHub
        # guarantees, where the commit `name` field is free text a person can
        # set to anything and frequently sets to nothing useful.
        person = _person(session, lab, GITHUB, login, (GITHUB_LOGIN, login))
        stats["identities"] += _identity(session, person, GITHUB_LOGIN, login)
        for merged in item.get("merged_from") or []:
            stats["identities"] += _identity(session, person, GITHUB_LOGIN, merged)

        _evidence(
            session, person, GITHUB, f"https://github.com/{login}",
            item.get("employment") or UNKNOWN,
            _as_date(item.get("last_commit")),
            {
                "org": org,
                "commits": item.get("commits"),
                "corp_commits": item.get("corp_commits"),
                "domains": item.get("domains") or {},
                "names": item.get("names") or [],
                "repo_count": item.get("repo_count"),
                "first_commit": item.get("first_commit"),
            },
        )
        stats["evidence"] += 1

        first = _as_date(item.get("first_commit"))
        if first and (person.first_seen is None or first < person.first_seen):
            person.first_seen = first

    session.flush()
    stats["people"] = _person_count(session) - before
    return stats


def load_unresolved(
    session: Session, leg: str, source_id: str, items: list[dict],
    run_id: int | None = None,
) -> int:
    """Record what a source could not resolve, idempotently.

    Args:
        session: Open session.
        leg: Which leg produced them.
        source_id: Which source.
        items: Each needs a `reason`; the identifier is whatever the source had.
        run_id: Run to attribute them to.

    Returns:
        How many rows are now recorded for this source in this call.
    """
    seen = 0
    for item in items:
        identifier = str(
            item.get("title") or item.get("url") or item.get("name") or item
        )[:500]
        existing = session.scalar(
            select(m.UnresolvedItem).where(
                m.UnresolvedItem.leg == leg,
                m.UnresolvedItem.source_id == source_id,
                m.UnresolvedItem.identifier == identifier,
            )
        )
        if existing is None:
            session.add(m.UnresolvedItem(
                leg=leg, source_id=source_id, kind=item.get("kind", "paper"),
                identifier=identifier, reason=str(item.get("reason", "unspecified")),
                run_id=run_id,
            ))
        else:
            existing.last_seen_at = utcnow()
            existing.reason = str(item.get("reason", existing.reason))
        seen += 1
    session.flush()
    return seen


def load_report(session: Session, report, run_id: int | None = None) -> dict:
    """Load a whole ingestion report's papers and GitHub output.

    Args:
        session: Open session.
        report: A :class:`~app.pipeline.orchestrator.RunReport`.
        run_id: Run to attribute the load to.

    Returns:
        Per-leg stats plus the unresolved count.
    """
    from app.pipeline.orchestrator import SUCCEEDED

    papers = report.items_for("papers")
    github = report.items_for("github")
    stats = {
        "papers": load_papers(session, papers, run_id),
        # Bronze before the aggregate that was built from it: a run that dies
        # between the two should leave the commits it paid to fetch behind.
        "github_repos": load_github_repos(session, report.raw_repos(), run_id),
        "github": load_github(session, github, run_id),
        "unresolved": 0,
    }
    for outcome in report.outcomes:
        if outcome.status == SUCCEEDED and outcome.result and outcome.result.unresolved:
            stats["unresolved"] += load_unresolved(
                session, outcome.source.leg, outcome.source.id,
                outcome.result.unresolved, run_id,
            )
    return stats
