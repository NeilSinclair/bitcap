"""Derive the clean tables from the raw layer.

Articles are upserted by URL; each article's classification and tag rows are
deleted and rebuilt inside the session's transaction — the derivation is
deterministic, so a re-run converges on the same rows. Both scores are
recomputed here from config/scoring.yaml via app.scoring: `score` doubles as a
reconciliation check against the file register, `ai_score` is new data the
register never carried.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import models as m
from app.scoring import ai_score_of, score_of

ROOT = Path(__file__).parent.parent
SCORING = ROOT / "config" / "scoring.yaml"

TAG_TABLES = (m.ArticleMechanism, m.ArticleCategory, m.ArticlePractice)


def _tag_rows(cls_id: int, payload: dict) -> list:
    """Build tag rows for one classification payload."""
    rows: list = []
    for ordinal, t in enumerate(payload.get("mechanisms", [])):
        rows.append(m.ArticleMechanism(
            classification_id=cls_id, mechanism_id=t["id"], sign=t["sign"],
            magnitude=t["magnitude"], confidence=t["confidence"], reason=t["reason"],
            quote=t["quote"], quote_repaired=t.get("quote_repaired", False),
            ordinal=ordinal))
    for ordinal, t in enumerate(payload.get("categories", [])):
        rows.append(m.ArticleCategory(
            classification_id=cls_id, category_id=t["id"], sign=t["sign"],
            confidence=t["confidence"], reason=t["reason"], quote=t["quote"],
            quote_repaired=t.get("quote_repaired", False), ordinal=ordinal))
    for ordinal, t in enumerate(payload.get("practices", [])):
        rows.append(m.ArticlePractice(
            classification_id=cls_id, practice_id=t["id"], action=t["action"],
            impact=t["impact"], confidence=t["confidence"],
            dimensions=t.get("dimensions") or [], reason=t["reason"], quote=t["quote"],
            quote_repaired=t.get("quote_repaired", False), ordinal=ordinal))
    return rows


def transform(session: Session, prompt_version: str, run_id: int | None = None) -> dict:
    """Raw articles + classifications → clean articles, classifications, tags.

    Args:
        session: Open session; this function flushes, the caller commits.
        prompt_version: Which raw classifications to derive from.
        run_id: Unused here (clean rows carry no run column); accepted so the
            CLI can call every stage uniformly.

    Returns:
        Counts: articles upserted, classifications rebuilt, tag rows written,
        and raw articles skipped for having no classification.
    """
    rules = yaml.safe_load(SCORING.read_text())
    raw_articles = {r.url: r for r in session.scalars(select(m.RawArticle))}
    raw_cls = {
        r.url: r for r in session.scalars(
            select(m.RawLlmResponse).where(m.RawLlmResponse.prompt_version == prompt_version)
        )
    }
    articles = {a.url: a for a in session.scalars(select(m.Article))}
    counts = {"articles": 0, "classifications": 0, "tags": 0, "no_classification": 0}

    for url, raw in raw_articles.items():
        p = raw.payload
        art = articles.get(url)
        if art is None:
            art = m.Article(url=url, raw_article_id=raw.id, lab=p["lab"], title=p["title"],
                            published_on=date.fromisoformat(p["date"]), text=p["text"],
                            text_source=p["text_source"],
                            feed_category=p.get("feed_category"),
                            archive_snapshot=p.get("archive_snapshot"))
            session.add(art)
            session.flush()
        else:
            art.raw_article_id, art.lab, art.title = raw.id, p["lab"], p["title"]
            art.published_on, art.text = date.fromisoformat(p["date"]), p["text"]
            art.text_source = p["text_source"]
            art.feed_category = p.get("feed_category")
            art.archive_snapshot = p.get("archive_snapshot")
        counts["articles"] += 1

        rc = raw_cls.get(url)
        if rc is None:
            counts["no_classification"] += 1
            continue
        result = rc.payload

        old_ids = session.scalars(
            select(m.Classification.id).where(
                m.Classification.article_id == art.id,
                m.Classification.prompt_version == prompt_version)
        ).all()
        for table in TAG_TABLES:
            session.execute(delete(table).where(table.classification_id.in_(old_ids)))
        session.execute(delete(m.Classification).where(m.Classification.id.in_(old_ids)))

        score, band = score_of(result, rules)
        ai_score, ai_band = ai_score_of(result, rules)
        cls = m.Classification(
            article_id=art.id, prompt_version=prompt_version,
            scoring_version=rules["version"], event_type=result["event_type"],
            summary=result["summary"], is_signal=result.get("is_signal"),
            notable=result.get("notable", False),
            notable_reason=result.get("notable_reason", ""),
            dropped_tags=result.get("dropped_tags", []),
            score=score, band=band, ai_score=ai_score, ai_band=ai_band)
        session.add(cls)
        session.flush()
        rows = _tag_rows(cls.id, result)
        session.add_all(rows)
        counts["classifications"] += 1
        counts["tags"] += len(rows)

    session.flush()
    return counts
