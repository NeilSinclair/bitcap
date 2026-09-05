"""The spending ceiling, and the classification stage it bounds.

The silent failures these catch:

* A ceiling that does not actually stop anything. A budget object that records
  spend but never gates a call is worse than none — it looks like a control in
  the design doc and is not one at runtime.
* Month-to-date starting at zero. The per-month ceiling only means anything if
  the run knows what the month already cost; seeded wrong, thirty runs each
  "within budget" spend thirty times the monthly limit.
* A budget-truncated run being indistinguishable from a quiet one. Four
  articles classified because the ceiling hit looks exactly like four new
  articles, unless `skipped_for_budget` says otherwise.
* Re-classifying work already done, which is pure cost for no output.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models as m
from app.db import create_all
from app.pipeline import classify
from app.pipeline.budget import Budget, BudgetExceeded, month_to_date

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


def cost_row(usd, at="2026-09-01T00:00:00+00:00", url="https://x/1"):
    return m.RawCost(url=url, model="claude-sonnet-5", input_tokens=1, output_tokens=1,
                     usd=usd, seconds=1.0, at=at)


class TestCeilings:
    def test_a_fresh_budget_is_not_exhausted(self):
        assert Budget(per_run_usd=3.0, per_month_usd=20.0).exhausted is False

    def test_the_run_ceiling_binds(self):
        budget = Budget(per_run_usd=1.0, per_month_usd=20.0)
        budget.spend(0.99)
        assert budget.exhausted is False
        budget.spend(0.02)
        assert budget.exhausted is True
        with pytest.raises(BudgetExceeded, match="per-run"):
            budget.check()

    def test_the_month_ceiling_binds_independently(self):
        """The slow leak: thirty runs each individually reasonable."""
        budget = Budget(per_run_usd=3.0, per_month_usd=20.0, month_spent_before=19.9)
        budget.spend(0.2)
        assert budget.exhausted is True
        with pytest.raises(BudgetExceeded, match="per-month"):
            budget.check()

    def test_remaining_is_whichever_ceiling_binds_first(self):
        budget = Budget(per_run_usd=3.0, per_month_usd=20.0, month_spent_before=19.0)
        assert budget.remaining == pytest.approx(1.0)

    def test_the_breach_names_the_scope_and_the_numbers(self):
        budget = Budget(per_run_usd=0.5, per_month_usd=20.0)
        budget.spend(0.6)
        with pytest.raises(BudgetExceeded) as exc:
            budget.check()
        assert exc.value.scope == "per-run"
        assert exc.value.limit == 0.5
        assert exc.value.spent == pytest.approx(0.6)

    def test_spend_is_thread_safe(self):
        """A dozen classifier workers all record cost concurrently."""
        import threading

        budget = Budget(per_run_usd=1000.0, per_month_usd=1000.0)
        threads = [threading.Thread(target=lambda: [budget.spend(0.001) for _ in range(200)])
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert budget.run_spent == pytest.approx(1.6)


class TestMonthToDate:
    def test_reads_the_current_month_only(self, session):
        session.add_all([
            cost_row(5.00, "2026-08-15T00:00:00+00:00", "https://x/aug"),
            cost_row(1.50, "2026-09-02T00:00:00+00:00", "https://x/sep1"),
            cost_row(0.25, "2026-09-14T00:00:00+00:00", "https://x/sep2"),
        ])
        session.commit()
        assert month_to_date(session, now=NOW) == pytest.approx(1.75)

    def test_an_empty_log_is_zero_not_an_error(self, session):
        assert month_to_date(session, now=NOW) == 0.0

    def test_from_config_seeds_the_month_from_the_cost_record(self, session, tmp_path):
        """Seeded wrong, thirty 'in-budget' runs spend thirty monthly limits."""
        now_month = datetime.now(timezone.utc).strftime("%Y-%m")
        session.add(cost_row(4.0, f"{now_month}-02T00:00:00+00:00"))
        session.commit()
        config = tmp_path / "pipeline.yaml"
        config.write_text(
            "budget:\n  per_run_usd: 3.0\n  per_month_usd: 20.0\n  on_exceed: abort_llm_stage\n"
        )

        budget = Budget.from_config(session, config)

        assert budget.per_run_usd == 3.0
        assert budget.month_spent_before == pytest.approx(4.0)
        assert budget.month_remaining == pytest.approx(16.0)

    def test_without_a_session_the_month_starts_at_zero(self, tmp_path):
        """Right for a test, wrong for production — hence a session everywhere."""
        config = tmp_path / "pipeline.yaml"
        config.write_text("budget:\n  per_run_usd: 3.0\n  per_month_usd: 20.0\n")
        assert Budget.from_config(None, config).month_spent_before == 0.0

    def test_the_real_config_parses(self):
        """config/pipeline.yaml must stay loadable — the whole run reads it."""
        budget = Budget.from_config()
        assert budget.per_run_usd > 0
        assert budget.per_month_usd >= budget.per_run_usd


class TestPendingWork:
    def test_only_unclassified_articles_are_pending(self, session):
        session.add_all([
            m.RawArticle(url="https://a/1", payload={}, content_hash="h", source_file="f"),
            m.RawArticle(url="https://a/2", payload={}, content_hash="h", source_file="f"),
            m.RawLlmResponse(url="https://a/1", prompt_version="v7", payload={}),
        ])
        session.commit()
        assert classify.pending_urls(session, "v7") == ["https://a/2"]

    def test_a_different_prompt_version_counts_as_unclassified(self, session):
        """Bumping the classifier means the whole corpus is pending again."""
        session.add_all([
            m.RawArticle(url="https://a/1", payload={}, content_hash="h", source_file="f"),
            m.RawLlmResponse(url="https://a/1", prompt_version="v6", payload={}),
        ])
        session.commit()
        assert classify.pending_urls(session, "v7") == ["https://a/1"]

    def test_nothing_pending_is_the_steady_state(self, session):
        session.add_all([
            m.RawArticle(url="https://a/1", payload={}, content_hash="h", source_file="f"),
            m.RawLlmResponse(url="https://a/1", prompt_version="v7", payload={}),
        ])
        session.commit()
        assert classify.pending_urls(session, "v7") == []

    def test_no_pending_work_needs_no_api_key(self, session, monkeypatch):
        """A quiet run must not import or construct an LLM client at all."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = classify.classify_new(session, "v7")
        assert result == {
            "pending": 0, "classified": 0, "skipped_for_budget": 0,
            "failures": [], "cost_usd": 0.0, "bands": {}, "budget": None,
        }


