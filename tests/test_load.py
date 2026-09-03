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
from app.load_raw import cache_key, load_articles, load_classifications, load_costs


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
