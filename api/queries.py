"""Read-side queries: clean tables -> the item shape the frontend renders.

One denormalized dict per article + its latest classification, with tags and
connections nested inline. The dataset is small (a few hundred articles), so
this ships everything in one response and lets the frontend filter/sort/frame
per audience client-side, rather than duplicating that logic on both sides.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.connect import match_name
from app.pipeline.registry import PAPERS_CORPUS


def _short_name(name: str) -> str:
    """A holding's display name, legal suffixes stripped (falls back to the full name)."""
    return match_name(name) or name


def _humanize(token: str) -> str:
    """A snake_case token as a short phrase: 'revenue_contract' -> 'Revenue contract'."""
    return token.replace("_", " ").capitalize()


def _connection_label(route: str, via: str, mech_labels: dict, cat_labels: dict) -> str:
    """What this connection actually matched — never the bare route name.

    The frontend groups connections by company and lists each one under the
    company's name, so this is the line the reader sees per row; a route name
    like "mechanism" on its own says nothing.

    Args:
        route: mechanism | category | lab_exposure | named.
        via: The route's join key (see app.connect.connections_for's docstring).
        mech_labels: mechanism id -> label.
        cat_labels: category id -> label.

    Returns:
        A short, human label for the row.
    """
    if route == "mechanism":
        return mech_labels.get(via, via)
    if route == "category":
        return cat_labels.get(via, via)
    if route == "lab_exposure":
        _, _, kind = via.partition(":")
        return f"Lab relationship — {_humanize(kind)}"
    if route == "named":
        _, _, matched = via.partition(":")
        return f'Named in article — "{matched}"'
    return via


def build_items(session: Session, prompt_version: str | tuple[str, ...]) -> list[dict]:
    """Assemble the full item list for one classification version.

    Args:
        session: Open session.
        prompt_version: Which classification run to read (see app.cli.PROMPT_VERSION).
            A tuple spans several, which is how papers and announcements reach
            one list.

    Returns:
        One dict per article that has a classification for this version, each
        carrying its mechanism/category/practice tags and holding connections.
    """
    labs = {r.id: r.label for r in session.scalars(select(m.RefLab))}
    mech_labels = {r.id: r.label for r in session.scalars(select(m.RefMechanism))}
    cat_labels = {r.id: r.label for r in session.scalars(select(m.RefCategory))}
    prac_labels = {r.id: r.label for r in session.scalars(select(m.RefPractice))}
    holding_names = {h.isin: _short_name(h.name) for h in session.scalars(select(m.Holding))}

    versions = ((prompt_version,) if isinstance(prompt_version, str)
                else tuple(prompt_version))
    classifications = {
        c.article_id: c for c in session.scalars(
            select(m.Classification).where(m.Classification.prompt_version.in_(versions))
        )
    }
    # Which leg a row came from. `source_file` is the provenance the shared
    # bronze table already carries, so the reader does not need a second column
    # on `articles` that could disagree with it.
    # Columns, not entities: `RawArticle.payload` is an eagerly-mapped JSON
    # column holding full article text, so hydrating ~700 rows to read two
    # strings parsed and threw away several MB of JSON on every dashboard load.
    doc_types = {
        row_id: ("paper" if source_file == PAPERS_CORPUS else "announcement")
        for row_id, source_file in session.execute(
            select(m.RawArticle.id, m.RawArticle.source_file))
    }
    cls_ids = [c.id for c in classifications.values()]

    mechs_by_cls: dict[int, list] = defaultdict(list)
    for row in session.scalars(select(m.ArticleMechanism)
                                .where(m.ArticleMechanism.classification_id.in_(cls_ids))
                                .order_by(m.ArticleMechanism.ordinal)):
        mechs_by_cls[row.classification_id].append(row)

    cats_by_cls: dict[int, list] = defaultdict(list)
    for row in session.scalars(select(m.ArticleCategory)
                                .where(m.ArticleCategory.classification_id.in_(cls_ids))
                                .order_by(m.ArticleCategory.ordinal)):
        cats_by_cls[row.classification_id].append(row)

    pracs_by_cls: dict[int, list] = defaultdict(list)
    for row in session.scalars(select(m.ArticlePractice)
                                .where(m.ArticlePractice.classification_id.in_(cls_ids))
                                .order_by(m.ArticlePractice.ordinal)):
        pracs_by_cls[row.classification_id].append(row)

    conns_by_article: dict[int, list] = defaultdict(list)
    for row in session.scalars(select(m.Connection).order_by(m.Connection.strength.desc())):
        conns_by_article[row.article_id].append(row)

    # Near-duplicate grouping. Every row still ships, including the folded ones:
    # the frontend hides them behind their anchor's expander, and a reader who
    # wants to check a merge has to be able to see what was merged. Filtering
    # them out server-side would make a collapse indistinguishable from an
    # article that was never ingested.
    groups = {g.article_id: g for g in session.scalars(select(m.ArticleGroup))}

    items = []
    for art in session.scalars(select(m.Article).order_by(m.Article.published_on.desc())):
        cls = classifications.get(art.id)
        if cls is None:
            continue
        group = groups.get(art.id)
        items.append({
            "id": art.id,
            "docType": doc_types.get(art.raw_article_id, "announcement"),
            "lab": art.lab,
            "labLabel": labs.get(art.lab, art.lab),
            "date": str(art.published_on),
            "title": art.title,
            "groupId": group.group_id if group else f"g{art.id}",
            "groupSize": group.group_size if group else 1,
            "isAnchor": group.is_anchor if group else True,
            "groupMethod": group.method if group else "singleton",
            "groupReason": group.reason if group else "",
            "summary": cls.summary,
            "notableReason": cls.notable_reason,
            "sourceUrl": art.url,
            "score": cls.score,
            "band": cls.band,
            "aiScore": cls.ai_score,
            "aiBand": cls.ai_band,
            "mechanisms": [
                {
                    "id": t.mechanism_id,
                    "label": mech_labels.get(t.mechanism_id, t.mechanism_id),
                    "sign": t.sign,
                    "magnitude": t.magnitude,
                    "confidence": t.confidence,
                    "reason": t.reason,
                    "quote": t.quote,
                }
                for t in mechs_by_cls.get(cls.id, [])
            ],
            "categories": [
                {
                    "id": t.category_id,
                    "label": cat_labels.get(t.category_id, t.category_id),
                    "sign": t.sign,
                    "confidence": t.confidence,
                    "reason": t.reason,
                    "quote": t.quote,
                }
                for t in cats_by_cls.get(cls.id, [])
            ],
            "practices": [
                {
                    "id": t.practice_id,
                    "label": prac_labels.get(t.practice_id, t.practice_id),
                    "action": t.action,
                    "impact": t.impact,
                    "confidence": t.confidence,
                    "reason": t.reason,
                    "quote": t.quote,
                }
                for t in pracs_by_cls.get(cls.id, [])
            ],
            "connections": [
                {
                    "holding": holding_names.get(c.isin, c.isin),
                    "route": c.route,
                    "label": _connection_label(c.route, c.via, mech_labels, cat_labels),
                    "direction": c.direction,
                    "strength": round(c.strength, 2),
                    "magnitude": c.holding_magnitude or c.article_magnitude,
                    "confidence": c.holding_confidence or c.article_confidence,
                    "note": c.holding_why or c.article_reason or "",
                }
                for c in conns_by_article.get(art.id, [])
            ],
        })
    return items
