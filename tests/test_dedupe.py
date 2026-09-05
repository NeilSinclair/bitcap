"""Near-duplicate collapse: the gates, the anchor, and the cache that keeps it free.

The silent failures these catch:

* **A merge that deletes a claim.** The whole risk of this feature. Two articles
  about GPT-6 Astra are not the same article if one of them reports a crossed
  Preparedness threshold the other never mentions. If gate 2 stops separating
  event types, the product quietly loses findings and every surface still looks
  healthy.
* **The subject gate drifting loose.** Identifier extraction over article bodies
  over-generates — the Astra launch post names `gemini-3.8` and `opus-5` in a
  comparison table. If subject extraction starts reading bodies again, unrelated
  launches from one lab join up and the merge looks confident while being wrong.
* **One model becoming two keys.** "GPT-6 Astra" and "GPT-6-Astra" extract
  differently, and those two rows are the clearest true duplicate in the corpus.
  A regression here shows up as *fewer* merges, which looks like caution rather
  than a bug.
* **The anchor pointing at the wrong member.** Silent by construction: the group
  is right, the collapse count is right, and the feed simply shows a
  documentation page where the launch announcement should be.
* **The embedding cache going stale or thrashing.** Stale means a rewritten
  summary is compared as its old self; thrashing means every run re-buys the
  whole corpus. Both are invisible without a test — one costs correctness, the
  other costs money, and neither raises anything.
"""

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models as m
from app.db import create_all
from app.pipeline import dedupe

ROOT = Path(__file__).parent.parent
LABELS = ROOT / "research" / "docs" / "dedupe_labels.json"
FEATURES = ROOT / "research" / "docs" / "dedupe_features.json"


def article(article_id, title, lab="openai", day=1, score=0.0, ai_score=0.0, repo=None):
    """Build the row shape the grouping functions consume."""
    return {
        "id": article_id, "title": title, "lab": lab,
        "published_on": date(2026, 9, day), "score": score,
        "ai_score": ai_score, "repo": repo,
        "url": f"https://example.test/{article_id}",
    }


class TestSubjectGate:
    """Gate 1 — which thing an article is about."""

    def test_spaced_and_hyphenated_spellings_reach_a_shared_key(self):
        """The launch post and its forum restatement must intersect.

        "GPT-6 Astra: A new generation of intelligence" and "Introducing
        GPT-6-Astra" are the clearest true duplicate in the live corpus. The
        identifier pattern only absorbs a suffix across a hyphen, so the first
        yields `gpt-6` and the second `gpt-6-astra`; without the stem they never
        match and the feature misses the case it was built for.
        """
        spaced = dedupe.subjects("GPT-6 Astra: A new generation of intelligence")
        hyphenated = dedupe.subjects("Introducing GPT-6-Astra: The most intelligent model")
        assert spaced & hyphenated

    def test_body_references_are_not_subjects(self):
        """A title names the subject; a comparison table names rivals.

        The real Astra launch post is 24,000 characters and its body yields
        `gemini-3.8`, `opus-5`, `gpt-5.6` and `gpt-5.6-sol`. Pairing on those
        would join the Astra launch to the GPT-5.6 launch — one lab, inside the
        window, two entirely different events.
        """
        found = dedupe.subjects("GPT-6 Astra: A new generation of intelligence")
        assert not {"gemini-3.8", "opus-5", "gpt-5.6-sol"} & found

    def test_a_name_without_a_version_number_yields_nothing(self):
        """`config/entities.yaml` records this gap; gate 1b covers it.

        "Path to Astra" is the live instance — a precursor published before the
        version number existed. Returning nothing is correct behaviour, and the
        test exists so that stays deliberate rather than looking like a bug to
        a future reader.
        """
        assert dedupe.subjects("Path to Astra: critical capabilities") == set()

    @pytest.mark.parametrize(
        "identifier,expected",
        [
            ("gpt-6-astra", "gpt-6"),
            ("gpt-6", "gpt-6"),
            ("opus-4.8", "opus-4.8"),
            ("deepseek-r1-0528", "deepseek-r1"),
            ("gemini-3.5-flash-cyber", "gemini-3.5"),
        ],
    )
    def test_stem_keeps_the_version_and_drops_the_variant(self, identifier, expected):
        assert dedupe.stem(identifier) == expected


