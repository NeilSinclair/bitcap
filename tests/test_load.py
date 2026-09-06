"""Loaders: ref mirror counts, the validation gate, and raw upsert behaviour.

The silent failures these catch: a config edit that changes edge counts without
anyone noticing, an invalid config being laundered into the mirror, and a
re-run that duplicates raw rows instead of converging.
"""

import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import load_refs as lr
from app import models as m
from app.db import create_all
from app.load_raw import (cache_key, load_articles, load_classifications,
                          load_costs, load_repo_verdicts)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


class TestLoadRefs:
    def test_real_config_counts(self, session):
        counts = lr.load_refs(session, check=False)
        # 7 now that xAI's announcements leg is configured (wayback_cdx
        # method) -- D7's original five (anthropic, openai, deepseek,
        # google-deepmind, mistral) plus Meta AI (D14) plus xAI. Update this
        # alongside any further register change, same as every other count
        # pinned here.
        assert counts["ref_labs"] == 7
        assert counts["ref_mechanisms"] == 13
        assert counts["ref_categories"] == 13
        assert counts["ref_practices"] == 5
        assert counts["holdings"] == 26
        assert counts["holding_mechanisms"] == 76
        assert counts["holding_lab_exposure"] == 6
        assert counts["edges_skipped"] == 0
        dormant = session.scalar(select(func.count()).select_from(m.HoldingLabExposure)
                                 .where(m.HoldingLabExposure.is_dormant))
        assert dormant == 2

    def test_reload_replaces_not_duplicates(self, session):
        lr.load_refs(session, check=False)
        lr.load_refs(session, check=False)
        assert session.scalar(select(func.count()).select_from(m.Holding)) == 26

    def test_invalid_config_refuses(self, session, monkeypatch):
        import validate  # importable via load_refs' sys.path insertion

        monkeypatch.setattr(validate, "main", lambda: 1)
        with pytest.raises(RuntimeError, match="refusing"):
            lr.load_refs(session, check=True)


@pytest.fixture()
def artifacts(tmp_path):
    articles = [
        {"lab": "openai", "url": "https://x/a", "date": "2026-08-01", "title": "A",
         "text": "body a", "text_source": "full_text", "feed_category": None},
        {"lab": "anthropic", "url": "https://x/b", "date": "2026-08-02", "title": "B",
         "text": "body b", "text_source": "full_text", "feed_category": None},
    ]
    reg = tmp_path / "announcements.json"
    reg.write_text(json.dumps(articles))
    cache = tmp_path / "scores" / "v7"
    cache.mkdir(parents=True)
    (cache / cache_key("https://x/a")).write_text(json.dumps({"event_type": "other"}))
    costs = tmp_path / "cost.json"
    costs.write_text(json.dumps([
        {"url": "https://x/a", "model": "m", "input_tokens": 1, "output_tokens": 2,
         "usd": 0.5, "seconds": 1.0, "at": "t1"},
    ]))
    return reg, tmp_path / "scores", costs


class TestLoadRaw:
    def test_upsert_and_change_detection(self, session, artifacts):
        reg, scores, costs = artifacts
        assert load_articles(session, reg) == {"inserted": 2, "updated": 0, "unchanged": 0}
        assert load_articles(session, reg) == {"inserted": 0, "updated": 0, "unchanged": 2}
        edited = json.loads(reg.read_text())
        edited[0]["title"] = "A2"
        reg.write_text(json.dumps(edited))
        assert load_articles(session, reg) == {"inserted": 0, "updated": 1, "unchanged": 1}

    def test_classifications_report_missing_cache(self, session, artifacts):
        reg, scores, _ = artifacts
        counts = load_classifications(session, "v7", reg, scores)
        assert counts == {"inserted": 1, "updated": 0, "unchanged": 0, "missing": 1}

    def test_costs_dedupe_on_url_and_at(self, session, artifacts):
        _, _, costs = artifacts
        assert load_costs(session, costs)["inserted"] == 1
        again = load_costs(session, costs)
        assert again["inserted"] == 0 and again["new_usd"] == 0.0

    def test_a_duplicate_url_within_one_file_does_not_abort_the_load(self, session, artifacts):
        """"Duplicate content across sources" is a named failure mode.

        Without an in-run guard the second record hits the UNIQUE constraint and
        the whole load raises — which, combined with the ref wipe, used to mean
        an empty database rather than a stale one.
        """
        reg, _, _ = artifacts
        records = json.loads(reg.read_text())
        records.append({**records[0], "title": "duplicate arrival"})
        reg.write_text(json.dumps(records))

        counts = load_articles(session, reg)
        assert counts["inserted"] == 2 and counts["updated"] == 1
        rows = session.scalars(select(m.RawArticle)).all()
        assert len(rows) == 2
        assert {r.url for r in rows} == {r["url"] for r in records}
        # last write wins, as it does for a repeat across runs
        assert next(r for r in rows if r.url == records[0]["url"]).payload["title"] == \
            "duplicate arrival"


