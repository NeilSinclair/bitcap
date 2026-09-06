"""End-to-end: the full pipeline against the real committed artifacts.

This is the reconciliation suite. The recomputed investment score must equal
the file register for every article — if the DB and the register ever say
different numbers about the same article, one of them is lying and this test
says so before anything downstream trusts either.
"""

import json
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import models as m
from app.cli import (PAPER_PROMPT_VERSION, POST_PROMPT_VERSION, PROMPT_VERSION,
                     cmd_connect, cmd_load)
from app.db import create_all

ROOT = Path(__file__).parent.parent
REGISTER = ROOT / "research" / "docs" / f"scored_announcements_{PROMPT_VERSION}.json"
PAPER_REGISTER = ROOT / "research" / "docs" / f"scored_papers_{PAPER_PROMPT_VERSION}.json"
POST_REGISTER = ROOT / "research" / "docs" / f"scored_posts_{POST_PROMPT_VERSION}.json"
CORPUS = ROOT / "research" / "docs" / "announcements.json"
PAPERS_CORPUS_FILE = ROOT / "research" / "docs" / "papers_corpus.json"
POSTS_CORPUS_FILE = ROOT / "research" / "docs" / "posts_corpus.json"


def paper_register_rows() -> list[dict]:
    """The scored papers register. Empty if the leg has not been run here."""
    if not PAPER_REGISTER.exists():
        return []
    return json.loads(PAPER_REGISTER.read_text()).get("scored", [])


