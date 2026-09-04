"""Tests for corpus-level first-mention detection.

This module's whole value is that it sees something no single-document read can,
so the tests are about the corpus-level contract rather than the regex: a name
is new because *nothing we hold* mentions it earlier, and adding a source must
make the answer stricter, never noisier. The failure that matters is a name
reported as new when an older document already carried it -- that is a false
alarm on the one signal the pipeline cannot otherwise produce, and nothing
downstream could detect it.

The regex is tested for the two collisions found in real data: the `haiku`
model versus google-deepmind's `haiku` package, and Hugging Face collection ids
inside meta-llama release notes.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as m
from app.db import create_all

sys.path.insert(0, str(Path(__file__).parent.parent / "research" / "corpus"))

import first_mention
from first_mention import (first_mentions, identifiers, load_config,
                           load_corpus, model_pattern, normalise, quote_for,
                           text_fields, unannounced_repos)

CFG = load_config()
TODAY = date(2026, 9, 4)
PATTERN = model_pattern(CFG["model_families"], CFG["max_version_parts"])


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


def doc(url, when, text, lab="openai", source="release", corpus="releases"):
    """Build a document in the shape `load_corpora` produces."""
    return {"url": url, "date": when, "lab": lab, "source": source,
            "corpus": corpus, "text": text}


class TestRealModelNames:
    """Every tracked lab's actual naming scheme must extract.

    The first version of this suite tested only invented names -- `gpt-6-astra`,
    `claude-9`, `grok-9` -- which all happened to fit the pattern they were
    written alongside. The pattern required the version to start with a digit,
    so it matched none of DeepSeek's naming scheme and none of `gpt-4o`, and
    97 real occurrences in the live corpus extracted to nothing. Nothing
    downstream could have noticed: an empty result reads as a quiet week.

    These are real identifiers, one family per tracked lab.
    """

    @pytest.mark.parametrize("raw,expected", [
        ("DeepSeek-V4", "deepseek-v4"),
        ("deepseek-v4-pro", "deepseek-v4-pro"),
        ("deepseek v3.1", "deepseek-v3.1"),
        ("DeepSeek-R1", "deepseek-r1"),
        # The digit-digit rule reads the dated snapshot as a decimal, so the
        # join key is `r1.0528`. That is a key, not a display string -- every
        # spelling of the snapshot maps to it consistently, and `as_written`
        # carries the real one for the reader.
        ("deepseek-r1-0528", "deepseek-r1.0528"),
        ("gpt-4o", "gpt-4o"),
        ("GPT-6-Astra", "gpt-6-astra"),
        ("gpt-5.6-luna", "gpt-5.6-luna"),
        ("claude-3-opus", "claude-3-opus"),
        ("opus-4.7", "opus-4.7"),
        ("llama-4-scout", "llama-4-scout"),
        ("grok-4.6", "grok-4.6"),
        ("gemma-3n", "gemma-3n"),
        ("mistral-7b", "mistral-7b"),
        ("qwen-3", "qwen-3"),
    ])
    def test_a_real_model_identifier_extracts(self, raw, expected):
        assert expected in identifiers(f"we shipped {raw} today", PATTERN, set())

    def test_every_tracked_lab_s_family_is_in_the_vocabulary(self):
        """A pattern that silently covers only one lab is the failure this
        module is least able to report on itself: an empty result reads as a
        quiet week."""
        families = set(CFG["model_families"])
        for required in ("gpt", "claude", "deepseek", "grok", "gemma", "llama",
                         "mistral"):
            assert required in families


class TestExtraction:
    """What the shape rule catches, and the two collisions it must not."""

    def test_it_finds_a_versioned_codename(self):
        assert "gpt-6-astra" in identifiers(
            "Added support for configuring GPT-6-Astra through the API",
            PATTERN, set())

    def test_it_finds_a_decimal_version_with_a_codename(self):
        assert "gpt-5.6-luna" in identifiers(
            "The default model is now `gpt-5.6-luna`", PATTERN, set())

    def test_spelling_variants_are_one_name(self):
        """`GPT-6-Astra` and `gpt 6 astra` are the same model. Reported
        separately they read as two first mentions of two things."""
        assert normalise("GPT-6-Astra") == normalise("gpt 6 astra") == "gpt-6-astra"

    def test_the_api_identifier_and_the_prose_name_are_one_model(self):
        """Anthropic's own launch post writes "Opus 4.8" in prose and
        `claude-opus-4-8` for the API in the same document, and both were
        reported as separate first mentions of separate models."""
        assert normalise("opus-4-8") == normalise("Opus 4.8") == "opus-4.8"

    def test_a_word_after_the_separator_is_not_a_decimal(self):
        assert normalise("GPT-6-Astra") == "gpt-6-astra"
        assert normalise("claude-3-opus") == "claude-3-opus"

    def test_a_semver_package_is_not_a_model(self):
        """google-deepmind ships `haiku`, a neural-network library, tagged
        `haiku-0.0.7`. Claude Haiku is a model. Three-part versions are the
        deterministic separator -- a denylist would need one entry per package
        and would silently miss the next one."""
        assert identifiers("dm-haiku v0.0.7 released", PATTERN, set()) == {}

    def test_a_hugging_face_collection_id_does_not_carry_its_hash(self):
        """meta-llama release notes link
        `llama-32-66f448ffc8c32f949b04c8cf`. The hash must not become part of
        an identifier, or every link is a distinct new model."""
        found = identifiers(
            "see llama-32-66f448ffc8c32f949b04c8cf on the hub", PATTERN, set())
        assert all("66f448" not in k for k in found)

    def test_the_deny_list_is_honoured(self):
        assert identifiers("GPT-6-Astra", PATTERN, {"gpt-6-astra"}) == {}

    def test_a_bare_family_word_is_not_an_identifier(self):
        """"Claude wrote this" is not a model release."""
        assert identifiers("Claude wrote the docs today", PATTERN, set()) == {}

    def test_the_family_list_is_config_not_code(self):
        """Adding a lab's model family must not require touching the module."""
        pattern = model_pattern(["newfam"], 2)
        assert "newfam-1" in identifiers("newfam-1 ships today", pattern, set())