class TestClassifyNewIsBounded:
    def test_the_ceiling_stops_further_calls(self, session, tmp_path, monkeypatch):
        """The property that makes an unattended cron safe."""
        session.add_all([
            m.RawArticle(url=f"https://a/{i}", payload={}, content_hash="h", source_file="f")
            for i in range(5)
        ])
        session.commit()

        corpus = tmp_path / "announcements.json"
        corpus.write_text(__import__("json").dumps([
            {"url": f"https://a/{i}", "date": "2026-08-01", "title": "t",
             "text": "body", "lab": "anthropic", "text_source": "full_text"}
            for i in range(5)
        ]))

        calls = []

        def fake_run(articles, model, workers, batch, budget):
            # Each call costs 0.40; the ceiling is 1.00, so it stops after 3.
            for article in articles:
                if budget.exhausted:
                    continue
                calls.append(article["url"])
                budget.spend(0.40)
            return {
                "scored": [], "failures": [], "cost_usd": round(0.40 * len(calls), 6),
                "classified": len(calls),
                "skipped_for_budget": len(articles) - len(calls),
                "bands": {},
            }

        import score_announcements as scorer

        monkeypatch.setattr(scorer, "run", fake_run)

        budget = Budget(per_run_usd=1.0, per_month_usd=20.0)
        result = classify.classify_new(session, "v7", budget=budget, articles_path=corpus)

        assert len(calls) == 3
        assert result["skipped_for_budget"] == 2
        assert result["pending"] == 5
        assert budget.exhausted is True

    def test_a_truncated_run_is_distinguishable_from_a_quiet_one(self, session):
        truncated = {"skipped_for_budget": 2,
                     "budget": {"run_usd": 1.2, "run_limit": 1.0,
                                "month_usd": 1.2, "month_limit": 20.0}}
        assert isinstance(classify.budget_breach(truncated), BudgetExceeded)
        assert classify.budget_breach(truncated).scope == "per-run"

        quiet = {"skipped_for_budget": 0, "budget": {"run_usd": 0.1, "run_limit": 1.0}}
        assert classify.budget_breach(quiet) is None

    def test_a_month_breach_is_reported_as_such(self):
        result = {"skipped_for_budget": 1,
                  "budget": {"run_usd": 0.5, "run_limit": 3.0,
                             "month_usd": 20.1, "month_limit": 20.0}}
        assert classify.budget_breach(result).scope == "per-month"


