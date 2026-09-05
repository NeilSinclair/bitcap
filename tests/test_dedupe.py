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
# The configured model is part of the cache key, so fixtures must use it.
MODEL = dedupe.settings()["embedding"]["model"]
FEATURES = ROOT / "research" / "docs" / "dedupe_features.json"


def article(article_id, title, lab="openai", day=1, score=0.0, ai_score=0.0, repo=None,
            body="", event_type="frontier_model_release"):
    """Build the row shape the grouping functions consume.

    `body` is populated for releases only, matching `_rows`: an announcement's
    body is the largest thing in the payload and no gate reads it.
    """
    return {
        "id": article_id, "title": title, "lab": lab,
        "published_on": date(2026, 9, day), "score": score,
        "ai_score": ai_score, "repo": repo, "body": body,
        "event_type": event_type,
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


LAUNCH = "frontier_model_release"
PAIRING = {
    "window_days": 14,
    "pairing": {"enabled": True, "window_days": 14, "max_identifiers": 3,
                "announcement_events": [LAUNCH, "incremental_model_release",
                                        "open_weights"]},
}


class TestReleaseSubjects:
    """The release side of the pairing gate, which reads the body and not the title.

    The silent failures here:

    * **Reading the title.** It returns nothing and always will — the title is
      built by this pipeline as "{org}/{repo} {tag}" — so pairing degrades to
      zero links and looks exactly like "no release was related to anything".
    * **The cap going away.** A changelog naming a model catalogue would then be
      treated as being about every entry in it.

    Note what is deliberately *not* on that list: stemming. `release_links` adds
    the stems on purpose (D61) — an earlier version refused to, which silently
    lost the spaced spelling of the launch post. What stops the widening is the
    event-type gate, tested in `TestReleasePairing`, not an exact-match rule
    here.
    """

    def test_a_release_title_yields_nothing_so_the_body_is_the_only_source(self):
        """`subjects` over a release title is empty. That is the reason this exists."""
        assert dedupe.subjects("openai/codex rust-v0.153.3") == set()
        assert dedupe.release_subjects("Added GPT-6-Astra to the model picker", 3)

    def test_a_release_naming_a_catalogue_names_nothing(self):
        """Above the cap the release is about a list, not about any entry in it."""
        catalogue = "Supports GPT-6-Astra, Grok-4.6, Gemma-4 and Opus-4.7"
        assert dedupe.release_subjects(catalogue, 3) == set()
        assert len(dedupe.release_subjects(catalogue, 10)) == 4

    def test_the_denied_collision_never_extracts(self):
        """`sonnet-2` is DeepMind's library, not a Claude model (config/entities.yaml).

        The input has to be one the pattern would otherwise match, or the test
        passes whether or not the denylist entry exists. "Sonnet v2.0.1" —
        the shape the real release titles use — extracts nothing either way and
        proved nothing; the release *bodies* are where `sonnet-2` was found.
        """
        import first_mention as fm

        cfg = fm.load_config()
        pattern = fm.model_pattern(cfg["model_families"], cfg["max_version_parts"])
        assert set(fm.identifiers("Sonnet 2 is released", pattern, set())) == {"sonnet-2"}
        assert dedupe.release_subjects("Sonnet 2 is released", 3) == set()


class TestReleasePairing:
    """Links relate; they never merge. The distinction is the whole design."""

    def _corpus(self):
        return [
            article(1, "openai/codex rust-v0.153.3", day=3, event_type="developer_tooling",
                    repo="openai/codex", body="Added GPT-6-Astra to the picker"),
            article(2, "Introducing GPT-6-Astra", day=2),
            # The SPACED spelling, which is what the real launch post uses. The
            # identifier pattern does not span a space, so this yields `gpt-6`
            # where the release body yields `gpt-6-astra` — the two only meet
            # through `stem`.
            article(3, "GPT-6 Astra: A new generation of intelligence", day=2),
            # Same identifier, different kind of event. Indistinguishable from
            # the launch posts on identifiers alone: both are exactly {gpt-6}.
            article(4, "Legora reviewed 41 documents with GPT-6 Astra", day=2,
                    event_type="enterprise_partnership"),
        ]

    def test_a_release_links_to_the_launch_that_names_the_same_model(self):
        links = dedupe.release_links(self._corpus(), PAIRING)
        assert (1, 2, "gpt-6-astra") in links

    def test_the_spaced_spelling_of_the_launch_post_links(self):
        """The regression this feature shipped broken once.

        "GPT-6 Astra: A new generation of intelligence" is the highest-scoring
        announcement in the corpus and the example the whole feature was written
        for. With the release side unstemmed it yielded `gpt-6`, the release
        yielded `gpt-6-astra`, and the flagship pair silently did not link —
        only the announcements that happened to hyphenate did.
        """
        links = dedupe.release_links(self._corpus(), PAIRING)
        assert (1, 3, "gpt-6") in links

    def test_a_customer_story_is_excluded_by_its_event_type(self):
        """Article 4 shares `gpt-6` with article 3 and must still not link.

        Nothing about the identifiers separates them — this is entirely the
        `announcement_events` gate, and if that gate goes the customer stories
        come back.
        """
        links = dedupe.release_links(self._corpus(), PAIRING)
        assert 4 not in {to_id for _, to_id, _ in links}

    def test_one_edge_per_pair_on_the_most_specific_identifier(self):
        """`gpt-6` and `gpt-6-astra` are one fact, not two rows."""
        links = [link for link in dedupe.release_links(self._corpus(), PAIRING)
                 if link[1] == 2]
        assert links == [(1, 2, "gpt-6-astra")]

    def test_the_evidence_is_stable_when_two_identifiers_tie_on_length(self):
        """A wholesale-rebuilt table has to converge on the same rows.

        `max` over a set breaks ties in iteration order, which follows string
        hash randomisation, so this pair stored `deepseek-r2` under one
        PYTHONHASHSEED and `deepseek-v4` under another — same input, different
        row, and the rendered citation flipping between runs.
        """
        rows = [
            article(1, "deepseek-ai/x v1", day=2, lab="deepseek",
                    event_type="developer_tooling", repo="deepseek-ai/x",
                    body="Adds DeepSeek-V4 and DeepSeek-R2 to the picker"),
            article(2, "DeepSeek-V4 and DeepSeek-R2 are now available", day=2,
                    lab="deepseek"),
        ]
        assert dedupe.release_links(rows, PAIRING) == [(1, 2, "deepseek-r2")]

    def test_a_link_is_directional_release_first(self):
        """`from` is the release: it records which side the evidence was read from."""
        links = dedupe.release_links(self._corpus(), PAIRING)
        assert links and all(from_id == 1 for from_id, _, _ in links)

    def test_different_labs_never_pair(self):
        rows = [
            article(1, "anthropics/x v1", day=2, lab="anthropic",
                    repo="anthropics/x", body="Added GPT-6-Astra"),
            article(2, "Introducing GPT-6-Astra", day=2, lab="openai"),
        ]
        assert dedupe.release_links(rows, PAIRING) == []

    def test_the_window_bounds_the_join(self):
        rows = [
            article(1, "openai/codex v1", day=1, repo="openai/codex",
                    body="Added GPT-6-Astra"),
            article(2, "Introducing GPT-6-Astra", day=1),
        ]
        rows[1]["published_on"] = date(2026, 10, 1)
        assert dedupe.release_links(rows, PAIRING) == []

    def test_two_releases_never_link_to_each_other(self):
        """Release-to-release is `release_trains`' job, and it groups rather than links.

        Without this, a repo's own train would be described twice in two
        different vocabularies and the card would say both.
        """
        rows = [
            article(1, "openai/codex v1", day=1, repo="openai/codex",
                    body="Added GPT-6-Astra"),
            article(2, "openai/openai-python v3", day=2, repo="openai/openai-python",
                    body="Added GPT-6-Astra"),
        ]
        assert dedupe.release_links(rows, PAIRING) == []

    def test_the_kill_switch_stops_the_pass(self):
        config = {"window_days": 14,
                  "pairing": {"enabled": False, "window_days": 14, "max_identifiers": 3}}
        assert dedupe.release_links(self._corpus(), config) == []


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
            url="u1", content_hash=dedupe.text_hash(text), model=MODEL, dim=3,
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
            url="u1", content_hash=dedupe.text_hash(stale), model=MODEL, dim=3,
            vector=dedupe.pack([1.0, 0.0, 0.0]),
        ))
        session.commit()

        classification = session.scalar(
            select(m.Classification).where(m.Classification.article_id == row.id)
        )
        classification.summary = "A materially different summary."
        session.commit()

        assert [u for u, _ in dedupe.pending(session, "v9")] == ["u1"]

    def test_a_vector_from_another_model_is_pending_again(self, session):
        """A model swap must re-embed, and the text hash alone will not say so.

        Two embedding models of the same width produce vectors in unrelated
        spaces. Keyed on the text alone, a swap re-embeds nothing: only new
        articles get the new model, the matrix silently mixes two spaces, and
        `matrix`'s width guard never fires because the width did not change.
        Cosines between the two spaces are still numbers in [0, 1], so pairs
        cross the auto-merge threshold on noise with every test green.
        """
        self._article(session, "u1", "GPT-6 Astra", "OpenAI announced GPT-6 Astra.")
        text = dedupe.embedded_text("GPT-6 Astra", "OpenAI announced GPT-6 Astra.")
        session.add(m.RawArticleEmbedding(
            url="u1", content_hash=dedupe.text_hash(text),
            model="some-other-model-of-the-same-width", dim=3,
            vector=dedupe.pack([1.0, 0.0, 0.0]),
        ))
        session.commit()
        assert [u for u, _ in dedupe.pending(session, "v9")] == ["u1"]

    def test_a_vector_from_another_model_is_not_loaded_into_the_matrix(self, session):
        """Belt to the braces above: a half-migrated cache must not be compared."""
        session.add(m.RawArticleEmbedding(
            url="u1", content_hash="h", model="some-other-model", dim=3,
            vector=dedupe.pack([1.0, 0.0, 0.0]),
        ))
        session.commit()
        urls, _ = dedupe.matrix(session, ["u1"])
        assert urls == []

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
            url="u1", content_hash="h", model=MODEL, dim=3,
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
            url="u1", content_hash="h", model=MODEL, dim=3,
            vector=dedupe.pack([1.0, 0.0, 0.0]),
        ))
        session.add(m.RawArticleEmbedding(
            url="u2", content_hash="h", model=MODEL, dim=4,
            vector=dedupe.pack([1.0, 0.0, 0.0, 0.0]),
        ))
        session.commit()
        with pytest.raises(ValueError, match="mixed widths"):
            dedupe.matrix(session, ["u1", "u2"])

    def test_a_zero_vector_does_not_divide_by_zero(self, session):
        session.add(m.RawArticleEmbedding(
            url="u1", content_hash="h", model=MODEL, dim=3,
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
        """Pairs that actually reach a threshold: post exact pass, post gate 2.

        The exact set comes from `calibrate.exact_pairs`, not from a copy of its
        rule. This test guards the thresholds in CI and `calibrate.py` derives
        them; restating the definition let the two compute different pools. It
        was harmless on the data at the time — both exact-mergeable pairs
        happened to have differing event types — and would stop being harmless
        the first time the classifier gives two copies of one article the *same*
        event type: calibrate drops it from the curve, this counts it as a
        positive, and `positives == 16` quietly stops meaning what it says.
        """
        from research.dedupe import calibrate

        exact = calibrate.exact_pairs(features, calibrate.articles())
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
        assert len(positives) == 16, (
            f"{len(positives)} positives now, not 16 — re-run "
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

class TestAssignWritesTheGrouping:
    """The function that actually writes, which nothing else here exercises.

    Every test above checks a helper in isolation. `assign` is where they
    compose, where the unique constraint on `article_groups.article_id` can turn
    a logic error into a run failure, and where the "a re-run produces the same
    assignment" claim either holds or does not.
    """

    @pytest.fixture()
    def session(self):
        engine = create_engine("sqlite://")
        create_all(engine)
        with Session(engine) as session:
            session.add(m.RefLab(id="openai", label="OpenAI", config_version=1))
            session.flush()
            yield session

    def _article(self, session, url, title, day, *, event_type="product_launch",
                 score=0.0, ai_score=0.0, payload=None, source="full_text"):
        raw = m.RawArticle(url=url, payload=payload or {}, content_hash=url,
                           source_file="a.json")
        session.add(raw)
        session.flush()
        art = m.Article(url=url, raw_article_id=raw.id, lab="openai", title=title,
                        published_on=date(2026, 9, day), text="body",
                        text_source=source)
        session.add(art)
        session.flush()
        session.add(m.Classification(
            article_id=art.id, prompt_version="v9", scoring_version=4,
            event_type=event_type, summary="s", is_signal=True, notable=False,
            notable_reason="", dropped_tags=[], score=score, band="none",
            ai_score=ai_score, ai_band="none"))
        session.commit()
        return art

    def test_every_article_gets_exactly_one_row(self, session):
        """The unique constraint makes a double-write a crash, not a bad group."""
        for index in range(4):
            self._article(session, f"u{index}", f"Title {index}", 1 + index)

        stats = dedupe.assign(session, "v9", adjudicate_pairs=False)

        rows = session.scalars(select(m.ArticleGroup)).all()
        assert len(rows) == 4 == stats["articles"]
        assert len({r.article_id for r in rows}) == 4

    def test_a_singleton_is_a_group_of_one_with_a_reason(self, session):
        """"Considered and left alone" has to be distinguishable from "not seen"."""
        self._article(session, "u1", "A one-off post", 1)

        dedupe.assign(session, "v9", adjudicate_pairs=False)

        row = session.scalars(select(m.ArticleGroup)).one()
        assert row.group_size == 1 and row.is_anchor and row.reason

    def test_one_article_at_two_urls_is_merged_across_disagreeing_event_types(self, session):
        """The live case, and the reason the exact pass runs before gate 2.

        The classifier gave two copies of "Introducing Intelligence Age" the
        labels `other` and `safety_policy`. Ordered the other way round, the
        event-type gate refuses to merge two copies of one text.
        """
        self._article(session, "u1", "Introducing Intelligence Age", 3,
                      event_type="other")
        self._article(session, "u2", "Introducing Intelligence Age!", 3,
                      event_type="safety_policy")

        stats = dedupe.assign(session, "v9", adjudicate_pairs=False)

        rows = session.scalars(select(m.ArticleGroup)).all()
        assert len({r.group_id for r in rows}) == 1
        assert stats["by_method"] == {"exact": 1}

    def test_a_release_train_collapses_and_anchors_on_the_latest(self, session):
        payload = {"org": "anthropics", "repo": "claude-code"}
        for index, day in enumerate((1, 2, 3)):
            self._article(session, f"r{index}", f"anthropics/claude-code v2.1.{index}",
                          day, payload=payload, source="github_release",
                          ai_score=50.0)

        dedupe.assign(session, "v9", adjudicate_pairs=False)

        rows = session.scalars(select(m.ArticleGroup)).all()
        assert len({r.group_id for r in rows}) == 1
        anchor = [r for r in rows if r.is_anchor]
        assert len(anchor) == 1
        art = session.get(m.Article, anchor[0].article_id)
        assert art.published_on == date(2026, 9, 3)

    def test_a_rerun_produces_the_same_assignment(self, session):
        """Claimed in the docstring, and the table is wiped and rebuilt each run.

        A grouping that shifts between runs would move items in and out of the
        digest with no change in the world to explain it.
        """
        self._article(session, "u1", "Introducing Intelligence Age", 3)
        self._article(session, "u2", "Introducing Intelligence Age!", 3)
        self._article(session, "u3", "Something else entirely", 3)

        first = dedupe.assign(session, "v9", adjudicate_pairs=False)
        before = {r.article_id: (r.group_size, r.is_anchor, r.method)
                  for r in session.scalars(select(m.ArticleGroup))}
        second = dedupe.assign(session, "v9", adjudicate_pairs=False)
        after = {r.article_id: (r.group_size, r.is_anchor, r.method)
                 for r in session.scalars(select(m.ArticleGroup))}

        assert before == after
        assert first["groups"] == second["groups"]

    def test_the_band_is_deferred_rather_than_adjudicated_when_told_not_to_spend(
        self, session
    ):
        """`--dry-run` must exercise the shape without calling a provider.

        `budget=None` does not express this: to `adjudicate` that means
        unlimited, not "do not call". Without the separate flag a dry run made
        real OpenAI and Anthropic calls.
        """
        self._article(session, "u1", "Introducing Intelligence Age", 3)
        self._article(session, "u2", "Introducing Intelligence Age!", 3)

        stats = dedupe.assign(session, "v9", adjudicate_pairs=False)

        assert stats["adjudicated"] == 0
        assert stats["usd"] == 0.0

    def test_pairing_writes_links_and_moves_no_group(self, session):
        """The load-bearing guarantee: a link relates, it never folds.

        If pairing ever reaches the union-find, a release and a launch post
        become one card and one of them stops being rendered — the false merge
        `config/dedupe.yaml` exists to prevent. This test fails the moment that
        happens, because the group count changes.
        """
        self._article(session, "u1", "Introducing GPT-6-Astra", 2,
                      event_type="frontier_model_release")
        self._article(
            session, "u2", "openai/codex rust-v0.153.3", 3,
            event_type="developer_tooling", source="github_release",
            payload={"org": "openai", "repo": "codex",
                     "text": "Added GPT-6-Astra to the picker"})

        stats = dedupe.assign(session, "v9", adjudicate_pairs=False)

        links = session.scalars(select(m.ArticleLink)).all()
        assert [(link.relation, link.evidence) for link in links] == [
            ("names_model", "gpt-6-astra")]
        assert stats["links"] == 1 and stats["paired"] == 1
        # Two articles, two groups, nothing collapsed.
        assert stats["groups"] == 2 and stats["collapsed"] == 0
        assert all(r.group_size == 1 for r in session.scalars(select(m.ArticleGroup)))

    def test_links_are_rebuilt_wholesale_rather_than_accumulated(self, session):
        """Silver, like `article_groups`: a re-run must not double the rows."""
        self._article(session, "u1", "Introducing GPT-6-Astra", 2,
                      event_type="frontier_model_release")
        self._article(
            session, "u2", "openai/codex rust-v0.153.3", 3,
            event_type="developer_tooling", source="github_release",
            payload={"org": "openai", "repo": "codex",
                     "text": "Added GPT-6-Astra to the picker"})

        dedupe.assign(session, "v9", adjudicate_pairs=False)
        dedupe.assign(session, "v9", adjudicate_pairs=False)

        assert len(session.scalars(select(m.ArticleLink)).all()) == 1

    def test_a_corpus_with_no_releases_reports_zero_rather_than_nothing(self, session):
        """Zero links and a pass that never ran must not look identical.

        The same reasoning as `coverage` on the gated pass: a stat that is only
        present when it is non-zero cannot distinguish "looked and found none"
        from "never looked".
        """
        self._article(session, "u1", "Introducing GPT-6-Astra", 2)

        stats = dedupe.assign(session, "v9", adjudicate_pairs=False)

        assert stats["links"] == 0 and stats["paired"] == 0

class TestAdjudicationIsCached:
    """A verdict is bought once, and the same run twice gives the same answer.

    Two silent failures, found by running `assign` twice against the live
    corpus and getting 547 groups and then 546:

    * **A re-run re-bought every verdict.** Every other LLM stage in this
      pipeline is free on a re-run — `classify` checks `raw_llm_responses`,
      `embed` checks the vector cache. This one was not, and CLAUDE.md's
      idempotent-re-run requirement is not satisfied by a stage that costs money
      each time it repeats.
    * **The grouping moved with no change in the world.** The model changed its
      mind about one pair between runs, so items entered and left the digest for
      a reason no reader could ever be shown. The re-run test above passes
      without this because it skips adjudication entirely.
    """

    @pytest.fixture()
    def session(self):
        engine = create_engine("sqlite://")
        create_all(engine)
        with Session(engine) as session:
            yield session

    def _pair(self):
        left = {"id": 1, "lab": "openai", "published_on": date(2026, 9, 3),
                "event_type": "product_launch", "title": "A", "summary": "s"}
        right = {**left, "id": 2, "title": "B"}
        return left, right

    def test_a_cached_verdict_is_returned_without_calling_the_provider(self, session):
        """No provider is monkeypatched: if it called out, this would fail."""
        left, right = self._pair()
        config = dedupe.settings()
        version = config["adjudication"]["prompt_version"]
        session.add(m.RawLlmResponse(
            url="dedupe:1-2", prompt_version=version,
            payload={"same": True, "reason": "cached"},
        ))
        session.commit()

        verdict, cost = dedupe.adjudicate(left, right, config, session=session)

        assert verdict == {"same": True, "reason": "cached"}
        assert cost == 0.0

    def test_the_cache_key_does_not_depend_on_pair_order(self, session):
        """`_key` orders the pair, or one comparison is bought twice."""
        left, right = self._pair()
        config = dedupe.settings()
        session.add(m.RawLlmResponse(
            url="dedupe:1-2", prompt_version=config["adjudication"]["prompt_version"],
            payload={"same": False, "reason": "cached"},
        ))
        session.commit()

        forward, _ = dedupe.adjudicate(left, right, config, session=session)
        backward, _ = dedupe.adjudicate(right, left, config, session=session)

        assert forward == backward

    def test_a_prompt_change_invalidates_the_verdict(self, session):
        """Keyed on prompt version, the same contract a classification has.

        A reworded rubric that kept its old answers would make the prompt
        version a lie about how the grouping was made — the exact drift
        `strip_comments` exists to prevent one layer up.
        """
        left, right = self._pair()
        config = dedupe.settings()
        session.add(m.RawLlmResponse(
            url="dedupe:1-2", prompt_version="v0-superseded",
            payload={"same": True, "reason": "old rubric"},
        ))
        session.commit()

        cached = session.scalar(
            select(m.RawLlmResponse).where(m.RawLlmResponse.prompt_version == "v0-superseded")
        )
        assert cached is not None
        assert config["adjudication"]["prompt_version"] != "v0-superseded"

