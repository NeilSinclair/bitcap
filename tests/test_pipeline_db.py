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
from app.cli import PROMPT_VERSION, cmd_connect, cmd_load
from app.db import create_all

ROOT = Path(__file__).parent.parent
REGISTER = ROOT / "research" / "docs" / f"scored_announcements_{PROMPT_VERSION}.json"
CORPUS = ROOT / "research" / "docs" / "announcements.json"


def register_rows() -> list[dict]:
    """The scored register for the version the pipeline currently runs.

    Fails with the reason rather than a FileNotFoundError. Bumping
    `PROMPT_VERSION` without re-scoring is a real and expected state — a full
    re-score costs money and is run deliberately (D47) — so the gap between the
    bump and the artifact wants a sentence, not a traceback three frames deep in
    a fixture.
    """
    if not REGISTER.exists():
        raise AssertionError(
            f"no scored register for {PROMPT_VERSION} at {REGISTER.name}. "
            f"`app/cli.PROMPT_VERSION` is {PROMPT_VERSION}, so either the corpus "
            "has not been re-scored at that version yet, or the bump was not "
            "meant to land without it."
        )
    return json.loads(REGISTER.read_text())["scored"]


def corpus_size() -> int:
    """How many articles the committed corpus holds.

    Derived, not hardcoded. These assertions carried a literal 236, which
    meant a firing that correctly discovered new articles turned the suite
    red -- the test failed precisely when the pipeline worked. What is worth
    asserting is that everything in the corpus loads and everything loaded is
    classified, not what the corpus happens to contain this week.
    """
    return len(json.loads(CORPUS.read_text()))


@pytest.fixture(scope="module")
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    s = Session(engine)
    cmd_load(s, PROMPT_VERSION)
    cmd_connect(s, PROMPT_VERSION)
    yield s
    s.close()


def test_corpus_fully_loaded(session):
    # Every corpus article loads, and every article the score cache covers is
    # classified. The second half is the one that catches something: scoring was
    # once deactivated for every lab added after the original three (D10), and
    # their articles loaded with no Classification row at all. The per-run and
    # per-month ceilings in config/pipeline.yaml replaced that switch, so every
    # lab is scored now, and an unclassified article means a real gap -- either
    # a classifier failure or an article ingested but never sent to one.
    #
    # This asserted `== expected` on both counts until 2026-09-05, which made a
    # frozen artifact responsible for tracking a moving one. `announcements.json`
    # rolls forward on every fetch; `announcement_scores/v7/` is a committed
    # snapshot that by definition cannot contain an article discovered after it
    # was written. The discourse channel (D47) found the GPT-6 Astra forum post
    # the day after, and the suite went red for a pipeline that was working --
    # the same failure mode `corpus_size()` was introduced to kill, one layer up.
    # `test_scores_reconcile_with_register` already reasons this way about the
    # register; this test now does too.
    expected = corpus_size()
    assert session.scalar(select(func.count()).select_from(m.Article)) == expected

    # `load_classifications` counts the corpus URLs it found no cache file for.
    # Using the loader's own number keeps the assertion exact: every article the
    # cache covers must reach a Classification row, and a drop between the two
    # is still a hard failure.
    run = session.scalars(
        select(m.PipelineRun).where(m.PipelineRun.kind == "load")
        .order_by(m.PipelineRun.id.desc())
    ).first()
    uncached = run.stats["classifications"]["missing"]
    assert session.scalar(select(func.count()).select_from(m.Classification)) == (
        expected - uncached
    )

    # And the gap must stay small. Without this bound the assertion above moves
    # down in lockstep with any number of unscored articles and reports success
    # while the corpus rots: `budget.per_run_usd` trips mid-firing, sixty new
    # articles never get a cache file, `missing` counts all sixty, and the suite
    # stays green on a pipeline that stopped scoring a quarter of the register.
    # Measured 2026-09-05: exactly 1 (the GPT-6 Astra forum post, D47), so 5
    # leaves room for a normal week's discovery without tolerating a stall.
    assert uncached <= 5, (
        f"{uncached} corpus articles have no v7 classification; the score cache "
        "has fallen behind the corpus by more than ordinary discovery explains"
    )

    # The bite the count assertion used to carry, kept explicitly rather than
    # left implicit in an equality: D10 was a whole lab silently unscored, which
    # a tolerated gap would now hide. Every lab in the corpus must be scored.
    labs_loaded = set(session.scalars(select(m.Article.lab).distinct()))
    labs_scored = set(
        session.scalars(
            select(m.Article.lab).join(m.Classification).distinct()
        )
    )
    assert labs_loaded == labs_scored, f"labs loaded but never scored: {labs_loaded - labs_scored}"

    assert session.scalar(select(func.count()).select_from(m.Holding)) == 26