class TestFirstness:
    """A name is new because nothing older mentions it. That is the contract."""

    def test_a_recent_first_appearance_is_reported(self):
        docs = [doc("u1", "2026-09-03", "Added GPT-6-Astra to the catalog")]
        assert [r["name"] for r in first_mentions(docs, CFG, TODAY)] == [
            "gpt-6-astra"]

    def test_an_older_mention_anywhere_suppresses_it(self):
        """The whole point. If any document already carried the name, it is not
        news -- and a false alarm here is undetectable downstream."""
        docs = [doc("u1", "2026-09-03", "Added GPT-6-Astra to the catalog"),
                doc("u2", "2024-01-01", "GPT-6-Astra research preview",
                    source="announcement", corpus="announcements")]
        assert first_mentions(docs, CFG, TODAY) == []

    def test_firstness_is_cross_source_not_release_versus_announcement(self):
        """An announcement is reported the same way a release is. The rule is
        about the corpus, not about which leg found it."""
        docs = [doc("u1", "2026-09-01", "Introducing Claude-9",
                    lab="anthropic", source="announcement",
                    corpus="announcements")]
        found = first_mentions(docs, CFG, TODAY)
        assert found[0]["source"] == "announcement"

    def test_the_earliest_document_is_the_one_cited(self):
        docs = [doc("late", "2026-09-03", "GPT-6-Astra again"),
                doc("early", "2026-08-30", "GPT-6-Astra first")]
        assert first_mentions(docs, CFG, TODAY)[0]["url"] == "early"

    def test_an_old_name_mentioned_again_today_is_not_new(self):
        """`llama-3.1` appears in 2024-dated cookbook releases pulled in by the
        backfill. Recency on the *earliest* mention is what removes them."""
        docs = [doc("old", "2024-07-23", "llama-3.1 cookbook", lab="meta-ai"),
                doc("new", "2026-09-01", "llama-3.1 still supported",
                    lab="meta-ai")]
        assert first_mentions(docs, CFG, TODAY) == []

    def test_an_undated_document_cannot_establish_firstness(self):
        docs = [doc("u1", "", "GPT-6-Astra")]
        assert first_mentions(docs, CFG, TODAY) == []

    def test_mentions_are_counted_so_a_one_off_is_visible(self):
        docs = [doc("u1", "2026-09-03", "GPT-6-Astra"),
                doc("u2", "2026-09-04", "GPT-6-Astra Fast tier"),
                doc("u3", "2026-09-04", "gpt-6-astra in the SDK")]
        assert first_mentions(docs, CFG, TODAY)[0]["mentions"] == 3

    def test_the_report_carries_a_quote_from_the_source(self):
        """Same contract as every other claim in this project: a reason and a
        citation, not a bare assertion."""
        docs = [doc("u1", "2026-09-03",
                    "Added support for configuring GPT-6-Astra through the API")]
        assert "GPT-6-Astra" in first_mentions(docs, CFG, TODAY)[0]["quote"]

    def test_results_are_newest_first(self):
        docs = [doc("a", "2026-08-01", "grok-9 preview", lab="xai"),
                doc("b", "2026-09-01", "Claude-9 preview", lab="anthropic")]
        assert [r["first_seen"] for r in first_mentions(docs, CFG, TODAY)] == [
            "2026-09-01", "2026-08-01"]


