"""Schema round-trip: every table accepts and returns a row.

The silent failure this catches: a column type that Postgres accepts and
sqlite mangles (or vice versa), or a JSON column that loses its content.
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models as m
from app.db import create_all


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


def test_every_table_round_trips(session):
    run = m.PipelineRun(kind="load_raw", stats={"raw_articles": 1}, watermarks={"openai": "2026-08-31"})
    session.add(run)
    session.flush()

    session.add_all([
        m.RawArticle(url="https://x/a", payload={"title": "t"}, content_hash="h",
                     source_file="announcements.json", load_run_id=run.id),
        m.RawLlmResponse(url="https://x/a", prompt_version="v7",
                            payload={"event_type": "other"}, load_run_id=run.id),
        m.RawCost(url="https://x/a", model="claude-sonnet-5", input_tokens=1,
                  output_tokens=2, usd=0.01, seconds=1.0, at="2026-09-02T00:00:00+00:00"),
        m.RefLab(id="openai", label="OpenAI", config_version=1),
        m.RefMechanism(id="mech", label="M", description="d", polarity_note="p", config_version=2),
        m.RefCategory(id="cat", label="C", definition="d", boundary="b",
                      lab_signal_routable=True, config_version=2),
        m.RefPractice(id="prac", label="P", description="d",
                      dimensions={"coding": "x"}, config_version=2),
        m.GoldSnapshot(prompt_version="v7", metrics={"f1": 0.88}, run_id=run.id),
        m.RunSource(run_id=run.id, leg="announcements", source_id="openai",
                    status="succeeded", items_seen=12, items_new=3, cost_usd=0.04,
                    duration_s=2.5),
        m.SourceState(leg="announcements", source_id="openai",
                      watermark={"max_published": "2026-08-31"}),
        m.Alert(kind="system", rule="source_down", severity="warning",
                subject="openai down", body="3 consecutive runs",
                payload={"consecutive_failures": 3},
                dedupe_key="source_down:announcements:openai:2026-09-03",
                run_id=run.id),
        m.RawPaper(url="https://deepmind.google/p/1", lab="openai",
                   payload={"title": "p"}, content_hash="h", load_run_id=run.id),
        m.RawGithubPerson(org="openai", login="ada", lab="openai",
                          payload={"commits": 3}, content_hash="h", load_run_id=run.id),
        m.RawGithubRepo(org="openai", repo="gym", pushed_at="2026-08-31T00:00:00Z",
                        payload={"total": 3, "commits": []}, content_hash="h",
                        load_run_id=run.id),
        m.UnresolvedItem(leg="papers", source_id="mistral", kind="paper",
                         identifier="Some announcement", reason="no arxiv match",
                         run_id=run.id),
        m.Digest(kind="investment",
                 window_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
                 window_end=datetime(2026, 9, 3, tzinfo=timezone.utc),
                 prompt_version="v7",
                 stats={"considered": 6, "surfaced": 1, "suppressed": 5},
                 payload={"items": [{"id": 1, "title": "t"}]},
                 run_id=run.id),
    ])
    person = m.Person(lab="openai", source_kind="paper", canonical_name="Ada Lovelace",
                      first_seen=date(2026, 3, 1), last_seen=date(2026, 8, 26))
    session.add(person)
    session.flush()
    session.add_all([
        m.PersonIdentity(person_id=person.id, kind="paper_name", value="Ada Lovelace",
                         lab="openai"),
        m.PersonEvidence(person_id=person.id, source_kind="paper",
                         ref_url="https://deepmind.google/p/1",
                         observed_on=date(2026, 8, 26), tier="model_asserted",
                         payload={"affiliations": ["OpenAI"]}),
    ])
    session.add(m.Holding(isin="US1", name="Co", custodian_name="Co Inc. Reg. Shs",
                          aliases=["CoCorp"], ticker="CO", weight_pct=1.0,
                          ai_role="primary", holdings_version=1, companies_version=1))
    session.flush()
    session.add_all([
        m.HoldingCategory(isin="US1", category_id="cat"),
        m.HoldingMechanism(isin="US1", mechanism_id="mech", sign="positive",
                           magnitude="high", confidence="high", why="w"),
        m.HoldingLabExposure(isin="US1", lab="anthropic", kind="equity", sign="positive",
                             magnitude="high", confidence="high", why="w", is_dormant=False),
    ])
    session.flush()
    raw_id = session.scalars(select(m.RawArticle.id)).one()
    art = m.Article(url="https://x/a", raw_article_id=raw_id, lab="openai", title="t",
                    published_on=date(2026, 8, 31), text="body", text_source="full_text")
    # A second article purely so `ArticleLink` has two ends. A link between an
    # article and itself would round-trip and prove nothing about the pair.
    release = m.Article(url="https://x/r", raw_article_id=raw_id, lab="openai",
                        title="openai/codex rust-v0.1.0", published_on=date(2026, 8, 31),
                        text="Added GPT-6-Astra", text_source="github_release")
    session.add_all([art, release])
    session.flush()
    cls = m.Classification(article_id=art.id, prompt_version="v7", scoring_version=4,
                           event_type="other", summary="s", is_signal=True, notable=False,
                           notable_reason="", dropped_tags=["mechanisms:x"],
                           score=0.0, band="none", ai_score=11.1, ai_band="low")
    session.add(cls)
    session.flush()
    session.add_all([
        m.ArticleMechanism(classification_id=cls.id, mechanism_id="mech", sign="positive",
                           magnitude="high", confidence="high", reason="r", quote="q", ordinal=0),
        m.ArticleCategory(classification_id=cls.id, category_id="cat", sign="positive",
                          confidence="high", reason="r", quote="q", ordinal=0),
        m.ArticlePractice(classification_id=cls.id, practice_id="prac", action="watch",
                          impact="low", confidence="low", dimensions=["coding"],
                          reason="r", quote="q", ordinal=0),
        m.Connection(article_id=art.id, isin="US1", route="mechanism", via="mech",
                     direction="positive", strength=1.0, article_sign="positive",
                     holding_sign="positive", holding_why="w"),
        # expires_at NULL is the meaningful case: an immutable arXiv id, cached
        # forever. The nullable column has to survive a round trip, because
        # "never expires" and "expired" are one typo apart (D53).
        m.FetchCache(url="https://arxiv.org/html/2501.00001v1", body="<html/>",
                     expires_at=None),
        m.RawArticleEmbedding(url="https://example.test/a", content_hash="h",
                              model="text-embedding-3-small", dim=3,
                              vector="AACAPwAAAAAAAAAA"),
        m.ArticleGroup(article_id=art.id, group_id="g1", is_anchor=True, group_size=1,
                       method="singleton", reason="no near-duplicate found in the window"),
        m.ArticleLink(from_article_id=release.id, to_article_id=art.id,
                      relation="names_model", evidence="gpt-6-astra"),
    ])
    session.commit()

    # JSON content survives, not just row counts.
    assert session.scalars(select(m.PipelineRun)).one().stats == {"raw_articles": 1}
    assert session.scalars(select(m.RefPractice)).one().dimensions == {"coding": "x"}
    assert session.scalars(select(m.Classification)).one().dropped_tags == ["mechanisms:x"]
    assert session.scalars(select(m.ArticlePractice)).one().dimensions == ["coding"]
    assert session.scalars(select(m.SourceState)).one().watermark == {"max_published": "2026-08-31"}
    assert session.scalars(select(m.Alert)).one().payload == {"consecutive_failures": 3}
    assert session.scalars(select(m.Digest)).one().payload == {"items": [{"id": 1, "title": "t"}]}
    for table in m.Base.metadata.tables.values():
        assert session.execute(select(table).limit(1)).first() is not None, table.name


def test_unique_constraints_hold(session):
    session.add(m.RawArticle(url="u", payload={}, content_hash="h", source_file="f"))
    session.commit()
    session.add(m.RawArticle(url="u", payload={}, content_hash="h2", source_file="f"))
    with pytest.raises(Exception):
        session.commit()