class TestInFlightAccounting:
    """The guard must be predictive, not reactive.

    Measured live before this existed: a $0.08 ceiling with 12 workers spent
    $0.53, because every worker checked a total none of its peers had yet
    contributed to. The overshoot is bounded by the worker count, which is
    exactly the number that makes a ceiling meaningless at small values.
    """

    def test_calls_in_flight_count_against_the_ceiling(self):
        budget = Budget(per_run_usd=0.10, per_month_usd=20.0)
        # Four concurrent calls at the seeded ~$0.029 estimate commit ~$0.116.
        assert [budget.begin_call() for _ in range(4)] == [True, True, True, False]

    def test_a_settled_call_frees_its_slot(self):
        budget = Budget(per_run_usd=1.0, per_month_usd=20.0)
        assert budget.begin_call() is True
        budget.end_call(0.03)
        assert budget._in_flight == 0
        assert budget.run_spent == pytest.approx(0.03)
        assert budget.calls == 1

    def test_a_cached_hit_releases_without_spending(self):
        """Cached articles cost nothing but still occupy a slot while checked."""
        budget = Budget(per_run_usd=1.0, per_month_usd=20.0)
        budget.begin_call()
        budget.end_call(0.0)
        assert budget.run_spent == 0.0
        assert budget.calls == 0
        assert budget._in_flight == 0

    def test_the_estimate_learns_from_observed_cost(self):
        budget = Budget(per_run_usd=10.0, per_month_usd=20.0)
        for usd in (0.10, 0.10, 0.10, 0.10):
            budget.begin_call()
            budget.end_call(usd)
        assert budget.expected_per_call == pytest.approx(0.10)

    def test_a_fan_out_respects_the_ceiling_within_one_call(self):
        """The property the live overshoot violated."""
        import threading

        budget = Budget(per_run_usd=0.30, per_month_usd=20.0)
        made = []
        barrier = threading.Barrier(12)

        def worker():
            barrier.wait()  # force maximum contention on the guard
            if budget.begin_call():
                made.append(1)
                budget.end_call(0.029)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 0.30 / 0.029 ~= 10 calls; without in-flight accounting all 12 fire.
        assert len(made) <= 11
        assert budget.run_spent <= 0.33

    def test_the_month_ceiling_also_counts_in_flight(self):
        budget = Budget(per_run_usd=100.0, per_month_usd=20.0, month_spent_before=19.95)
        assert budget.begin_call() is True
        assert budget.begin_call() is False