class TestUnannouncedRepos:
    """The no-extraction half: a plain set difference over exact strings."""

    def test_a_repo_never_named_in_prose_is_reported(self):
        docs = [doc("u1", "2026-09-01", "We released a new model today")]
        rows = [{"repo": "deepseek-harness", "org": "deepseek-ai", "stars": 1}]
        assert len(unannounced_repos(docs, rows, 40)) == 1

    def test_a_repo_named_in_any_document_is_not_reported(self):
        docs = [doc("u1", "2026-09-01", "Try deepseek-harness today")]
        rows = [{"repo": "deepseek-harness", "org": "deepseek-ai", "stars": 1}]
        assert unannounced_repos(docs, rows, 40) == []

    def test_the_match_is_case_insensitive(self):
        docs = [doc("u1", "2026-09-01", "Try DeepSeek-Harness today")]
        rows = [{"repo": "deepseek-harness", "org": "deepseek-ai", "stars": 1}]
        assert unannounced_repos(docs, rows, 40) == []

    def test_only_the_top_slice_is_checked(self):
        docs = [doc("u1", "2026-09-01", "nothing relevant")]
        rows = [{"repo": f"r{i}", "org": "o", "stars": 100 - i} for i in range(50)]
        assert len(unannounced_repos(docs, rows, 10)) == 10




class TestQuote:
    def test_the_quote_contains_the_identifier(self):
        text = "a" * 400 + " configuring GPT-6-Astra through " + "b" * 400
        assert "GPT-6-Astra" in quote_for(text, "GPT-6-Astra")

    def test_an_absent_identifier_yields_no_quote(self):
        assert quote_for("nothing here", "GPT-6-Astra") == ""


class TestTheCorpusIsBronze:
    """`raw_articles` is the corpus, and `source_file` is what makes "which
    source got to this name first" answerable.

    A url is the identity of a document and bronze enforces it, so the
    double-counting the file corpora had -- 175 of 236 announcements also sat
    in the six-month archive, doubling every mention total -- cannot recur.
    """

    def rows(self, session):
        session.add_all([
            m.RawArticle(url="https://openai.com/a", content_hash="h1",
                         source_file="research/docs/announcements.json",
                         payload={"lab": "openai", "date": "2026-09-01",
                                  "title": "Introducing GPT-6-Astra",
                                  "text": "body", "text_source": "rss_summary"}),
            m.RawArticle(url="https://github.com/o/r/releases/tag/v1",
                         content_hash="h2", source_file="github_releases",
                         payload={"lab": "openai", "date": "2026-09-03",
                                  "title": "o/r v1",
                                  "text": "Added Claude-9 support",
                                  "text_source": "github_release"}),
        ])
        session.commit()

    def test_every_leg_s_documents_are_read(self, session):
        self.rows(session)
        assert len(load_corpus(session)) == 2

    def test_the_source_is_carried_through(self, session):
        """The whole point: a name first seen in a release shipped in code
        before anyone wrote about it; first seen in an announcement it is an
        ordinary launch. Losing this field loses the finding."""
        self.rows(session)
        by_url = {d["url"]: d for d in load_corpus(session)}
        assert by_url["https://github.com/o/r/releases/tag/v1"]["source"] == \
            "github_release"

    def test_a_release_title_is_not_read_as_source_text(self, session):
        """A release item's title is built by `as_announcement` as
        "{org}/{repo} {tag}" -- our string, not GitHub's. Reading it as prose
        made the emitted quote start with words absent from the release note,
        and injected every repository's own name into the text the
        unannounced-repository pass reads."""
        self.rows(session)
        by_url = {d["url"]: d for d in load_corpus(session)}
        text = by_url["https://github.com/o/r/releases/tag/v1"]["text"]
        assert text == "Added Claude-9 support"
        assert "o/r v1" not in text

    def test_an_announcement_title_is_source_text(self, session):
        """It is the publisher's own headline, and models get announced in it."""
        self.rows(session)
        by_url = {d["url"]: d for d in load_corpus(session)}
        assert "GPT-6-Astra" in by_url["https://openai.com/a"]["text"]

    def test_text_fields_keys_on_the_source_not_the_url(self):
        assert text_fields({"text_source": "github_release"}) == ("text",)
        assert text_fields({"text_source": "rss_summary"}) == ("title", "text")

    def test_a_release_name_is_still_found_in_its_body(self, session):
        self.rows(session)
        found = first_mentions(load_corpus(session), CFG, date(2026, 9, 4))
        assert "claude-9" in {r["name"] for r in found}

    def test_every_quote_is_verbatim_in_its_document(self, session):
        """Non-negotiable 1. `quote_for` slices the same text firstness was
        computed over, so this holds by construction -- unless a source starts
        contributing text this pipeline wrote."""
        self.rows(session)
        docs = load_corpus(session)
        by_url = {d["url"]: d for d in docs}
        for row in first_mentions(docs, CFG, date(2026, 9, 4)):
            collapsed = " ".join(by_url[row["url"]]["text"].split())
            assert row["quote"] in collapsed, row["name"]
