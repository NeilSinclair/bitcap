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
from app.pipeline.registry import CORPUS_LABELS, RELEASES
from app.scoring import ai_score_of, score_of

ROOT = Path(__file__).parent.parent
SCORING = ROOT / "config" / "scoring.yaml"
REPO_SIGNALS = ROOT / "config" / "repo_signals.yaml"

TAG_TABLES = (m.ArticleMechanism, m.ArticleCategory, m.ArticlePractice)


def _drop_classifications(session: Session, article_id: int, prompt_version: str) -> None:
    """Delete an article's classification and tag rows for one prompt version.

    Args:
        session: Open session; this flushes nothing, the caller commits.
        article_id: Article whose rows to remove.
        prompt_version: Only rows for this version are touched.
    """
    old_ids = session.scalars(
        select(m.Classification.id).where(
            m.Classification.article_id == article_id,
            m.Classification.prompt_version == prompt_version)
    ).all()
    for table in TAG_TABLES:
        session.execute(delete(table).where(table.classification_id.in_(old_ids)))
    session.execute(delete(m.Classification).where(m.Classification.id.in_(old_ids)))


def off_topic_repos(session: Session) -> set[str]:
    """Repositories the relevance filter has judged off-topic, as `"org/repo"`.

    Reads the verdict cache and never calls a provider — this runs over the
    whole corpus on every firing.

    Returns:
        Set of `"org/repo"`, empty when the filter is disabled.
    """
    config = (yaml.safe_load(REPO_SIGNALS.read_text(encoding="utf-8")) or {}).get(
        "relevance") or {}
    if not config.get("enabled"):
        return set()

    from app.pipeline import repo_relevance

    return repo_relevance.off_topic(session, config)


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
    counts = {"articles": 0, "classifications": 0, "tags": 0, "no_classification": 0,
              "off_topic": 0}

    # The relevance filter's second gate. The first one, in the releases
    # adapter, stops off-topic repositories being *fetched* from now on — but
    # this function reads the whole of bronze and upserts silver on every run,
    # so the releases already in the corpus would come straight back and a
    # one-off DELETE would be undone by the next firing.
    #
    # Gating derivation rather than deleting bronze keeps the whole thing
    # reversible: the raw rows stay as the audit trail, and flipping a verdict
    # re-derives the article and its classification on the next run with no
    # backfill and no refetch.
    off_topic = off_topic_repos(session)
    releases_corpus = CORPUS_LABELS[RELEASES]

    for url, raw in raw_articles.items():
        p = raw.payload
        if (off_topic and raw.source_file == releases_corpus
                and f"{p.get('org')}/{p.get('repo')}" in off_topic):
            existing = articles.get(url)
            if existing is not None:
                # Only the classification is removed, not the article. Every
                # reader — `build_items`, the digest, `dedupe._rows` — joins
                # classifications, so this is what takes the row off the
                # dashboard, and it leaves no foreign keys dangling for the
                # stages that run after this one.
                _drop_classifications(session, existing.id, prompt_version)
            counts["off_topic"] += 1
            continue
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
            art.text = p["text"]
            # A `first_seen` date is when discovery noticed the item, not when
            # the lab published it -- model spec pages carry no publication
            # date at all (fetch_announcements.from_model_index). Re-emitting
            # one on a later run would otherwise walk `published_on` forward:
            # `_settled_urls` only skips items already classified under the
            # current PROMPT_VERSION, so a version bump or a budget-capped run
            # re-emits it dated today, and a September launch silently becomes
            # a November one and re-enters the digest as fresh.
            if p.get("date_basis") != "first_seen":
                art.published_on = date.fromisoformat(p["date"])
            art.text_source = p["text_source"]
            art.feed_category = p.get("feed_category")
            art.archive_snapshot = p.get("archive_snapshot")
        counts["articles"] += 1

        rc = raw_cls.get(url)
        if rc is None:
            counts["no_classification"] += 1
            continue
        result = rc.payload

        _drop_classifications(session, art.id, prompt_version)

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
