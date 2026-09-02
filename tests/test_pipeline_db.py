"""End-to-end: the full pipeline against the real committed artifacts.

This is the reconciliation suite. The recomputed investment score must equal
the file register for every article — if the DB and the register ever say
different numbers about the same article, one of them is lying and this test
says so before anything downstream trusts either.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import models as m
from app.cli import cmd_connect, cmd_load
from app.db import create_all

ROOT = Path(__file__).parent.parent
REGISTER = ROOT / "research" / "docs" / "scored_announcements_v7.json"


@pytest.fixture(scope="module")
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    s = Session(engine)
    cmd_load(s, "v7")
    cmd_connect(s, "v7")
    yield s
    s.close()


def test_corpus_fully_loaded(session):
    assert session.scalar(select(func.count()).select_from(m.Article)) == 191
    assert session.scalar(select(func.count()).select_from(m.Classification)) == 191
    assert session.scalar(select(func.count()).select_from(m.Holding)) == 26


def test_scores_reconcile_with_register(session):
    register = {r["url"]: r for r in json.loads(REGISTER.read_text())["scored"]}
    rows = session.execute(
        select(m.Article.url, m.Classification.score, m.Classification.band)
        .join(m.Classification, m.Classification.article_id == m.Article.id)
    ).all()
    assert len(rows) == len(register)
    mismatches = [u for u, score, band in rows
                  if register[u]["score"] != score or register[u]["band"] != band]
    assert mismatches == []


def test_tag_rows_match_register_totals(session):
    register = json.loads(REGISTER.read_text())["scored"]
    for table, key in ((m.ArticleMechanism, "mechanisms"),
                       (m.ArticleCategory, "categories"),
                       (m.ArticlePractice, "practices")):
        expected = sum(len(r[key]) for r in register)
        assert session.scalar(select(func.count()).select_from(table)) == expected


def test_connections_spot_checks(session):
    def conns(url_fragment, **filters):
        q = (select(m.Connection).join(m.Article, m.Article.id == m.Connection.article_id)
             .where(m.Article.url.contains(url_fragment)))
        for k, v in filters.items():
            q = q.where(getattr(m.Connection, k) == v)
        return session.scalars(q).all()

    # Jalapeño x NVDA: positive article tag x negative holding edge.
    nvda = [c for c in conns("jalapeno-first-results", via="custom_silicon_substitution")
            if c.isin == "US67066G1040"]
    assert len(nvda) == 1
    assert (nvda[0].direction, nvda[0].strength) == ("negative", 0.3333)
    assert nvda[0].holding_why and nvda[0].article_quote  # both sides explain themselves

    # Export directive reaches TeraWulf through the anthropic lab edge.
    wulf = [c for c in conns("fable-mythos-access", route="lab_exposure")
            if c.via == "anthropic:revenue_contract"]
    assert {c.direction for c in wulf} == {"mixed"}

    # The article at openai.com/index/nvidia/chatgpt-work names NVIDIA.
    named = conns("nvidia/chatgpt-work", route="named")
    assert [c.via for c in named] == ["name:NVIDIA"]


def test_no_zero_strength_and_no_dormant_labs(session):
    assert session.scalar(select(func.count()).select_from(m.Connection)
                          .where(m.Connection.strength <= 0)) == 0
    dormant_isins = {e.isin for e in session.scalars(
        select(m.HoldingLabExposure).where(m.HoldingLabExposure.is_dormant))}
    dormant_vias = {f"{e.lab}:{e.kind}" for e in session.scalars(
        select(m.HoldingLabExposure).where(m.HoldingLabExposure.is_dormant))}
    fired = session.scalars(select(m.Connection).where(
        m.Connection.route == "lab_exposure",
        m.Connection.via.in_(dormant_vias),
        m.Connection.isin.in_(dormant_isins))).all()
    assert fired == []


def test_rerun_converges(session):
    before = {t.name: session.scalar(select(func.count()).select_from(t))
              for t in m.Base.metadata.tables.values()}
    cmd_load(session, "v7")
    cmd_connect(session, "v7")
    after = {t.name: session.scalar(select(func.count()).select_from(t))
             for t in m.Base.metadata.tables.values()}
    before.pop("pipeline_runs"), after.pop("pipeline_runs")  # runs do accumulate
    assert before == after


def test_runs_recorded_with_watermarks(session):
    runs = session.scalars(select(m.PipelineRun)).all()
    assert all(r.status == "succeeded" for r in runs)
    latest = max((r for r in runs if r.watermarks), key=lambda r: r.id)
    assert set(latest.watermarks) == {"openai", "anthropic", "deepseek"}