class TestTheRegisterIsMergedNotReplaced:
    """A partial run must not truncate the committed register.

    The same failure the announcements corpus had: `run()` writes the register
    from the articles it was *given*, so classifying only what is new — which is
    the whole point of the scheduled stage — rewrote a 236-article register down
    to the 61 rows that run touched. Nothing errors; the register just silently
    becomes a record of the last run instead of the corpus.
    """

    def _run_with(self, monkeypatch, tmp_path, articles, existing=None):
        import json

        import score_announcements as scorer

        out = tmp_path / "scored.json"
        if existing is not None:
            out.write_text(json.dumps({"scored": existing, "failures": []}))
        monkeypatch.setattr(scorer, "OUT", out)
        monkeypatch.setattr(scorer, "CACHE", tmp_path / "cache")
        monkeypatch.setattr(scorer, "COST", tmp_path / "cost.json")
        monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: object())
        monkeypatch.setattr(scorer, "classify_one", lambda *a, **k: (
            {"event_type": "other", "mechanisms": [], "categories": [], "practices": [],
             "summary": "", "notable": False, "notable_reason": "", "dropped_tags": []},
            None, None,
        ))
        scorer.run(articles, workers=2)
        return json.loads(out.read_text())["scored"]

    def test_untouched_rows_survive_a_partial_run(self, monkeypatch, tmp_path):
        existing = [
            {"url": "https://a/old", "score": 66.7, "band": "high", "title": "kept"},
            {"url": "https://a/new", "score": 0.0, "band": "none", "title": "stale"},
        ]
        register = self._run_with(
            monkeypatch, tmp_path,
            [{"url": "https://a/new", "date": "2026-08-01", "text": "t", "title": "fresh",
              "lab": "anthropic", "text_source": "full_text"}],
            existing=existing,
        )

        urls = {r["url"] for r in register}
        assert urls == {"https://a/old", "https://a/new"}
        assert next(r for r in register if r["url"] == "https://a/old")["title"] == "kept"

    def test_a_reclassified_row_is_replaced_not_duplicated(self, monkeypatch, tmp_path):
        register = self._run_with(
            monkeypatch, tmp_path,
            [{"url": "https://a/1", "date": "2026-08-01", "text": "t", "title": "fresh",
              "lab": "anthropic", "text_source": "full_text"}],
            existing=[{"url": "https://a/1", "score": 99.0, "band": "high", "title": "old"}],
        )
        assert len(register) == 1
        assert register[0]["title"] == "fresh"

    def test_the_register_stays_sorted_by_score(self, monkeypatch, tmp_path):
        register = self._run_with(
            monkeypatch, tmp_path,
            [{"url": "https://a/1", "date": "2026-08-01", "text": "t", "title": "t",
              "lab": "anthropic", "text_source": "full_text"}],
            existing=[{"url": "https://a/9", "score": 66.7, "band": "high", "title": "x"}],
        )
        assert [r["score"] for r in register] == sorted(
            (r["score"] for r in register), reverse=True
        )


class TestTheBatchPathIsAlsoBounded:
    """A config flag must not be able to switch the ceiling off.

    `classification.batch: true` is a one-line config change. The batch path
    submitted the whole pending set with no check, then called `spend()` on the
    results afterwards — enforcing nothing while still recording a budget
    snapshot on the run, so the control *reported itself as working*. Because
    `skipped_for_budget` stayed 0, `budget_breach()` returned None and the
    `budget_exceeded` alert never fired either.
    """

    def _run_batch_with(self, monkeypatch, tmp_path, budget, n_articles):
        import json

        import score_announcements as scorer

        submitted = {}

        def fake_run_batch(client, model, articles, *a, **k):
            submitted["count"] = len(articles)
            return {}, [], [{"usd": 0.01} for _ in articles]

        monkeypatch.setattr(scorer, "run_batch", fake_run_batch)
        monkeypatch.setattr(scorer, "OUT", tmp_path / "scored.json")
        monkeypatch.setattr(scorer, "CACHE", tmp_path / "cache")
        monkeypatch.setattr(scorer, "COST", tmp_path / "cost.json")
        monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: object())

        articles = [
            {"url": f"https://a/{i}", "date": "2026-08-01", "text": "t", "title": "t",
             "lab": "anthropic", "text_source": "full_text"}
            for i in range(n_articles)
        ]
        summary = scorer.run(articles, batch=True, budget=budget)
        return submitted.get("count", 0), summary

    def test_the_submission_is_capped_by_the_remaining_budget(self, monkeypatch, tmp_path):
        # $0.30 remaining, ~$0.029/call, halved for batch -> ~20 affordable.
        budget = Budget(per_run_usd=0.30, per_month_usd=20.0)
        count, summary = self._run_batch_with(monkeypatch, tmp_path, budget, 100)

        assert count < 100, "the whole set was submitted with no ceiling"
        assert count == pytest.approx(20, abs=2)
        assert summary["skipped_for_budget"] == 100 - count

    def test_an_exhausted_budget_submits_nothing(self, monkeypatch, tmp_path):
        budget = Budget(per_run_usd=0.0, per_month_usd=0.0)
        count, summary = self._run_batch_with(monkeypatch, tmp_path, budget, 10)

        assert count == 0
        assert summary["skipped_for_budget"] == 10

    def test_a_truncated_batch_run_reports_a_breach(self, monkeypatch, tmp_path):
        """Otherwise the run records a budget it never enforced."""
        budget = Budget(per_run_usd=0.05, per_month_usd=20.0)
        _, summary = self._run_batch_with(monkeypatch, tmp_path, budget, 100)

        assert summary["skipped_for_budget"] > 0
        assert classify.budget_breach({**summary, "budget": budget.snapshot()}) is not None

    def test_no_budget_still_submits_everything(self, monkeypatch, tmp_path):
        """A human running the CLI without a ceiling is unchanged."""
        count, summary = self._run_batch_with(monkeypatch, tmp_path, None, 10)
        assert count == 10
        assert summary["skipped_for_budget"] == 0


