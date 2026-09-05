"""The repository relevance verdict and its cache.

Four silent failures this suite exists to catch.

**A cache key that never hits.** The whole cost argument for the filter is that
a repository is judged once and the answer kept. A key that varies with anything
per-run re-buys every verdict on every firing, and from outside it is
indistinguishable from a working cache — same watch list, same behaviour, a bill
that nobody reads until the month closes.

**A key that hits when it should not.** `raw_llm_responses` is unique on
`(url, prompt_version)`, so a key that omits the model serves verdicts bought
from a different one. The bake-off deliberately puts two candidates' verdicts in
that table, which is exactly when this goes wrong.

**Failing closed.** A wrongly excluded repository stops producing articles and
nothing downstream can tell that apart from a repository that shipped nothing.
The gate has to keep what it could not judge.

**Caching a failure.** A malformed verdict written to the cache freezes the
failure in place, for free, for ever — worse than re-buying it.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

# Deliberately no `sys.path` insert for research/announcements. `repo_relevance`
# has to put that on the path itself — it did not, and because the gate fails
# open the resulting ModuleNotFoundError produced a filter that silently did
# nothing. A test module that fixes up the path first cannot catch that.
from app import models as m
from app.db import create_all
from app.pipeline import repo_relevance

ROOT = Path(__file__).parent.parent
CONFIG = {"enabled": True, "provider": "anthropic",
          "model": "claude-haiku-4-5-20251001", "prompt_version": "r1",
          "max_judged": 30}

ROW = {"org": "google-deepmind", "repo": "mujoco", "stars": 14920,
       "description": "Multi-Joint dynamics with Contact.",
       "language": "C++", "topics": ["physics", "robotics"]}


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture()
def calls(monkeypatch):
    """Record every provider call and answer it locally."""
    from research.announcements import providers

    made = []

    def fake(provider, model, system, user, schema, url):
        made.append({"provider": provider, "model": model, "system": system,
                     "user": user, "url": url})
        return ({"relevant": False, "reason": "a physics simulator"},
                {"url": url, "model": model, "input_tokens": 900,
                 "output_tokens": 40, "usd": 0.0011, "seconds": 1.0, "at": "now"})

    monkeypatch.setattr(providers, "classify", fake)
    return made


class TestTheCache:
    def test_a_first_judgement_calls_the_provider_and_stores_the_verdict(
            self, session, calls):
        verdict, usd, error = repo_relevance.judge(session, ROW, CONFIG)

        assert verdict == {"relevant": False, "reason": "a physics simulator"}
        assert usd == 0.0011
        assert len(calls) == 1
        stored = session.scalars(select(m.RawLlmResponse)).one()
        assert stored.url == "repo:google-deepmind/mujoco"
        assert stored.prompt_version == "r1:claude-haiku-4-5-20251001"

    def test_a_cached_verdict_makes_no_call_and_costs_nothing(self, session, calls):
        repo_relevance.judge(session, ROW, CONFIG)
        verdict, usd, error = repo_relevance.judge(session, ROW, CONFIG)

        assert verdict["relevant"] is False
        assert usd == 0.0
        assert len(calls) == 1, "re-bought a verdict it already had"

    def test_the_cache_key_carries_the_model_not_just_the_version(
            self, session, calls):
        """The bake-off puts both candidates' verdicts in this table. A
        version-only key would serve the loser's answers under the winner's
        name, and nothing would report the substitution."""
        repo_relevance.judge(session, ROW, CONFIG)
        repo_relevance.judge(session, ROW, {**CONFIG, "model": "gpt-5-mini"})

        versions = {r.prompt_version for r in session.scalars(select(m.RawLlmResponse))}
        assert versions == {"r1:claude-haiku-4-5-20251001", "r1:gpt-5-mini"}
        assert len(calls) == 2

    def test_a_new_prompt_version_invalidates_the_verdicts(self):
        """Same contract as `(url, prompt_version)` on a classification: a
        rewritten rubric must not keep serving answers to the old question."""
        assert (repo_relevance.cache_key(CONFIG)
                != repo_relevance.cache_key({**CONFIG, "prompt_version": "r2"}))
        assert repo_relevance.cache_key(CONFIG) == "r1:claude-haiku-4-5-20251001"

    def test_two_repos_with_the_same_name_in_different_orgs_do_not_collide(
            self, session, calls):
        repo_relevance.judge(session, ROW, CONFIG)
        repo_relevance.judge(session, {**ROW, "org": "openai"}, CONFIG)

        urls = {r.url for r in session.scalars(select(m.RawLlmResponse))}
        assert urls == {"repo:google-deepmind/mujoco", "repo:openai/mujoco"}

    def test_without_a_session_it_still_answers_and_stores_nothing(self, calls):
        verdict, _, _ = repo_relevance.judge(None, ROW, CONFIG)

        assert verdict["relevant"] is False


class TestFailureKeepsTheRepo:
    def test_a_provider_failure_returns_no_verdict(self, session, monkeypatch):
        from research.announcements import providers

        def boom(*a, **k):
            raise RuntimeError("rate limited")

        monkeypatch.setattr(providers, "classify", boom)
        verdict, usd, error = repo_relevance.judge(session, ROW, CONFIG)

        assert verdict is None, "callers read None as keep; a False here would drop it"
        assert usd == 0.0
        assert "rate limited" in error, "an alert that cannot say why is half an alert"

    def test_the_prompt_actually_loads(self):
        """The one that would have caught the real failure.

        `prompt()` imports `score_announcements`, which imports *its* siblings by
        bare name — so importing it as a package member raises
        ModuleNotFoundError. That happened, and because the gate fails open it
        was completely silent: every verdict came back None, every repository was
        kept, and the run reported a full watch list and no fault. Nothing else
        in this suite would have noticed, because everything else stubs the
        provider and never reaches this line."""
        text = repo_relevance.prompt(CONFIG)

        assert "language models" in text.lower()
        assert len(text) > 500

    def test_malformed_output_returns_no_verdict_and_is_not_cached(
            self, session, monkeypatch):
        """Caching this would freeze the failure in place, for free, for ever."""
        from research.announcements import providers

        monkeypatch.setattr(providers, "classify", lambda *a, **k: (
            {"reason": "forgot the verdict"},
            {"url": "u", "model": "m", "input_tokens": 1, "output_tokens": 1,
             "usd": 0.0009, "seconds": 1.0, "at": "now"}))
        verdict, usd, error = repo_relevance.judge(session, ROW, CONFIG)

        assert verdict is None
        assert usd == 0.0009, "the provider billed it; the spend is still real"
        assert session.scalars(select(m.RawLlmResponse)).all() == []

    def test_a_non_boolean_relevant_is_malformed(self, session, monkeypatch):
        """`"relevant": "yes"` is truthy, so a plain key check would accept it
        and every repository would pass the gate."""
        from research.announcements import providers

        monkeypatch.setattr(providers, "classify", lambda *a, **k: (
            {"relevant": "yes", "reason": "r"},
            {"url": "u", "model": "m", "input_tokens": 1, "output_tokens": 1,
             "usd": 0.0, "seconds": 1.0, "at": "now"}))

        assert repo_relevance.judge(session, ROW, CONFIG)[0] is None


class TestOffTopicReadsOnlyTheCache:
    def _verdict(self, session, repo, relevant, version="r1:claude-haiku-4-5-20251001"):
        session.add(m.RawLlmResponse(
            url=f"repo:google-deepmind/{repo}", prompt_version=version,
            payload={"relevant": relevant, "reason": "r"}))
        session.flush()

    def test_only_the_off_topic_repos_are_returned(self, session):
        self._verdict(session, "mujoco", False)
        self._verdict(session, "gemma", True)

        assert repo_relevance.off_topic(session, CONFIG) == {"google-deepmind/mujoco"}

    def test_a_verdict_from_another_model_is_invisible(self, session):
        self._verdict(session, "mujoco", False, version="r1:gpt-5-mini")

        assert repo_relevance.off_topic(session, CONFIG) == set()

    def test_a_classification_row_is_never_mistaken_for_a_verdict(self, session):
        """`raw_llm_responses` is shared with classifications and duplicate
        adjudications. Matching on anything looser than the `repo:` prefix would
        read an article's payload as a repository verdict."""
        session.add(m.RawLlmResponse(
            url="https://openai.com/index/thing",
            prompt_version="r1:claude-haiku-4-5-20251001",
            payload={"event_type": "other", "summary": "s"}))
        session.flush()

        assert repo_relevance.off_topic(session, CONFIG) == set()

    def test_nothing_judged_means_nothing_excluded(self, session):
        assert repo_relevance.off_topic(session, CONFIG) == set()