def post_register_rows() -> list[dict]:
    """The scored posts register. Empty if the leg has not been run here."""
    if not POST_REGISTER.exists():
        return []
    return json.loads(POST_REGISTER.read_text()).get("scored", [])


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

    All three committed corpora, since `rebuild` loads all three: announcements,
    the papers corpus and the posts corpus land in the same `articles` table
    under different `source_file` values.
    """
    return (len(json.loads(CORPUS.read_text()))
            + len(json.loads(PAPERS_CORPUS_FILE.read_text()))
            + len(json.loads(POSTS_CORPUS_FILE.read_text())))


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
    # Both registers: the tag tables are shared, so summing only the
    # announcement register would report every paper's tags as unexpected.
    register = [r for r in register_rows() + paper_register_rows() + post_register_rows()
                if r["url"] in loaded_urls]
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
        assert set(run.stats) == {"refs", "articles", "paper_corpus", "posts_corpus",
                              "classifications", "paper_classifications",
                              "post_classifications", "costs", "repo_verdicts"}
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


class TestARebuiltDatabaseHasItsGrouping:
    """`bitcap-db rebuild` must leave the feed grouped, not flat. D74.

    THE FAILURE THIS REPRODUCES, observed on the live database. A `bitcap-db
    load` at 14:01 left `article_groups` and `article_links` at zero rows, and
    every surface silently lost its grouping: no "5 more on this" on the
    dashboard, no fold badge in the digest, no related-document pills anywhere.
    Nothing errored and no alert fired, because from the pipeline's point of view
    nothing had gone wrong.

    `transform` deletes and re-inserts `articles`, which reassigns their primary
    keys, and both grouping tables hang off `articles.id`. `cmd_load` chains
    `connect` for exactly this reason — its own docstring says a load stopping
    before the join "would leave the table empty and looking like a finding" —
    and grouping was never added to the same chain.

    Why this is the README's problem and not only an operator's: `bitcap-db
    rebuild` is `drop_all` + `ensure_schema` + this same `cmd_load`, and
    README.md makes it the first command a new reader runs. Without this they
    clone the repo, follow the instructions, open the dashboard, and see an
    ungrouped feed with no way to tell it is not the finished product.

    WHAT A REBUILT DATABASE STILL DOES NOT HAVE, stated because the fix looks
    more complete than it is. Two of the four grouping passes need data no
    committed artifact carries:

    * **The cosine gate is a no-op.** `raw_article_embeddings` is an ops table
      and survives a rebuild, but a *fresh* database has none, so `coverage` is
      0.0 and every similarity pair is skipped. Only the exact pass and the
      release trains fire — on this corpus, one collapse rather than the 57 the
      live database carries.
    * **Pairing has nothing to pair.** `article_links` needs a GitHub release
      naming a model an announcement also names, and releases are live-fetched
      bronze with no committed artifact. The corpus here holds none, so the
      correct result is zero links, and asserting otherwise would be asserting
      the fixture rather than the behaviour.

    Both resolve on the first worker firing. The claim being made here is
    narrower and is the one that broke: a load leaves the grouping tables
    populated and consistent, rather than empty.
    """

    def _dedupe_stats(self, session):
        run = session.scalars(
            select(m.PipelineRun).order_by(m.PipelineRun.id)).first()
        return (run.stats or {}).get("dedupe") or {}

    def test_a_load_leaves_the_announcement_corpus_grouped(self, session):
        """The regression itself: zero rows here is the whole bug."""
        groups = session.scalar(select(func.count()).select_from(m.ArticleGroup))
        assert groups > 0, (
            "cmd_load left article_groups empty; the feed renders flat and "
            "`bitcap-db rebuild` ships a database that looks unfinished")

        # One row per classified announcement — a singleton is a group of one,
        # so coverage is total by construction and a short count would mean
        # grouping ran against a stale article set.
        #
        # Announcements only, matching the worker: `assign` takes one prompt
        # version and papers and posts are not grouped by either path. The
        # read side falls back to a per-article singleton for them, so they
        # render correctly ungrouped rather than not at all.
        announcements = session.scalar(
            select(func.count()).select_from(m.Classification)
            .where(m.Classification.prompt_version == PROMPT_VERSION))
        assert groups == announcements

    def test_the_deterministic_passes_actually_collapsed_something(self, session):
        """Non-empty is not enough: 292 groups of one is also "grouped".

        One collapse, from the exact pass — the same article reaching us at two
        URLs. That is all this corpus can produce without an embedding cache,
        and it is enough to prove the pass ran rather than merely wrote a row
        per article.
        """
        sizes = [g.group_size for g in session.scalars(select(m.ArticleGroup))]
        assert max(sizes) > 1, "every article is its own group; nothing folded"
        assert self._dedupe_stats(session)["collapsed"] >= 1

    def test_every_folded_group_says_why_it_merged(self, session):
        """A fold the reader cannot interrogate is one they must take on trust,
        and both surfaces render this string."""
        folded = session.scalars(
            select(m.ArticleGroup).where(m.ArticleGroup.group_size > 1)).all()
        assert folded
        assert all(g.reason for g in folded)
        assert all(g.method in ("exact", "release_train", "llm", "embedding")
                   for g in folded)

    def test_exactly_one_anchor_per_group(self, session):
        """Two anchors renders one event twice; none renders it not at all."""
        anchors = Counter(
            g.group_id for g in session.scalars(
                select(m.ArticleGroup).where(m.ArticleGroup.is_anchor)))
        every = {g.group_id for g in session.scalars(select(m.ArticleGroup))}
        assert set(anchors) == every
        assert set(anchors.values()) == {1}

    def test_the_pairing_pass_runs_even_where_it_finds_nothing(self, session):
        """`article_links` was equally empty and is rebuilt by the same call.

        Asserted on the pass having *run*, not on it having found something.
        Links need a release naming a model an announcement also names, and this
        corpus carries no releases at all — so zero is the correct answer here
        and a non-empty assertion would be testing the fixture. What must not
        happen is the pass silently ceasing to be called, which is exactly what
        the missing key would say.
        """
        stats = self._dedupe_stats(session)
        assert "links" in stats and "paired" in stats

    def test_grouping_costs_nothing_so_rebuild_stays_keyless(self, session):
        """The whole reason this can live in `cmd_load` at all.

        A rebuild is documented as reproducing the database from committed
        artifacts with no API key. If the load's grouping could call a provider,
        that claim would be false and `bitcap-db rebuild` would fail — or worse,
        quietly bill — on a fresh clone. The adjudicator is the only paid call in
        the path, and `budget=None` means *unlimited* to it rather than *do not
        call*, so the flag is the only thing standing between a rebuild and a
        bill.
        """
        import inspect

        import app.cli as cli

        source = inspect.getsource(cli.cmd_load)
        assert "adjudicate_pairs=False" in source, (
            "cmd_load may not let the dedupe phase reach a paid adjudication")
        assert self._dedupe_stats(session)["usd"] == 0.0
        assert self._dedupe_stats(session)["adjudicated"] == 0
