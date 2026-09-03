"""api.queries.build_items: clean tables -> the shape the frontend renders.

The silent failure this catches: ref labels or a holding's display name
failing to resolve, a connection's note/magnitude/confidence picking the
wrong side, a connection's label falling back to a bare route name, or an
article with no classification (or the wrong prompt version) leaking into
the list.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as m
from app.db import create_all
from api.queries import _connection_label, build_items


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        s.add(m.RefLab(id="anthropic", label="Anthropic", config_version=1))
        s.add(m.RefMechanism(id="training_compute_up", label="Training compute demand rises",
                             description="d", polarity_note="p", config_version=2))
        s.add(m.RefCategory(id="memory_storage", label="Memory and storage", definition="d",
                            boundary="b", lab_signal_routable=True, config_version=2))
        s.add(m.RefPractice(id="orchestration", label="How you would build the pipeline",
                            description="d", dimensions={}, config_version=2))
        s.add(m.Holding(isin="US0001", name="Micron Technology, Inc.", custodian_name="c",
                        aliases=[], ticker="MU", weight_pct=1.0, ai_role="primary",
                        holdings_version=1, companies_version=2))
        s.flush()  # refs and holdings must land before anything that FKs to them

        s.add(m.RawArticle(id=1, url="https://x/a", payload={}, content_hash="h1", source_file="f"))
        s.add(m.RawArticle(id=2, url="https://x/b", payload={}, content_hash="h2", source_file="f"))
        s.flush()

        s.add(m.Article(id=1, url="https://x/a", raw_article_id=1, lab="anthropic",
                        title="A cluster announcement", published_on=date(2026, 8, 20),
                        text="body", text_source="full_text"))
        s.add(m.Article(id=2, url="https://x/b", raw_article_id=2, lab="anthropic",
                        title="No classification yet", published_on=date(2026, 8, 21),
                        text="body", text_source="full_text"))
        s.flush()

        s.add(m.Classification(id=1, article_id=1, prompt_version="v7", scoring_version=4,
                               event_type="compute_commitment", summary="s",
                               notable=True, notable_reason="why", score=80.0, band="high",
                               ai_score=0.0, ai_band="none"))
        s.flush()

        s.add(m.ArticleMechanism(classification_id=1, mechanism_id="training_compute_up",
                                 sign="positive", magnitude="high", confidence="high",
                                 reason="r", quote="q", ordinal=0))
        s.add(m.ArticlePractice(classification_id=1, practice_id="orchestration",
                                action="watch", impact="low", confidence="low",
                                dimensions=[], reason="r", quote="q", ordinal=0))
        s.add(m.Connection(article_id=1, isin="US0001", route="mechanism", via="training_compute_up",
                           direction="positive", strength=0.62, article_reason="article side",
                           holding_why="holding side", article_magnitude="high",
                           article_confidence="high", holding_magnitude="medium",
                           holding_confidence="medium"))
        s.commit()
        yield s


def test_labels_and_names_resolve(session):
    items = build_items(session, "v7")
    assert len(items) == 1  # article 2 has no v7 classification
    item = items[0]
    assert item["labLabel"] == "Anthropic"
    assert item["mechanisms"][0]["label"] == "Training compute demand rises"
    assert item["practices"][0]["label"] == "How you would build the pipeline"
    assert item["connections"][0]["holding"] == "Micron"  # legal suffix stripped
    assert item["connections"][0]["label"] == "Training compute demand rises"


def test_connection_note_and_magnitude_prefer_holding_side(session):
    item = build_items(session, "v7")[0]
    conn = item["connections"][0]
    assert conn["note"] == "holding side"
    assert (conn["magnitude"], conn["confidence"]) == ("medium", "medium")


def test_falls_back_to_article_side_when_no_holding_side(session):
    session.query(m.Connection).update({
        "holding_why": None, "holding_magnitude": None, "holding_confidence": None,
    })
    session.commit()
    conn = build_items(session, "v7")[0]["connections"][0]
    assert conn["note"] == "article side"
    assert (conn["magnitude"], conn["confidence"]) == ("high", "high")


def test_wrong_prompt_version_yields_nothing(session):
    assert build_items(session, "v99") == []


class TestConnectionLabel:
    """The line shown per row under a company's name — never a bare route name."""

    def test_mechanism_resolves_from_ref_labels(self):
        got = _connection_label("mechanism", "training_compute_up",
                                {"training_compute_up": "Training compute demand rises"}, {})
        assert got == "Training compute demand rises"

    def test_category_resolves_from_ref_labels(self):
        got = _connection_label("category", "memory_storage", {}, {"memory_storage": "Memory and storage"})
        assert got == "Memory and storage"

    def test_lab_exposure_humanizes_the_kind(self):
        assert _connection_label("lab_exposure", "anthropic:revenue_contract", {}, {}) == \
            "Lab relationship — Revenue contract"

    def test_named_shows_the_matched_text(self):
        assert _connection_label("named", "alias:AWS", {}, {}) == 'Named in article — "AWS"'

    def test_unresolved_ref_falls_back_to_the_raw_id(self):
        """A ref reload dropping a vocabulary id must be visible, not blank."""
        assert _connection_label("mechanism", "some_id", {}, {}) == "some_id"