class TestThePrompt:
    def test_the_rationale_comment_never_reaches_the_model(self, session, calls):
        """The v9 leak: a note explaining why the version exists was sent to the
        model on all 259 calls, as a leading statement about the input."""
        repo_relevance.judge(session, ROW, CONFIG)

        assert "<!--" not in calls[0]["system"]
        assert "D59c" not in calls[0]["system"]

    def test_the_repo_is_described_with_the_fields_the_eval_used(self, session, calls):
        repo_relevance.judge(session, ROW, CONFIG)
        user = calls[0]["user"]

        for expected in ("google-deepmind", "mujoco", "14920", "C++",
                         "physics, robotics", "Multi-Joint"):
            assert expected in user

    def test_a_repo_with_no_description_still_renders(self):
        rendered = repo_relevance.describe(
            {"org": "google-deepmind", "repo": "weathernext"})

        assert "(none)" in rendered
        assert "weathernext" in rendered

    @pytest.mark.parametrize("prompt_file", ["r1", "label_v1"])
    def test_no_prompt_names_a_repo_in_the_labelled_population(self, prompt_file):
        """D59c, enforced on **both** prompts.

        The v2 dedupe rubric carried seven worked examples, and seven of the
        twenty spot-checked pairs were those same examples — so its headline 0.90
        was a training score. That was a failure of the *labelling* rubric, which
        is why `label_v1.md` is checked here too: it is the file that produces
        the ground truth, so an example added to it turns every figure in D65
        into a partial training score, and checking only the production prompt
        would guard the file where it matters less.
        """
        population = ROOT / "research" / "docs" / "repo_population.json"
        if not population.exists():
            pytest.skip("population not built")

        text = repo_relevance.prompt({**CONFIG, "prompt_version": prompt_file}).lower()
        # Names of four characters or fewer are skipped: `chex`, `mmf`, `rlax`,
        # `Kats`, `moco` and `tree` collide with ordinary English and with each
        # other, and flagging those would make this test fire on prose. The hole
        # is real but small, and a worked example is not written with a
        # four-letter repository.
        named = sorted(
            {row["repo"] for row in json.loads(population.read_text(encoding="utf-8"))
             if len(row["repo"]) > 4 and row["repo"].lower() in text}
        )
        assert named == [], f"{prompt_file}.md names repositories it is scored on: {named}"


class TestTheConfiguredModelIsReal:
    def test_the_configured_model_is_priced(self):
        """`providers._cost` raises KeyError *after* the provider has billed the
        call, so an unpriced name spends money and returns nothing."""
        from research.announcements import providers
        import yaml

        config = yaml.safe_load(
            (ROOT / "config" / "repo_signals.yaml").read_text(encoding="utf-8"))
        assert config["relevance"]["model"] in providers.PRICES

    def test_the_configured_prompt_exists(self):
        import yaml

        config = yaml.safe_load(
            (ROOT / "config" / "repo_signals.yaml").read_text(encoding="utf-8"))
        version = config["relevance"]["prompt_version"]
        assert (ROOT / "prompts" / "repo_relevance" / f"{version}.md").exists()
