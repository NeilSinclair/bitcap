"""The posts corpus inside the shared spine.

The silent failures this suite exists to catch:

  - `classify_new(corpus="posts")` falling through to the announcement prompt.
    Until this leg landed that dispatch was a ternary reading `== "papers"`, so
    any other corpus name was scored under v9 -- silently, at full price, and
    with a result that looks entirely plausible.
  - `connect()` deleting the whole connections table and being called once per
    version, leaving only the last version's rows.
  - A third prompt version reaching the digest or raising content alerts merely
    because it exists in `classifications`. `high_band_items` has no version
    filter of its own, so a new corpus starts paging people the moment it loads.
  - The dashboard's doc-type filter offering "Posts" and returning something
    else, or returning posts under a label that is not theirs.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as m
from app.cli import (DIGEST_VERSIONS, PAPER_PROMPT_VERSION, POST_PROMPT_VERSION,
                     PROMPT_VERSION, PROMPT_VERSIONS)
from app.pipeline import alerts
from app.pipeline.registry import (CORPUS_LABELS, LEGS, PAPERS_CORPUS, POSTS,
                                   POSTS_CORPUS, load_sources)

ROOT = Path(__file__).parent.parent


@pytest.fixture()
def session():
    from app.db import create_all
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


class TestThePostsCorpusIsScoredByItsOwnPrompt:
    """A corpus scored under the wrong prompt produces plausible nonsense."""

    def test_the_posts_corpus_selects_the_t1_variant(self):
        import sys
        sys.path.insert(0, str(ROOT / "research" / "announcements"))
        import score_announcements as sa

        variant = sa.posts()
        assert variant.version == POST_PROMPT_VERSION
        assert variant.prompt.name == f"{POST_PROMPT_VERSION}.md"
        assert variant.prompt.parent.name == "post_scoring"

    def test_an_unknown_corpus_name_is_refused_rather_than_defaulted(self):
        """The regression: anything not "papers" silently became announcements."""
        from app.pipeline import classify
        with pytest.raises(ValueError, match="unknown corpus"):
            classify.classify_new(None, "t1", corpus="tweets")

    def test_each_corpus_has_its_own_version(self):
        assert len({PROMPT_VERSION, PAPER_PROMPT_VERSION, POST_PROMPT_VERSION}) == 3

    def test_the_prompt_version_the_scorer_reads_matches_the_cli(self):
        # These are two constants in two files that must agree; the scorer
        # scrapes app/cli.py rather than restating it, and this pins that.
        import sys
        sys.path.insert(0, str(ROOT / "research" / "announcements"))
        import score_announcements as sa
        assert sa.POST_PROMPT_VERSION == POST_PROMPT_VERSION


class TestT1IsPromptedLikeP1WhereTheQuestionIsTheSame:
    """Three corpora, one question wherever the question can be one."""

    def test_the_shared_sections_are_byte_identical_to_the_paper_prompt(self):
        p1 = (ROOT / "prompts" / "paper_scoring" / "p1.md").read_text()
        t1 = (ROOT / "prompts" / "post_scoring" / "t1.md").read_text()
        for section in ("## Mechanisms", "## Categories", "## Practices",
                        "## Event type"):
            a, b = p1[p1.index(section):], t1[t1.index(section):]
            end_a = a.index("\n## ", 5)
            end_b = b.index("\n## ", 5)
            assert a[:end_a] == b[:end_b], f"{section} drifted between p1 and t1"

    def test_the_output_schema_section_is_identical(self):
        p1 = (ROOT / "prompts" / "paper_scoring" / "p1.md").read_text()
        t1 = (ROOT / "prompts" / "post_scoring" / "t1.md").read_text()
        assert p1[p1.index("## Output"):] == t1[t1.index("## Output"):]

    def test_the_prompt_declares_the_text_source_the_corpus_emits(self):
        t1 = (ROOT / "prompts" / "post_scoring" / "t1.md").read_text()
        assert "`x_post`" in t1
        corpus = json.loads((ROOT / "research" / "docs" / "posts_corpus.json").read_text())
        assert {r["text_source"] for r in corpus} == {"x_post"}

    def test_the_confidence_ceiling_is_stated(self):
        # Score scales on magnitude x confidence, so this sentence is what
        # stops enthusiasm reaching the high band.
        t1 = (ROOT / "prompts" / "post_scoring" / "t1.md").read_text()
        assert "Cap `confidence` at `medium`" in t1

    def test_the_prompt_introduces_no_event_type_the_scoring_config_lacks(self):
        # `app/scoring.py` does `.get(event_type, 0)`, so an event type present
        # in the prompt and absent from config scores zero with no error.
        rules = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())
        p1 = (ROOT / "prompts" / "paper_scoring" / "p1.md").read_text()
        t1 = (ROOT / "prompts" / "post_scoring" / "t1.md").read_text()
        assert p1[p1.index("## Event type"):p1.index("## Output")] == \
               t1[t1.index("## Event type"):t1.index("## Output")]
        for event in rules["event_weight"]:
            if event != "other":
                assert event in t1


class TestPostsAreSeenButNotPushed:
    """Scored and browsable, deliberately not in the digest or the alerts."""

    def test_the_digest_reads_fewer_versions_than_the_dashboard(self):
        assert POST_PROMPT_VERSION in PROMPT_VERSIONS
        assert POST_PROMPT_VERSION not in DIGEST_VERSIONS
        assert set(DIGEST_VERSIONS) < set(PROMPT_VERSIONS)

    def test_content_alerts_are_muted_for_posts_in_the_committed_config(self):
        config = yaml.safe_load((ROOT / "config" / "pipeline.yaml").read_text())
        assert POST_PROMPT_VERSION in config["alerts"]["content_mute_prompt_versions"]

    def test_a_muted_version_raises_no_content_alert(self, session):
        """The rule reads `classifications` with no version filter of its own."""
        session.add(m.RefLab(id="openai", label="OpenAI", config_version=1))
        raw = m.RawArticle(url="https://x.com/sama/status/1", payload={},
                           content_hash="h", source_file=POSTS_CORPUS)
        session.add(raw)
        session.flush()
        article = m.Article(url=raw.url, raw_article_id=raw.id, lab="openai",
                            title="t", published_on=date.today(),
                            text="x", text_source="x_post")
        session.add(article)
        session.flush()
        session.add(m.Classification(
            article_id=article.id, prompt_version=POST_PROMPT_VERSION,
            scoring_version=4, event_type="frontier_model_release", summary="s",
            notable=False, notable_reason="", dropped_tags=[],
            score=100.0, band="high", ai_score=0.0, ai_band="none"))
        session.commit()

        muted = alerts.high_band_items(
            session, {"content_band": "high",
                      "content_mute_prompt_versions": [POST_PROMPT_VERSION]}, {})
        assert muted == []

        # And the mute is the only thing suppressing it: without it, it fires.
        unmuted = alerts.high_band_items(session, {"content_band": "high"}, {})
        assert len(unmuted) == 1


class TestTheLegIsRegisteredEverywhereItMustBe:
    """A leg half-registered fails in a different place each firing."""

    def test_posts_is_a_leg_with_a_corpus_label_and_a_loader(self):
        assert POSTS in LEGS
        assert CORPUS_LABELS[POSTS] == POSTS_CORPUS
        assert [s for s in load_sources(legs=(POSTS,))]

    def test_the_posts_leg_is_one_source_not_one_per_lab(self):
        # One API behind one credential fails as a unit; 27 identical rows
        # would be noise rather than diagnosis.
        assert len(load_sources(legs=(POSTS,))) == 1

    def test_the_corpus_label_is_the_committed_file(self):
        assert (ROOT / POSTS_CORPUS).exists()

    def test_posts_have_their_own_adapter(self):
        from app.pipeline.adapters import ADAPTERS, adapter_for
        assert "posts" in ADAPTERS
        source = load_sources(legs=(POSTS,))[0]
        assert adapter_for(source) is ADAPTERS["posts"]

    def test_the_dashboard_labels_posts_as_their_own_doc_type(self):
        from api.queries import _DOC_TYPES
        assert _DOC_TYPES[POSTS_CORPUS] == "post"
        assert _DOC_TYPES[PAPERS_CORPUS] == "paper"
        page = (ROOT / "frontend" / "app" / "page.js").read_text()
        # The filter must offer exactly the values the API can emit.
        for value in set(_DOC_TYPES.values()) | {"announcement"}:
            assert f'value="{value}"' in page, f"dashboard cannot filter {value}"
