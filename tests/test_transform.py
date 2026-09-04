"""Transform: clean rows derive correctly and re-runs converge.

The silent failure this catches: the DB's recomputed score drifting from the
rule the register was scored under, or a rebuild that duplicates tag rows.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import models as m
from app.db import create_all
from app.transform import transform

RESULT = {
    "event_type": "pricing_change",  # weight 5
    "summary": "s",
    "is_signal": True,
    "notable": False,
    "notable_reason": "",
    "dropped_tags": [],
    "mechanisms": [{"id": "inference_cost_down", "sign": "positive", "magnitude": "high",
                    "confidence": "high", "reason": "r", "quote": "q"}],
    "categories": [],
    "practices": [{"id": "evaluation", "action": "investigate", "impact": "medium",
                   "confidence": "high", "dimensions": [], "reason": "r", "quote": "q"}],
}


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        s.add(m.RefLab(id="openai", label="OpenAI", config_version=1))
        s.add(m.RefMechanism(id="inference_cost_down", label="M", description="d",
                             polarity_note="p", config_version=2))
        s.add(m.RefPractice(id="evaluation", label="P", description="d",
                            dimensions={}, config_version=2))
        s.add(m.RawArticle(url="https://x/a", content_hash="h", source_file="f", payload={
            "lab": "openai", "url": "https://x/a", "date": "2026-08-01", "title": "A",
            "text": "body", "text_source": "full_text"}))
        s.add(m.RawLlmResponse(url="https://x/a", prompt_version="v7", payload=RESULT))
        s.commit()
        yield s


def test_scores_recomputed_from_rules(session):
    counts = transform(session, "v7")
    assert counts == {"articles": 1, "classifications": 1, "tags": 2, "no_classification": 0}
    cls = session.scalars(select(m.Classification)).one()
    # pricing_change (5/5) x high*high (3/3) = 100; ai: investigate (2/3) x medium*high (2/3)
    assert (cls.score, cls.band) == (100.0, "high")
    assert (cls.ai_score, cls.ai_band) == (44.4, "medium")
    assert cls.scoring_version == 4


def test_rerun_converges(session):
    transform(session, "v7")
    transform(session, "v7")
    assert session.scalar(select(func.count()).select_from(m.Classification)) == 1
    assert session.scalar(select(func.count()).select_from(m.ArticleMechanism)) == 1
    assert session.scalar(select(func.count()).select_from(m.ArticlePractice)) == 1


def test_article_without_classification_still_loads(session):
    session.add(m.RawArticle(url="https://x/b", content_hash="h2", source_file="f", payload={
        "lab": "openai", "url": "https://x/b", "date": "2026-08-02", "title": "B",
        "text": "body", "text_source": "full_text"}))
    session.commit()
    counts = transform(session, "v7")
    assert counts["articles"] == 2 and counts["no_classification"] == 1


class TestFirstSeenDatesDoNotDrift:
    """A discovery date must not walk forward on every re-emit.

    Model spec pages carry no publication date, so `from_model_index` dates
    items by first sight and marks them `date_basis: first_seen`. Those items
    are re-emitted whenever they are not yet settled — a PROMPT_VERSION bump or
    a budget-capped run is enough — and re-dating them would move a September
    launch to November and float it back into the digest as fresh.
    """

    def _reemit(self, session, url, new_date, **extra):
        raw = session.scalars(
            select(m.RawArticle).where(m.RawArticle.url == url)).one()
        raw.payload = {**raw.payload, "date": new_date, **extra}
        session.commit()
        transform(session, "v7")
        return session.scalars(select(m.Article).where(m.Article.url == url)).one()

    def test_a_first_seen_date_is_kept_when_the_item_is_re_emitted(self, session):
        session.add(m.RawArticle(
            url="https://x/model", content_hash="h3", source_file="f", payload={
                "lab": "openai", "url": "https://x/model", "date": "2026-09-04",
                "date_basis": "first_seen", "title": "M", "text": "body",
                "text_source": "model_spec"}))
        session.commit()
        transform(session, "v7")

        art = self._reemit(session, "https://x/model", "2026-11-01",
                           date_basis="first_seen")

        assert art.published_on == date(2026, 9, 4)

    def test_a_real_publication_date_still_updates(self, session):
        # The guard must be narrow: a source that corrects a genuine
        # publication date has to be able to.
        art = self._reemit(session, "https://x/a", "2026-08-05")

        assert art.published_on == date(2026, 8, 5)