class TestCandidatePairs:
    """The space gate 1b searches."""

    def test_different_labs_never_pair(self):
        """Two labs announcing in one week are two events, by construction."""
        rows = [article(1, "A", lab="openai"), article(2, "B", lab="anthropic")]
        assert dedupe.candidate_pairs(rows, 14) == []

    def test_the_window_is_inclusive_at_its_edge(self):
        rows = [article(1, "A", day=1), article(2, "B", day=15)]
        assert dedupe.candidate_pairs(rows, 14) == [(0, 1)]

    def test_beyond_the_window_does_not_pair(self):
        rows = [article(1, "A", day=1), article(2, "B", day=16)]
        assert dedupe.candidate_pairs(rows, 14) == []

    def test_every_pair_in_a_burst_is_offered(self):
        """Three same-day items from one lab make three pairs, not two."""
        rows = [article(i, f"T{i}", day=3) for i in (1, 2, 3)]
        assert len(dedupe.candidate_pairs(rows, 14)) == 3


class TestAnchor:
    """Which member represents a group."""

    def test_score_beats_recency(self):
        """The Astra shape: a launch and a docs page two days apart."""
        launch = article(7610, "GPT-6 Astra: A new generation", day=3, score=100.0)
        docs = article(7655, "GPT-6 Astra", day=5, score=100.0)
        low = article(7601, "Legora reviewed 41 documents", day=3, score=0.0)
        assert dedupe.anchor_of([low, docs, launch], "score")["id"] == 7610

    def test_an_article_cluster_breaks_a_tie_towards_the_earliest(self):
        """Being early is the product's claim, so a tie goes to first."""
        launch = article(7610, "launch", day=3, score=100.0)
        docs = article(7655, "docs", day=5, score=100.0)
        assert dedupe.anchor_of([docs, launch], "score")["id"] == 7610

    def test_a_release_train_breaks_a_tie_towards_the_latest(self):
        """A repo's card should name the version it is on, not the one it left.

        Within a train every member usually scores the same, so this tie-break
        decides every release group in the corpus.
        """
        old = article(1, "claude-code v2.1.257", day=1, ai_score=66.7)
        new = article(2, "claude-code v2.1.259", day=2, ai_score=66.7)
        chosen = dedupe.anchor_of([old, new], "ai_score", prefer="latest")
        assert chosen["id"] == 2

    def test_a_higher_scoring_older_release_still_wins(self):
        """Score first, always — the tie-break is only ever a tie-break.

        Live case: `claude-code` v2.1.259 scores 66.7 and v2.1.260 scores 22.2,
        so the substantive release anchors and the trivial newer one folds.
        """
        substantive = article(1, "v2.1.259", day=2, ai_score=66.7)
        trivial = article(2, "v2.1.260", day=3, ai_score=22.2)
        assert dedupe.anchor_of([substantive, trivial], "ai_score", prefer="latest")["id"] == 1

    def test_an_unknown_preference_is_refused(self):
        """Defaulting here would anchor a whole surface the wrong way round."""
        with pytest.raises(ValueError):
            dedupe.anchor_of([article(1, "A")], "score", prefer="newest")


