"""Transform: clean rows derive correctly and re-runs converge.

The silent failure this catches: the DB's recomputed score drifting from the
rule the register was scored under, or a rebuild that duplicates tag rows.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine, delete, func, select
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
    assert counts == {"articles": 1, "classifications": 1, "tags": 2,
                      "no_classification": 0, "off_topic": 0}
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


class TestTheRelevanceGate:
    """Derivation is gated on the repository relevance verdicts.

    This is the half of the filter that acts on releases *already* in bronze.
    The adapter's gate only stops future fetches, and `transform` re-derives
    silver from the whole of bronze on every firing — so without this, the
    off-topic releases already in the corpus stay on the dashboard for ever and
    a one-off DELETE would be undone by the next run.

    The silent failures: a gate that reads through to a provider (this function
    runs over the whole corpus, every firing), a gate that catches announcements
    as well as releases, and a verdict flip that does not converge — which would
    make the filter one-way and any mistake permanent.
    """

    def _release(self, session, url, repo, org="google-deepmind"):
        session.add(m.RawArticle(
            url=url, content_hash="h", source_file="github_releases",
            payload={"lab": "openai", "title": f"{org}/{repo} v1", "date": "2026-09-01",
                     "text": "body", "text_source": "github_release",
                     "org": org, "repo": repo}))
        session.add(m.RawLlmResponse(url=url, prompt_version="v7", payload=RESULT))
        session.flush()

    def _version(self):
        """The live cache key, so switching `relevance.model` in config does not
        break these tests — the coupling being tested is transform-to-cache, not
        transform-to-a-particular-model."""
        import yaml

        from app.pipeline import repo_relevance
        from app.transform import REPO_SIGNALS

        config = yaml.safe_load(REPO_SIGNALS.read_text(encoding="utf-8"))["relevance"]
        return repo_relevance.cache_key(config)

    def _verdict(self, session, repo, relevant, org="google-deepmind", version=None):
        session.add(m.RawLlmResponse(
            url=f"repo:{org}/{repo}", prompt_version=version or self._version(),
            payload={"relevant": relevant, "reason": "physics simulator"}))
        session.flush()

    def _classified(self, session, url):
        art = session.scalar(select(m.Article).where(m.Article.url == url))
        if art is None:
            return None
        return session.scalar(
            select(m.Classification).where(m.Classification.article_id == art.id))

    def test_an_off_topic_release_is_never_derived_into_silver(self, session):
        self._release(session, "https://x/mujoco", "mujoco")
        self._verdict(session, "mujoco", relevant=False)
        session.commit()

        counts = transform(session, "v7")

        assert counts["off_topic"] == 1
        assert self._classified(session, "https://x/mujoco") is None

    def test_a_relevant_release_is_derived_normally(self, session):
        """The gate must be a cut, not a blanket. A verdict of `relevant` has to
        leave the row exactly as it was before the filter existed."""
        self._release(session, "https://x/gemma", "gemma")
        self._verdict(session, "gemma", relevant=True)
        session.commit()

        transform(session, "v7")

        assert self._classified(session, "https://x/gemma") is not None

    def test_an_announcement_is_never_gated(self, session):
        """Only the releases corpus is judged. An announcement carrying an
        `org`/`repo` in its payload must not be matched against repository
        verdicts — nothing else in the corpus has been judged at all."""
        session.add(m.RawArticle(
            url="https://x/post", content_hash="h", source_file="announcements.json",
            payload={"lab": "openai", "title": "t", "date": "2026-09-01",
                     "text": "body", "text_source": "full_text",
                     "org": "google-deepmind", "repo": "mujoco"}))
        session.add(m.RawLlmResponse(url="https://x/post", prompt_version="v7",
                                     payload=RESULT))
        self._verdict(session, "mujoco", relevant=False)
        session.commit()

        counts = transform(session, "v7")

        assert counts["off_topic"] == 0
        assert self._classified(session, "https://x/post") is not None

    def test_an_already_derived_release_is_removed_when_judged_off_topic(self, session):
        """The 33 off-topic releases already on the dashboard. They were derived
        before any verdict existed, so a forward-only gate would leave every one
        of them exactly where it is."""
        self._release(session, "https://x/torax", "torax")
        session.commit()
        transform(session, "v7")
        assert self._classified(session, "https://x/torax") is not None

        self._verdict(session, "torax", relevant=False)
        session.commit()
        transform(session, "v7")

        assert self._classified(session, "https://x/torax") is None

    def test_flipping_a_verdict_back_re_derives_the_rows(self, session):
        """Why derivation is gated rather than the rows deleted. Bronze is kept,
        so a wrong exclusion is undone by correcting the verdict — no refetch,
        no backfill, and the releases cursor never has to be rewound."""
        self._release(session, "https://x/faiss", "faiss", org="facebookresearch")
        self._verdict(session, "faiss", relevant=False, org="facebookresearch")
        session.commit()
        transform(session, "v7")
        assert self._classified(session, "https://x/faiss") is None

        session.execute(delete(m.RawLlmResponse).where(
            m.RawLlmResponse.url == "repo:facebookresearch/faiss"))
        self._verdict(session, "faiss", relevant=True, org="facebookresearch")
        session.commit()
        transform(session, "v7")

        assert self._classified(session, "https://x/faiss") is not None

    def test_a_verdict_from_another_model_is_not_read(self, session):
        """Verdicts are keyed on prompt version *and* model. A verdict bought
        from the bake-off's losing candidate must not decide what the configured
        one watches."""
        self._release(session, "https://x/mujoco", "mujoco")
        self._verdict(session, "mujoco", relevant=False,
                      version="r1:a-model-that-is-not-configured")
        session.commit()

        counts = transform(session, "v7")

        assert counts["off_topic"] == 0
        assert self._classified(session, "https://x/mujoco") is not None

    def test_the_gate_never_calls_a_provider(self, session, monkeypatch):
        """`transform` runs over the whole corpus on every firing. A gate that
        reads through to a model would turn the cheapest stage in the pipeline
        into the most expensive one, and nothing here would say so."""
        from app.pipeline import repo_relevance

        monkeypatch.setattr(repo_relevance, "judge", lambda *a, **k: pytest.fail(
            "transform asked for a verdict instead of reading the cache"))
        self._release(session, "https://x/mujoco", "mujoco")
        session.commit()

        transform(session, "v7")