class TestTheScorerReadsBronze:
    """`classify_new` takes article text from `raw_articles`, not a file.

    This is the path that spends money and it had no test: every existing case
    either passed `articles_path` (the legacy file branch), returned at the
    `pending: 0` short-circuit, or monkeypatched `classify_new` away. Revert
    the DB read and the suite stayed green while every release row sat pending
    for ever -- re-listed each firing, costing nothing, producing nothing, and
    reporting `classified: N, cost_usd: 0.0` like a healthy incremental run.
    """

    def _rows(self, session):
        session.add_all([
            m.RawArticle(url="https://openai.com/a", content_hash="h1",
                         source_file="research/docs/announcements.json",
                         payload={"url": "https://openai.com/a", "lab": "openai",
                                  "date": "2026-09-01", "title": "post",
                                  "text": "announcement body",
                                  "text_source": "rss_summary"}),
            m.RawArticle(url="https://github.com/o/r/releases/tag/v1",
                         content_hash="h2", source_file="github_releases",
                         payload={"url": "https://github.com/o/r/releases/tag/v1",
                                  "lab": "openai", "date": "2026-09-03",
                                  "title": "o/r v1", "text": "release body",
                                  "text_source": "github_release"}),
        ])
        session.commit()

    def _spy(self, monkeypatch, seen):
        def fake_run(articles, model, workers, batch, budget):
            seen.extend(articles)
            return {"scored": [], "failures": [], "cost_usd": 0.0,
                    "classified": len(articles), "skipped_for_budget": 0,
                    "bands": {}}

        import score_announcements as scorer
        monkeypatch.setattr(scorer, "run", fake_run)

    def test_the_payload_from_bronze_is_what_reaches_the_scorer(
            self, session, monkeypatch):
        self._rows(session)
        seen = []
        self._spy(monkeypatch, seen)

        classify.classify_new(session, "v7",
                              budget=Budget(per_run_usd=5.0, per_month_usd=20.0))

        texts = {a["text"] for a in seen}
        assert texts == {"announcement body", "release body"}

    def test_a_release_row_is_not_dropped_for_being_in_another_source_file(
            self, session, monkeypatch):
        """The failure D53 fixes: filtering one corpus file against a work list
        built from the whole table silently drops every other file's rows."""
        self._rows(session)
        seen = []
        self._spy(monkeypatch, seen)

        result = classify.classify_new(
            session, "v7", budget=Budget(per_run_usd=5.0, per_month_usd=20.0))

        assert result["classified"] == result["pending"] == 2
        assert "https://github.com/o/r/releases/tag/v1" in {a["url"] for a in seen}

    def test_the_scorer_gets_the_work_list_in_its_own_order(self, session,
                                                            monkeypatch):
        """Ordering `pending_urls` alone does nothing: this is the list the
        scorer slices when the budget binds, so which articles a truncated run
        paid for has to follow it."""
        for url in ("https://c", "https://a", "https://b"):
            session.add(m.RawArticle(
                url=url, content_hash="h", source_file="f",
                payload={"url": url, "text": "t", "lab": "openai",
                         "date": "2026-09-01", "title": "t",
                         "text_source": "rss_summary"}))
        session.commit()
        seen = []
        self._spy(monkeypatch, seen)

        classify.classify_new(session, "v7",
                              budget=Budget(per_run_usd=5.0, per_month_usd=20.0))

        assert [a["url"] for a in seen] == ["https://a", "https://b", "https://c"]