class TestReleaseTrains:
    """380 of 647 articles, collapsed without a similarity check."""

    def test_consecutive_releases_from_one_repo_become_one_group(self):
        rows = [article(i, f"v{i}", day=i, repo="anthropics/claude-code") for i in (1, 2, 3)]
        assert [len(g) for g in dedupe.release_trains(rows, 48)] == [3]

    def test_different_repos_never_share_a_group(self):
        rows = [
            article(1, "a", day=1, repo="anthropics/claude-code"),
            article(2, "b", day=1, repo="openai/codex"),
        ]
        assert [len(g) for g in dedupe.release_trains(rows, 48)] == [1, 1]

    def test_a_gap_wider_than_the_window_starts_a_new_train(self):
        rows = [
            article(1, "a", day=1, repo="r"),
            article(2, "b", day=2, repo="r"),
            article(3, "c", day=20, repo="r"),
        ]
        assert [len(g) for g in dedupe.release_trains(rows, 48)] == [2, 1]

    def test_a_train_chains_past_the_window_length(self):
        """`deepseek-harness` shipped four alphas across five days.

        A fixed 48-hour bucket would cut that into two or three cards for one
        burst of work. Chaining on the gap between consecutive releases is what
        keeps it one, and this is the case that distinguishes the two designs.
        """
        rows = [article(i, f"v{i}", day=i, repo="deepseek-ai/deepseek-harness")
                for i in (1, 2, 3, 4, 5)]
        assert [len(g) for g in dedupe.release_trains(rows, 48)] == [5]


class TestEmbeddingCache:
    """What keeps gate 1b free on a re-run, and honest after a re-classification."""

    @pytest.fixture()
    def session(self):
        engine = create_engine("sqlite://")
        create_all(engine)
        with Session(engine) as session:
            yield session

    def _article(self, session, url, title, summary):
        if not session.get(m.RefLab, "openai"):
            session.add(m.RefLab(id="openai", label="OpenAI", config_version=1))
            session.flush()
        raw = m.RawArticle(url=url, payload={}, content_hash="h", source_file="f")
        session.add(raw)
        session.flush()
        row = m.Article(
            url=url, lab="openai", title=title, published_on=date(2026, 9, 3),
            raw_article_id=raw.id, text="body", text_source="full_text",
        )
        session.add(row)
        session.flush()
        session.add(m.Classification(
            article_id=row.id, prompt_version="v9", scoring_version=4,
            event_type="frontier_model_release", summary=summary,
            is_signal=True, notable=False, notable_reason="", dropped_tags=[],
            score=100.0, band="high", ai_score=100.0, ai_band="high",
        ))
        session.commit()
        return row

    def test_an_uncached_article_is_pending(self, session):
        self._article(session, "u1", "GPT-6 Astra", "OpenAI announced GPT-6 Astra.")
        assert [u for u, _ in dedupe.pending(session, "v9")] == ["u1"]

    def test_a_cached_article_is_not_pending(self, session):
        self._article(session, "u1", "GPT-6 Astra", "OpenAI announced GPT-6 Astra.")
        text = dedupe.embedded_text("GPT-6 Astra", "OpenAI announced GPT-6 Astra.")
        session.add(m.RawArticleEmbedding(
            url="u1", content_hash=dedupe.text_hash(text), model="m", dim=3,
            vector=dedupe.pack([1.0, 0.0, 0.0]),
        ))
        session.commit()
        assert dedupe.pending(session, "v9") == []

    def test_a_rewritten_summary_makes_it_pending_again(self, session):
        """A re-classification must re-embed, or the vector is of the old text.

        This is the stale half of the cache's failure mode, and it is silent:
        the comparison still runs, still returns a number, and the number
        describes text the article no longer contains.
        """
        row = self._article(session, "u1", "GPT-6 Astra", "First summary.")
        stale = dedupe.embedded_text("GPT-6 Astra", "First summary.")
        session.add(m.RawArticleEmbedding(
            url="u1", content_hash=dedupe.text_hash(stale), model="m", dim=3,
            vector=dedupe.pack([1.0, 0.0, 0.0]),
        ))
        session.commit()

        classification = session.scalar(
            select(m.Classification).where(m.Classification.article_id == row.id)
        )
        classification.summary = "A materially different summary."
        session.commit()

        assert [u for u, _ in dedupe.pending(session, "v9")] == ["u1"]

    def test_an_untitled_unsummarised_article_is_skipped(self, session):
        """Embedding an empty string buys a vector that means nothing."""
        self._article(session, "u1", "", "")
        assert dedupe.pending(session, "v9") == []

    def test_vectors_survive_a_round_trip(self):
        vector = [0.5, -0.25, 0.125, 1.0]
        assert dedupe.unpack(dedupe.pack(vector)).tolist() == vector

    def test_the_matrix_is_l2_normalised(self, session):
        """Cosine is then one matmul rather than a divide per pair."""
        session.add(m.RawArticleEmbedding(
            url="u1", content_hash="h", model="m", dim=3,
            vector=dedupe.pack([3.0, 4.0, 0.0]),
        ))
        session.commit()
        _, block = dedupe.matrix(session, ["u1"])
        assert float((block[0] ** 2).sum()) == pytest.approx(1.0)

    def test_mixed_vector_widths_are_refused(self, session):
        """A model swap under a populated cache makes every comparison nonsense.

        Failing loudly matters more here than elsewhere: ragged vectors would
        otherwise be silently truncated or broadcast into a similarity number
        that looks entirely plausible.
        """
        session.add(m.RawArticleEmbedding(
            url="u1", content_hash="h", model="small", dim=3,
            vector=dedupe.pack([1.0, 0.0, 0.0]),
        ))
        session.add(m.RawArticleEmbedding(
            url="u2", content_hash="h", model="large", dim=4,
            vector=dedupe.pack([1.0, 0.0, 0.0, 0.0]),
        ))
        session.commit()
        with pytest.raises(ValueError, match="mixed widths"):
            dedupe.matrix(session, ["u1", "u2"])

    def test_a_zero_vector_does_not_divide_by_zero(self, session):
        session.add(m.RawArticleEmbedding(
            url="u1", content_hash="h", model="m", dim=3,
            vector=dedupe.pack([0.0, 0.0, 0.0]),
        ))
        session.commit()
        _, block = dedupe.matrix(session, ["u1"])
        assert float(block[0].sum()) == 0.0