class TestLoadRepoVerdicts:
    """The relevance verdicts have to survive `bitcap-db rebuild`.

    The silent failure: `raw_llm_responses` is not an ops table, so a rebuild
    drops it — and the derivation gate only ever *reads* the cache, never
    re-buys. Without this loader a fresh clone renders every off-topic release
    again, while `source_state` (which *does* survive) still reports them
    excluded. The database and its own watermark disagree, and nothing raises.
    """

    def _artifact(self, tmp_path, rows):
        path = tmp_path / "verdicts.json"
        path.write_text(json.dumps(rows))
        return path

    def test_verdicts_load_into_the_cache_the_gate_reads(self, session, tmp_path):
        path = self._artifact(tmp_path, [
            {"repo": "google-deepmind/mujoco", "relevant": False,
             "reason": "a physics simulator", "prompt_version": "r1:gpt-5-mini"},
        ])
        counts = load_repo_verdicts(session, path=path)

        assert counts["inserted"] == 1
        row = session.scalars(select(m.RawLlmResponse)).one()
        assert row.url == "repo:google-deepmind/mujoco"
        assert row.prompt_version == "r1:gpt-5-mini"
        assert row.payload["relevant"] is False

    def test_a_second_load_converges(self, session, tmp_path):
        """`rebuild` and a live firing both call this; the second must not
        duplicate a row that `(url, prompt_version)` is unique on."""
        path = self._artifact(tmp_path, [
            {"repo": "o/r", "relevant": True, "reason": "r",
             "prompt_version": "r1:gpt-5-mini"}])
        load_repo_verdicts(session, path=path)
        counts = load_repo_verdicts(session, path=path)

        assert counts == {"inserted": 0, "unchanged": 1, "malformed": 0}

    def test_a_verdict_bought_tonight_is_not_overwritten(self, session, tmp_path):
        """The live cache wins. The artifact is a floor for a fresh database,
        not a source of truth that overwrites a fresher answer."""
        session.add(m.RawLlmResponse(
            url="repo:o/r", prompt_version="r1:gpt-5-mini",
            payload={"relevant": True, "reason": "judged tonight"}))
        session.flush()
        path = self._artifact(tmp_path, [
            {"repo": "o/r", "relevant": False, "reason": "from the artifact",
             "prompt_version": "r1:gpt-5-mini"}])

        load_repo_verdicts(session, path=path)

        assert session.scalars(select(m.RawLlmResponse)).one().payload["relevant"] is True

    def test_a_malformed_row_is_counted_not_fatal(self, session, tmp_path):
        """One bad row must not take down a load that is otherwise fine — and a
        non-boolean `relevant` is the dangerous shape, because `"no"` is truthy
        and would pass every repository through the gate."""
        path = self._artifact(tmp_path, [
            {"repo": "o/good", "relevant": True, "reason": "r",
             "prompt_version": "r1:gpt-5-mini"},
            {"repo": "o/bad", "relevant": "no", "reason": "r",
             "prompt_version": "r1:gpt-5-mini"},
            {"relevant": True, "prompt_version": "r1:gpt-5-mini"},
        ])
        counts = load_repo_verdicts(session, path=path)

        assert counts == {"inserted": 1, "unchanged": 0, "malformed": 2}

    def test_a_missing_artifact_is_not_an_error(self, session, tmp_path):
        assert load_repo_verdicts(session, path=tmp_path / "absent.json")["inserted"] == 0

    def test_the_committed_artifact_matches_the_configured_cache_key(self):
        """The artifact is keyed on `{prompt_version}:{model}`. If config moves
        to another model and the artifact is not refrozen, every verdict in it
        becomes invisible — a rebuild would load 183 rows that nothing reads."""
        import yaml

        from app.load_raw import REPO_VERDICTS
        from app.pipeline import repo_relevance

        if not REPO_VERDICTS.exists():
            pytest.skip("verdicts artifact not frozen")
        config = yaml.safe_load(
            (lr.ROOT / "config" / "repo_signals.yaml").read_text())["relevance"]
        expected = repo_relevance.cache_key(config)
        versions = {r["prompt_version"]
                    for r in json.loads(REPO_VERDICTS.read_text())}

        assert versions == {expected}
