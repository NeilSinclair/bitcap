"""Read side of the people register: browse it, and find the moves in it.

The register itself is built by `app/pipeline/register.py`, whose design is a
set of refusals — chiefly that a name at two labs is two people until something
says otherwise (D25). This module is what that refusal buys.

**Researcher moves fall out of the refusal, not out of a merge.** The brief
names "a key researcher quietly moving to a competitor" as top-tier signal. Had
the register glued matching names together, each move would have become one
tidy row and the signal would have been destroyed by the cleanup. Because it
does not, a person appearing under two labs is a *visible anomaly*, and finding
them is a group-by rather than a model.

**Candidates, never conclusions.** Same name is not same person. Every row here
carries both sides' evidence — the papers, the dates, the tiers — so a human
can settle it in a minute, and nothing downstream consumes it as fact.

**The evidence tier is the noise filter, and it does real work.** On the papers
leg a byline is a published claim of authorship, so a name at two labs is worth
looking at on its own. On the GitHub leg it is not: 5,076 of 6,429 evidence rows
are `unknown` — an account that touched a lab's repository with no evidence of
employment. Requiring a lab-owned email domain (`confirmed`, or
`confirmed_org_wide` for a parent company) on *both* sides takes GitHub's 46
cross-lab logins to **zero**. They are drive-by open-source contributors, and a
register that reported them as moves would be reporting repository popularity.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models as m

# Tiers that assert employment rather than mere contact. `confirmed` is a commit
# from a lab-owned email domain; `confirmed_org_wide` a parent company's domain
# (@google.com covers all of Alphabet), which is weaker but still an employment
# claim. Everything else — `unknown`, `handle`, `vendor`, `model_asserted` —
# says only that the account touched the repository, or that a model said so.
EMPLOYMENT_TIERS = frozenset({"confirmed", "confirmed_org_wide"})

PAPER, GITHUB = "paper", "github"


def _tiers_by_person(session: Session) -> dict[int, set[str]]:
    """person_id -> the set of evidence tiers recorded for them."""
    out: dict[int, set[str]] = defaultdict(set)
    for person_id, tier in session.execute(
        select(m.PersonEvidence.person_id, m.PersonEvidence.tier)
    ):
        out[person_id].add(tier)
    return out


def overview(session: Session) -> dict:
    """Register totals, per lab and per evidence tier.

    The tier breakdown is not decoration. A raw contributor count is nearly
    meaningless as a measure of a lab — most of it is drive-by open-source
    traffic — so any figure the page shows has to carry its tier or it is just
    repository popularity.

    Args:
        session: Open session.

    Returns:
        ``{"totals", "labs", "tiers"}``. `labs` is one row per lab with its
        paper, GitHub and employment-evidenced counts.
    """
    tiers = _tiers_by_person(session)
    labs: dict[str, dict] = defaultdict(
        lambda: {"paper": 0, "github": 0, "employment_evidenced": 0}
    )
    for person in session.scalars(select(m.Person)):
        row = labs[person.lab]
        row[person.source_kind] = row.get(person.source_kind, 0) + 1
        if tiers[person.id] & EMPLOYMENT_TIERS:
            row["employment_evidenced"] += 1

    lab_labels = {r.id: r.label for r in session.scalars(select(m.RefLab))}
    tier_counts = dict(
        session.execute(
            select(m.PersonEvidence.tier, func.count()).group_by(m.PersonEvidence.tier)
        ).all()
    )

    return {
        "totals": {
            "people": session.scalar(select(func.count()).select_from(m.Person)),
            "identities": session.scalar(select(func.count()).select_from(m.PersonIdentity)),
            "evidence": session.scalar(select(func.count()).select_from(m.PersonEvidence)),
            "employment_evidenced": sum(
                1 for pid, t in tiers.items() if t & EMPLOYMENT_TIERS
            ),
        },
        "labs": sorted(
            (
                {"lab": lab, "label": lab_labels.get(lab, lab), **counts,
                 "total": counts["paper"] + counts["github"]}
                for lab, counts in labs.items()
            ),
            key=lambda r: r["total"],
            reverse=True,
        ),
        "tiers": [
            {"tier": tier, "count": count, "asserts_employment": tier in EMPLOYMENT_TIERS}
            for tier, count in sorted(tier_counts.items(), key=lambda kv: -kv[1])
        ],
    }


def move_candidates(session: Session) -> list[dict]:
    """People recorded under more than one lab — possible researcher moves.

    A group-by, not a model: the register keeps per-lab records, so a move shows
    up as one name with two of them. Deterministic on purpose — "which names
    appear twice" is a set operation, and an LLM asked the same question would
    be slower, dearer, and able to invent a person.

    Papers and GitHub are gated differently, and the difference is the whole
    filter. A byline is a published claim of authorship, so a paper-leg name at
    two labs stands on its own. A GitHub login is not a claim of anything, so it
    is only reported when *both* labs carry employment-tier evidence — which, on
    the current register, none do.

    Args:
        session: Open session.

    Returns:
        One dict per candidate, most recent last-seen first. Each carries every
        lab-side record with its dates, tiers and source URLs.
    """
    tiers = _tiers_by_person(session)

    by_name: dict[tuple[str, str], list[m.Person]] = defaultdict(list)
    for person in session.scalars(select(m.Person)):
        by_name[(person.source_kind, person.canonical_name)].append(person)

    evidence: dict[int, list[m.PersonEvidence]] = defaultdict(list)
    for row in session.scalars(select(m.PersonEvidence)):
        evidence[row.person_id].append(row)

    lab_labels = {r.id: r.label for r in session.scalars(select(m.RefLab))}
    out = []
    for (source_kind, name), people in by_name.items():
        if len({p.lab for p in people}) < 2:
            continue
        if source_kind == GITHUB and not all(
            tiers[p.id] & EMPLOYMENT_TIERS for p in people
        ):
            # A login at two labs with no lab-domain email on *every* side is an
            # outside contributor, not a move. `>= 2` was wrong: the register
            # holds logins at three, four and five labs, and counting records
            # rather than sides published a candidate as soon as any two labs
            # were confirmed — with the unverified third attached and `readsAs`
            # able to point straight at it. Dropped, and the count of what was
            # dropped is reported by `move_candidate_stats`.
            continue

        sides = sorted(
            (
                {
                    "lab": p.lab,
                    "label": lab_labels.get(p.lab, p.lab),
                    "firstSeen": str(p.first_seen) if p.first_seen else None,
                    "lastSeen": str(p.last_seen) if p.last_seen else None,
                    "tiers": sorted(tiers[p.id]),
                    "assertsEmployment": bool(tiers[p.id] & EMPLOYMENT_TIERS),
                    "sources": sorted(
                        {e.ref_url for e in evidence[p.id] if e.ref_url}
                    )[:5],
                }
                for p in people
            ),
            key=lambda s: s["lastSeen"] or "",
        )
        out.append({
            "name": name,
            "sourceKind": source_kind,
            "labs": [s["label"] for s in sides],
            "sides": sides,
            # The two sides in date order read as a direction. It is a reading,
            # not a finding: the dates are publication dates, and a paper can
            # appear long after the work moved.
            "readsAs": f"{sides[0]['label']} → {sides[-1]['label']}",
            "assertsEmployment": all(s["assertsEmployment"] for s in sides),
            # Days between the two sides. A short gap is a candidate worth an
            # hour of someone's time; a three-year one is usually a large author
            # list from a landmark paper catching a common name. Shown rather
            # than filtered on — the threshold would be a guess, and the reader
            # can see the dates.
            "gapDays": _gap_days(sides),
        })

    # Most recent first. Alphabetical order says nothing; "who moved last" is
    # the question a reader actually opens this list with.
    return sorted(out, key=lambda c: c["sides"][-1]["lastSeen"] or "", reverse=True)


def _gap_days(sides: list[dict]) -> int | None:
    """Days between leaving the earlier lab and appearing at the later one.

    The **gap**, not the span: `sides[0].lastSeen -> sides[-1].firstSeen`. It was
    written as `firstSeen -> lastSeen`, which measures the whole of both
    tenures, and on the live register that reported Yao Li's DeepSeek-to-OpenAI
    move as 956 days when the actual gap is 114. The page renders the number as
    "2.6 years apart", and the wide gaps are precisely the ones a reader is told
    to discount as a common name on a large author list — so the bug pushed the
    reader to dismiss the most move-like candidate the system has.

    Negative when the two tenures overlap: a person publishing at both labs in
    the same period is a different and more interesting thing than a clean move,
    so it is reported rather than clamped to zero.

    Returns:
        The gap in days, or None if either date is missing.
    """
    left, right = sides[0]["lastSeen"], sides[-1]["firstSeen"]
    if not left or not right:
        return None
    return (date.fromisoformat(right) - date.fromisoformat(left)).days


def move_candidate_stats(session: Session) -> dict:
    """How many cross-lab names there are, and how many survive the tier gate.

    Reported alongside the candidates because the gate is the interesting part:
    a filter that removes nothing is not a filter, and the design-doc claim is
    specifically that most cross-lab GitHub names are noise.

    Args:
        session: Open session.

    Returns:
        ``{"paper": {...}, "github": {...}}``, each with `raw` and `kept`.
    """
    tiers = _tiers_by_person(session)
    by_name: dict[tuple[str, str], list[m.Person]] = defaultdict(list)
    for person in session.scalars(select(m.Person)):
        by_name[(person.source_kind, person.canonical_name)].append(person)

    stats = {PAPER: {"raw": 0, "kept": 0}, GITHUB: {"raw": 0, "kept": 0}}
    for (source_kind, _name), people in by_name.items():
        if len({p.lab for p in people}) < 2 or source_kind not in stats:
            continue
        stats[source_kind]["raw"] += 1
        if source_kind == PAPER or all(
            tiers[p.id] & EMPLOYMENT_TIERS for p in people
        ):
            stats[source_kind]["kept"] += 1
    return stats


def search(session: Session, query: str, limit: int = 50) -> list[dict]:
    """People whose canonical name or any recorded identity matches `query`.

    Searches identities as well as names because the identity table *is* the
    entity-resolution surface: a reader who knows a GitHub login should not have
    to know the byline it was harvested beside.

    Args:
        session: Open session.
        query: Case-insensitive substring.
        limit: Maximum rows.

    Returns:
        One dict per matching person, with their identities and evidence tiers.
    """
    if not query.strip():
        return []
    pattern = f"%{query.strip().lower()}%"

    matched_ids = set(
        session.scalars(
            select(m.Person.id).where(func.lower(m.Person.canonical_name).like(pattern))
        )
    ) | set(
        session.scalars(
            select(m.PersonIdentity.person_id).where(
                func.lower(m.PersonIdentity.value).like(pattern)
            )
        )
    )
    if not matched_ids:
        return []

    tiers = _tiers_by_person(session)
    identities: dict[int, list] = defaultdict(list)
    for row in session.scalars(
        select(m.PersonIdentity).where(m.PersonIdentity.person_id.in_(matched_ids))
    ):
        identities[row.person_id].append({"kind": row.kind, "value": row.value})

    lab_labels = {r.id: r.label for r in session.scalars(select(m.RefLab))}
    people = session.scalars(select(m.Person).where(m.Person.id.in_(matched_ids))).all()
    return [
        {
            "id": p.id,
            "name": p.canonical_name,
            "lab": p.lab,
            "label": lab_labels.get(p.lab, p.lab),
            "sourceKind": p.source_kind,
            "firstSeen": str(p.first_seen) if p.first_seen else None,
            "lastSeen": str(p.last_seen) if p.last_seen else None,
            "identities": identities[p.id],
            "tiers": sorted(tiers[p.id]),
            "assertsEmployment": bool(tiers[p.id] & EMPLOYMENT_TIERS),
        }
        for p in sorted(people, key=lambda p: (p.canonical_name, p.lab))[:limit]
    ]