class TestThresholdsAgainstTheLabelledSet:
    """The guard against this silently degrading.

    Every other test here fixes a behaviour. These fix the *quality* of the
    decision against the labelled pairs, which is the only thing that would
    catch the collapse getting quietly worse — a changed embedding model, a
    reworded summary upstream, an edited threshold. All of those leave the code
    passing and the numbers moving.

    The floors are the measured values rather than round numbers: the point is
    to fail on any movement and make someone look, not to leave slack a real
    regression can hide inside.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def labelled():
        if not (LABELS.exists() and FEATURES.exists()):
            pytest.skip("labelled set not built; run research/dedupe/candidates.py")
        labels = {r["pair"]: r for r in json.loads(LABELS.read_text(encoding="utf-8"))}
        features = json.loads(FEATURES.read_text(encoding="utf-8"))
        return features, labels

    def _pool(self, features, labels):
        """Pairs that actually reach a threshold: post exact pass, post gate 2."""
        exact = {
            f["pair"] for f in features
            if not f["same_event_type"] and labels[f["pair"]]["label"] == "same"
        }
        return [f for f in features if f["pair"] not in exact and f["same_event_type"]]

    def test_every_labelled_pair_has_features(self, labelled):
        """A stale pairing on a fresh set would silently shrink the evidence."""
        features, labels = labelled
        assert {f["pair"] for f in features} <= set(labels)

    def test_nothing_above_the_high_threshold_is_a_false_merge(self, labelled):
        """`cosine_high` exists to be safe to merge on without asking.

        If this fails, the auto-merge path has started deleting claims, and it
        does so invisibly — the feed simply shows fewer rows.
        """
        features, labels = labelled
        high = float(dedupe.settings()["thresholds"]["cosine_high"])
        wrong = [
            f["pair"] for f in self._pool(features, labels)
            if f["cosine"] >= high and labels[f["pair"]]["label"] == "different"
        ]
        assert wrong == [], f"auto-merge would wrongly merge {wrong}"

    def test_nothing_below_the_low_threshold_is_a_missed_duplicate(self, labelled):
        """`cosine_low` exists to be safe to separate on without asking."""
        features, labels = labelled
        low = float(dedupe.settings()["thresholds"]["cosine_low"])
        missed = [
            f["pair"] for f in self._pool(features, labels)
            if f["cosine"] < low and labels[f["pair"]]["label"] == "same"
        ]
        assert missed == [], f"auto-separate would miss {missed}"

    def test_the_adjudication_band_stays_small(self, labelled):
        """The band is the only part that costs money per pair.

        A band that quietly widens turns a 12-call phase into a per-run bill
        nobody decided to accept. Failing here is a prompt to re-calibrate,
        not necessarily a bug.
        """
        features, labels = labelled
        config = dedupe.settings()["thresholds"]
        high, low = float(config["cosine_high"]), float(config["cosine_low"])
        band = [f for f in self._pool(features, labels) if low <= f["cosine"] < high]
        assert len(band) <= 20, f"{len(band)} pairs would be adjudicated per full run"

    def test_the_positive_count_that_the_caveat_rests_on(self, labelled):
        """Guards a claim the design doc makes, not the code.

        The thresholds rest on a handful of positives. If the labelled set grows
        or shrinks, the "indicative, not precise" caveat in config/dedupe.yaml
        and docs/decisions.md needs rewriting with it — this is what says so.
        """
        features, labels = labelled
        positives = [f for f in self._pool(features, labels)
                     if labels[f["pair"]]["label"] == "same"]
        assert len(positives) == 5, (
            f"{len(positives)} positives now, not 5 — re-run "
            "research/dedupe/calibrate.py and update the caveat"
        )


class TestExactPass:
    """Gate 0 — and why it has to run before the event-type gate."""

    def test_one_article_at_two_urls_is_matched(self):
        rows = [
            {"id": 1, "lab": "openai", "published_on": date(2026, 8, 10), "repo": None,
             "title": "Expanding Daybreak as the Cyber Defense Window Narrows"},
            {"id": 2, "lab": "openai", "published_on": date(2026, 8, 10), "repo": None,
             "title": "Expanding Daybreak as the Cyber Defense Window Narrows!"},
        ]
        assert len(dedupe.exact_groups(rows)) == 2

    def test_the_same_title_on_a_different_day_is_not_matched(self):
        """A recurring column is not a duplicate of last week's edition."""
        rows = [
            {"id": 1, "lab": "openai", "published_on": date(2026, 8, 10),
             "title": "Weekly", "repo": None},
            {"id": 2, "lab": "openai", "published_on": date(2026, 8, 17),
             "title": "Weekly", "repo": None},
        ]
        assert dedupe.exact_groups(rows) == {}

    def test_punctuation_and_case_do_not_separate_two_copies(self):
        assert (dedupe.normalised_title("Introducing GPT-6-Astra: The Model")
                == dedupe.normalised_title("introducing gpt 6 astra   the model"))