def test_scores_reconcile_with_register(session):
    # Intersect on URL rather than asserting equal set *size*. The register
    # file is a point-in-time snapshot; `window_months: 3` in sources.yaml
    # rolls forward on every real fetch, so articles present when the
    # register was generated can fall out of the live window (and out of
    # `raw_articles` with them) before the register is regenerated -- 16 such
    # URLs exist here already, independent of and predating this session's
    # register additions. That drift is expected; a *value* mismatch on a
    # URL present in both is the real bug this test exists to catch.
    register = {r["url"]: r for r in register_rows()}
    rows = session.execute(
        select(m.Article.url, m.Classification.score, m.Classification.band)
        .join(m.Classification, m.Classification.article_id == m.Article.id)
    ).all()
    shared = [(u, score, band) for u, score, band in rows if u in register]
    assert shared, "no overlap between the DB and the register -- window drift alone can't explain that"
    mismatches = [u for u, score, band in shared
                  if register[u]["score"] != score or register[u]["band"] != band]
    assert mismatches == []


def test_tag_rows_match_register_totals(session):
    # Same window-drift reasoning as test_scores_reconcile_with_register:
    # sum only over register rows whose article is actually loaded now.
    loaded_urls = set(session.scalars(select(m.Article.url)))
    register = [r for r in register_rows() if r["url"] in loaded_urls]
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

    # Jalapeño x NVDA: positive article tag x negative holding edge composes to a
    # negative connection. The *direction* is the claim; the strength is not
    # asserted as a literal here. It was `0.3333` until 2026-09-05, which was a
    # snapshot of v7 classifier output rather than a property of the join — v8
    # rates the same mechanism medium/medium instead of high/high and it becomes
    # 0.1111, so the literal turned this into a change-detector for the
    # classifier, which is the gold/drift suite's job. The arithmetic itself is
    # covered directly by `weight("medium", "medium") == 1/3` in
    # tests/test_connect.py, so nothing is lost by dropping it.
    nvda = [c for c in conns("jalapeno-first-results", via="custom_silicon_substitution")
            if c.isin == "US67066G1040"]
    assert len(nvda) == 1
    assert nvda[0].direction == "negative"
    assert nvda[0].strength > 0                            # a zero-strength row is never written
    assert nvda[0].holding_why and nvda[0].article_quote   # both sides explain themselves

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
    cmd_load(session, PROMPT_VERSION)
    cmd_connect(session, PROMPT_VERSION)
    after = {t.name: session.scalar(select(func.count()).select_from(t))
             for t in m.Base.metadata.tables.values()}
    before.pop("pipeline_runs"), after.pop("pipeline_runs")  # runs do accumulate
    assert before == after


def test_runs_recorded_with_watermarks(session):
    runs = session.scalars(select(m.PipelineRun)).all()
    assert all(r.status == "succeeded" for r in runs)
    latest = max((r for r in runs if r.watermarks), key=lambda r: r.id)
    assert set(latest.watermarks) == {
        "openai", "anthropic", "deepseek", "google-deepmind", "mistral", "meta-ai", "xai",
    }


class TestFailureLeavesTheDatabaseUsable:
    """A load that dies must not leave the database emptier than it found it.

    `load_refs` opens by deleting the whole derived layer. Before the stages
    stopped committing individually, that deletion was already durable by the
    time anything downstream could fail, so one bad payload emptied `articles`,
    `classifications` and `connections` until a human noticed.
    """

    def _loaded(self):
        engine = create_engine("sqlite:///:memory:")
        create_all(engine)
        s = Session(engine)
        cmd_load(s, PROMPT_VERSION)
        return s

    def test_a_failed_reload_keeps_the_previous_good_state(self, monkeypatch):
        s = self._loaded()
        before = session_counts(s)
        assert before["articles"] == corpus_size() and before["connections"] > 0

        import app.cli as cli

        def boom(*a, **kw):
            raise RuntimeError("transform exploded")

        monkeypatch.setattr(cli, "transform", boom)
        with pytest.raises(RuntimeError):
            cmd_load(s, PROMPT_VERSION)

        assert session_counts(s) == before
        s.close()

    def test_the_failed_run_records_how_far_it_got(self, monkeypatch):
        s = self._loaded()
        import app.cli as cli

        monkeypatch.setattr(cli, "transform", lambda *a, **kw: (_ for _ in ()).throw(ValueError("nope")))
        with pytest.raises(ValueError):
            cmd_load(s, PROMPT_VERSION)

        run = s.scalars(select(m.PipelineRun).order_by(m.PipelineRun.id.desc())).first()
        assert run.status == "failed" and "nope" in run.error
        # Not `{}`: an operator must be able to see which stage died.
        assert set(run.stats) == {"refs", "articles", "classifications",
                              "paper_classifications", "costs"}
        assert "transform" not in run.stats
        s.close()

    def test_rebuild_and_load_are_distinguishable_in_the_history(self):
        s = self._loaded()
        cmd_load(s, PROMPT_VERSION, kind="rebuild")
        kinds = [r.kind for r in s.scalars(select(m.PipelineRun).order_by(m.PipelineRun.id))]
        assert kinds == ["load", "rebuild"]
        s.close()


def session_counts(s) -> dict:
    """Row counts for the tables a failed load would have wiped."""
    return {
        "articles": s.scalar(select(func.count()).select_from(m.Article)),
        "classifications": s.scalar(select(func.count()).select_from(m.Classification)),
        "connections": s.scalar(select(func.count()).select_from(m.Connection)),
        "holdings": s.scalar(select(func.count()).select_from(m.Holding)),
    }