class TestUnion:
    """Merges from different gates have to compose."""

    def test_a_transitive_chain_forms_one_group(self):
        """Exact joins A-B while the gated pass joins B-C: one group of three.

        Handled with lists of pairs instead, this returns two overlapping groups
        and an article lands in both — which the unique constraint on
        `article_groups.article_id` then rejects at write time, turning a logic
        bug into a run failure.
        """
        union = dedupe._Union()
        union.union(1, 2)
        union.union(2, 3)
        assert union.find(1) == union.find(3)

    def test_unrelated_articles_stay_apart(self):
        union = dedupe._Union()
        union.union(1, 2)
        assert union.find(1) != union.find(3)


class TestEvidence:
    """What a group is labelled with, and why a reason is never empty."""

    def test_a_singleton_says_it_was_considered(self):
        """"Considered and left alone" must not read as "never looked at"."""
        method, reason = dedupe._evidence([{"id": 1}], {})
        assert method == "singleton" and reason

    def test_the_most_trustworthy_gate_labels_the_group(self):
        """A fact outranks an inference when both contributed."""
        reasons = {(1, 2): ("embedding", "cosine 0.9"), (2, 3): ("exact", "same title")}
        method, reason = dedupe._evidence([{"id": 1}, {"id": 2}, {"id": 3}], reasons)
        assert method == "exact" and reason == "same title"
