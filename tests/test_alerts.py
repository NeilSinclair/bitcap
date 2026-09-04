"""Alerting: the two kinds, and the dedupe that decides whether anyone reads them.

The silent failures these catch:

* **A rule that never fires.** An alerter is the one component whose failure
  mode is silence, and silence is also its healthy state. Each rule is tested
  firing *and* not firing, because "no alerts" proves nothing on its own.
* **Repeat alerting on one incident.** A source down for a week that alerts
  nightly is worse than no alerting: it trains everyone to mute the channel, and
  then the real one is missed too.
* **Conflating the two kinds.** CLAUDE.md requires system-failure alerting
  distinct from content alerting. If a scorer degrading arrives looking like a
  finding about the world, the distinction has been lost in implementation.
* **A dead channel losing alerts.** The row is the record of truth; delivery is
  a courtesy. A webhook being down must neither fail the run nor lose the alert.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import models as m
from app.db import create_all
from app.pipeline import alerts
from app.pipeline import state as state_mod
from app.pipeline.budget import BudgetExceeded

CONFIG = {
    "source_down_runs": 3,
    "content_band": "high",
    "content_min_strength": 0.5,
    "drift_agreement_floor": 0.80,
    "channel": "stdout",
    "max_deliveries_per_run": 10,
}
NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        yield s


def article_with(session, band="high", score=66.7, lab="anthropic", url="https://a/1"):
    session.add(m.RefLab(id=lab, label=lab, config_version=1))
    raw = m.RawArticle(url=url, payload={}, content_hash="h", source_file="f")
    session.add(raw)
    session.flush()
    article = m.Article(url=url, raw_article_id=raw.id, lab=lab, title="A title",
                        published_on=NOW.date(), text="body", text_source="full_text")
    session.add(article)
    session.flush()
    cls = m.Classification(article_id=article.id, prompt_version="v7", scoring_version=4,
                           event_type="frontier_model_release", summary="A summary",
                           notable=True, notable_reason="", score=score, band=band,
                           ai_score=0.0, ai_band="none")
    session.add(cls)
    session.flush()
    return article, cls


class TestSystemRuleRunFailed:
    def test_a_failed_run_alerts(self, session):
        """The standing query app/runs.py was designed around, finally executed."""
        session.add(m.PipelineRun(kind="scheduled", status="failed", error="boom"))
        session.commit()

        found = alerts.evaluate(session, CONFIG, rules=("run_failed",))

        assert len(found) == 1
        assert found[0].kind == alerts.SYSTEM
        assert found[0].severity == alerts.CRITICAL
        assert "boom" in found[0].body

    def test_a_succeeded_run_does_not_alert(self, session):
        session.add(m.PipelineRun(kind="scheduled", status="succeeded"))
        session.commit()
        assert alerts.evaluate(session, CONFIG, rules=("run_failed",)) == []

    def test_dispatch_marks_the_run_so_it_stops_returning(self, session):
        session.add(m.PipelineRun(kind="scheduled", status="failed", error="boom"))
        session.commit()

        alerts.dispatch(session, alerts.evaluate(session, CONFIG, rules=("run_failed",)), CONFIG)

        assert session.scalars(select(m.PipelineRun)).one().alerted_at is not None
        assert alerts.evaluate(session, CONFIG, rules=("run_failed",)) == []


class TestSystemRuleSourceDown:
    def _failing(self, session, n, last_success=None, leg="announcements", source="openai"):
        st = state_mod.load(session, leg, source)
        st.consecutive_failures = n
        st.last_error = "503 from the archive"
        st.last_success_at = last_success
        session.commit()
        return st

    def test_fires_at_the_threshold(self, session):
        self._failing(session, 3)
        found = alerts.evaluate(session, CONFIG, rules=("source_down",))
        assert len(found) == 1
        assert found[0].kind == alerts.SYSTEM
        assert "3 runs" in found[0].subject

    def test_does_not_fire_below_the_threshold(self, session):
        """One firing down and back up is noise, not an incident."""
        self._failing(session, 2)
        assert alerts.evaluate(session, CONFIG, rules=("source_down",)) == []

    def test_a_week_long_outage_alerts_once(self, session):
        """The property that keeps the channel worth reading.

        Keyed on when the outage began, which does not move while it continues.
        """
        st = self._failing(session, 3, last_success=NOW - timedelta(days=1))
        for run in range(5):
            st.consecutive_failures = 3 + run
            session.commit()
            alerts.dispatch(session, alerts.evaluate(session, CONFIG, rules=("source_down",)), CONFIG)

        assert session.scalar(select(func.count()).select_from(m.Alert)) == 1

    def test_a_recovery_then_a_new_outage_alerts_again(self, session):
        """A new episode is genuinely new — dedupe must not suppress it."""
        st = self._failing(session, 3, last_success=NOW - timedelta(days=5))
        alerts.dispatch(session, alerts.evaluate(session, CONFIG, rules=("source_down",)), CONFIG)

        state_mod.record_success(st, {}, now=NOW)          # recovered
        st.consecutive_failures = 3                          # then went down again
        st.last_success_at = NOW
        session.commit()
        alerts.dispatch(session, alerts.evaluate(session, CONFIG, rules=("source_down",)), CONFIG)

        assert session.scalar(select(func.count()).select_from(m.Alert)) == 2

    def test_an_operator_disabled_source_never_alerts(self, session):
        st = self._failing(session, 9)
        st.disabled = True
        session.commit()
        assert alerts.evaluate(session, CONFIG, rules=("source_down",)) == []


class TestSystemRuleBudget:
    def test_a_run_breach_alerts(self, session):
        context = {"budget_breach": BudgetExceeded("per-run", 3.2, 3.0), "run_id": None}
        found = alerts.evaluate(session, CONFIG, context, rules=("budget_exceeded",))
        assert len(found) == 1
        assert found[0].severity == alerts.WARNING
        assert "3.0" in found[0].body or "3.00" in found[0].body

    def test_a_month_breach_is_more_severe(self, session):
        context = {"budget_breach": BudgetExceeded("per-month", 20.5, 20.0)}
        found = alerts.evaluate(session, CONFIG, context, rules=("budget_exceeded",))
        assert found[0].severity == alerts.CRITICAL

    def test_no_breach_no_alert(self, session):
        assert alerts.evaluate(session, CONFIG, {}, rules=("budget_exceeded",)) == []

    def test_a_month_breach_does_not_re_alert_every_run(self, session):
        """Keyed on the month, not the run: the ceiling stays hit until it resets."""
        context = {"budget_breach": BudgetExceeded("per-month", 20.5, 20.0)}
        for _ in range(3):
            run = m.PipelineRun(kind="scheduled", status="succeeded")
            session.add(run)
            session.flush()
            context["run_id"] = run.id
            alerts.dispatch(
                session, alerts.evaluate(session, CONFIG, context, rules=("budget_exceeded",)),
                CONFIG,
            )
        assert session.scalar(select(func.count()).select_from(m.Alert)) == 1


class TestSystemRuleDrift:
    def test_below_the_floor_alerts_as_a_system_failure(self, session):
        """Not a content alert: a degrading scorer is the pipeline breaking."""
        context = {"drift": {"mechanism_f1": 0.55, "compared": 6}, "snapshot_id": 7}
        found = alerts.evaluate(session, CONFIG, context, rules=("drift",))
        assert len(found) == 1
        assert found[0].kind == alerts.SYSTEM
        assert found[0].kind != alerts.CONTENT

    def test_at_or_above_the_floor_does_not_alert(self, session):
        context = {"drift": {"mechanism_f1": 0.95, "compared": 6}, "snapshot_id": 7}
        assert alerts.evaluate(session, CONFIG, context, rules=("drift",)) == []

    def test_a_measurement_that_did_not_run_is_not_drift(self, session):
        """Otherwise the alert fires every time the budget runs out."""
        context = {"drift": {"mechanism_f1": None, "compared": 0}, "snapshot_id": 7}
        assert alerts.evaluate(session, CONFIG, context, rules=("drift",)) == []


class TestContentRules:
    def test_a_high_band_article_alerts(self, session):
        article_with(session, band="high", score=66.7)
        session.commit()
        found = alerts.evaluate(session, CONFIG, rules=("high_band_item",))
        assert len(found) == 1
        assert found[0].kind == alerts.CONTENT
        assert found[0].severity == alerts.INFO

    def test_a_low_band_article_does_not(self, session):
        article_with(session, band="low", score=11.1)
        session.commit()
        assert alerts.evaluate(session, CONFIG, rules=("high_band_item",)) == []

    def test_an_article_alerts_once_not_once_per_run(self, session):
        """It stays in the rolling window for three months."""
        article_with(session)
        session.commit()
        for _ in range(3):
            alerts.dispatch(session, alerts.evaluate(session, CONFIG, rules=("high_band_item",)), CONFIG)
        assert session.scalar(select(func.count()).select_from(m.Alert)) == 1

    def test_one_alert_per_holding_not_per_route(self, session):
        """An article can reach one holding four ways. Nobody wants it named four times."""
        article, _ = article_with(session)
        session.add(m.Holding(isin="US1", name="NVIDIA", custodian_name="NVIDIA",
                              aliases=[], ticker="NVDA", weight_pct=5.0, ai_role="primary",
                              holdings_version=1, companies_version=1))
        session.flush()
        for route, via, strength in (("mechanism", "training_compute_up", 0.67),
                                     ("category", "accelerator", 0.60),
                                     ("named", "name:NVIDIA", 0.30)):
            session.add(m.Connection(article_id=article.id, isin="US1", route=route,
                                     via=via, direction="positive", strength=strength))
        session.commit()

        found = alerts.evaluate(session, CONFIG, rules=("holding_impact",))

        assert len(found) == 1
        # The strongest route is the one reported.
        assert found[0].payload["route"] == "mechanism"
        assert found[0].payload["strength"] == 0.67

    def test_a_weak_connection_does_not_alert(self, session):
        article, _ = article_with(session)
        session.add(m.Holding(isin="US1", name="NVIDIA", custodian_name="NVIDIA",
                              aliases=[], ticker="NVDA", weight_pct=5.0, ai_role="primary",
                              holdings_version=1, companies_version=1))
        session.flush()
        session.add(m.Connection(article_id=article.id, isin="US1", route="named",
                                 via="name:NVIDIA", direction="mixed", strength=0.30))
        session.commit()
        assert alerts.evaluate(session, CONFIG, rules=("holding_impact",)) == []


class TestTheTwoKindsStaySeparate:
    def test_system_alerts_sort_before_content(self, session):
        session.add(m.PipelineRun(kind="scheduled", status="failed", error="boom"))
        article_with(session)
        session.commit()

        found = alerts.evaluate(session, CONFIG)

        assert found[0].kind == alerts.SYSTEM
        assert {c.kind for c in found} == {alerts.SYSTEM, alerts.CONTENT}

    def test_recent_can_filter_to_one_kind(self, session):
        session.add(m.PipelineRun(kind="scheduled", status="failed", error="boom"))
        article_with(session)
        session.commit()
        alerts.dispatch(session, alerts.evaluate(session, CONFIG), CONFIG)

        assert {a["kind"] for a in alerts.recent(session, kind="system")} == {"system"}
        assert {a["kind"] for a in alerts.recent(session, kind="content")} == {"content"}


class TestDelivery:
    def test_a_dead_channel_records_the_failure_and_keeps_the_alert(self, session, monkeypatch):
        """The row is the record of truth; delivery is a courtesy."""
        session.add(m.PipelineRun(kind="scheduled", status="failed", error="boom"))
        session.commit()

        def broken(alert):
            raise ConnectionError("webhook unreachable")

        monkeypatch.setitem(alerts.CHANNELS, "broken", broken)
        stats = alerts.dispatch(
            session, alerts.evaluate(session, CONFIG, rules=("run_failed",)),
            CONFIG, channel="broken",
        )

        assert stats["failed"] == 1
        alert = session.scalars(select(m.Alert)).one()
        assert alert.sent_at is None
        assert "webhook unreachable" in alert.delivery_error

    def test_delivery_is_capped_but_recording_is_not(self, session):
        """A first run over an existing corpus must not fire 16 notifications."""
        for i in range(5):
            article_with(session, url=f"https://a/{i}", lab=f"lab{i}")
        session.commit()

        stats = alerts.dispatch(
            session, alerts.evaluate(session, CONFIG, rules=("high_band_item",)),
            {**CONFIG, "max_deliveries_per_run": 2},
        )

        assert stats["raised"] == 5
        assert stats["delivered"] == 2
        assert stats["suppressed"] == 3
        assert session.scalar(select(func.count()).select_from(m.Alert)) == 5
        suppressed = session.scalars(
            select(m.Alert).where(m.Alert.sent_at.is_(None))
        ).all()
        assert all("suppressed" in a.delivery_error for a in suppressed)

    def test_a_duplicate_is_counted_not_raised(self, session):
        session.add(m.PipelineRun(kind="scheduled", status="failed", error="boom"))
        session.commit()
        candidates = alerts.evaluate(session, CONFIG, rules=("run_failed",))

        alerts.dispatch(session, candidates, CONFIG)
        stats = alerts.dispatch(session, candidates, CONFIG)

        assert stats == {"raised": 0, "duplicate": 1, "delivered": 0,
                         "suppressed": 0, "failed": 0}

    def test_an_unknown_channel_raises_rather_than_dropping_alerts(self, session):
        with pytest.raises(ValueError, match="unknown alert channel"):
            alerts.dispatch(session, [], CONFIG, channel="carrier_pigeon")

    def test_a_webhook_with_no_url_is_a_misconfiguration(self, session, monkeypatch):
        """Not a silent no-op — that would look like a working alerter."""
        monkeypatch.delenv(alerts.WEBHOOK_ENV, raising=False)
        session.add(m.PipelineRun(kind="scheduled", status="failed", error="boom"))
        session.commit()

        stats = alerts.dispatch(
            session, alerts.evaluate(session, CONFIG, rules=("run_failed",)),
            CONFIG, channel="webhook",
        )

        assert stats["failed"] == 1
        assert alerts.WEBHOOK_ENV in session.scalars(select(m.Alert)).one().delivery_error


class TestTheRealConfig:
    def test_the_alerts_block_parses_and_is_complete(self):
        config = alerts.settings()
        for key in ("source_down_runs", "content_band", "drift_agreement_floor", "channel"):
            assert key in config
        assert config["channel"] in alerts.CHANNELS
        assert config["content_band"] in alerts.BANDS


class TestDedupeKeysSurviveARebuild:
    """Content alerts must key on identifiers that outlive the derived layer.

    `load_refs` wipes the whole clean layer on every load and `transform`
    rebuilds it, so every article gets a fresh autoincrement id each firing.
    Keying on the row id therefore invents new dedupe keys every run and
    re-raises every content alert forever — the exact failure dedupe exists to
    prevent, arriving by a different route. Measured live before the fix: 135
    alerts re-raised on the very next firing.
    """

    def _rebuild_clean_layer(self, session, url="https://a/1", lab="anthropic"):
        """What a load does: wipe the derived rows and build them again.

        The raw layer is upsert-only and survives, so only articles,
        classifications and connections are recreated — with fresh ids, which
        is the whole point.
        """
        session.query(m.Connection).delete()
        session.query(m.Classification).delete()
        session.query(m.Article).delete()
        session.commit()

        raw_id = session.scalar(select(m.RawArticle.id).where(m.RawArticle.url == url))
        article = m.Article(url=url, raw_article_id=raw_id, lab=lab, title="A title",
                            published_on=NOW.date(), text="body", text_source="full_text")
        session.add(article)
        session.flush()
        session.add(m.Classification(
            article_id=article.id, prompt_version="v7", scoring_version=4,
            event_type="frontier_model_release", summary="A summary", notable=True,
            notable_reason="", score=66.7, band="high", ai_score=0.0, ai_band="none",
        ))
        session.commit()
        return article, None

    def test_a_high_band_alert_is_not_re_raised_after_a_rebuild(self, session):
        article_with(session)
        session.commit()
        alerts.dispatch(session, alerts.evaluate(session, CONFIG, rules=("high_band_item",)), CONFIG)
        first = session.scalars(select(m.Alert)).one().dedupe_key

        self._rebuild_clean_layer(session)
        stats = alerts.dispatch(
            session, alerts.evaluate(session, CONFIG, rules=("high_band_item",)), CONFIG
        )

        assert stats["raised"] == 0
        assert stats["duplicate"] == 1
        assert session.scalars(select(m.Alert)).one().dedupe_key == first

    def test_the_key_is_the_url_not_the_row_id(self, session):
        article, _ = article_with(session)
        session.commit()
        candidate = alerts.evaluate(session, CONFIG, rules=("high_band_item",))[0]
        assert article.url in candidate.dedupe_key
        assert f":{article.id}:" not in candidate.dedupe_key

    def test_a_holding_impact_alert_survives_a_rebuild(self, session):
        article, _ = article_with(session)
        session.add(m.Holding(isin="US1", name="NVIDIA", custodian_name="NVIDIA",
                              aliases=[], ticker="NVDA", weight_pct=5.0, ai_role="primary",
                              holdings_version=1, companies_version=1))
        session.flush()
        session.add(m.Connection(article_id=article.id, isin="US1", route="mechanism",
                                 via="training_compute_up", direction="positive", strength=0.67))
        session.commit()
        alerts.dispatch(session, alerts.evaluate(session, CONFIG, rules=("holding_impact",)), CONFIG)

        rebuilt, _ = self._rebuild_clean_layer(session)
        session.add(m.Connection(article_id=rebuilt.id, isin="US1", route="mechanism",
                                 via="training_compute_up", direction="positive", strength=0.67))
        session.commit()

        stats = alerts.dispatch(
            session, alerts.evaluate(session, CONFIG, rules=("holding_impact",)), CONFIG
        )
        assert stats["raised"] == 0
        assert stats["duplicate"] == 1


class TestDriftAlertsOncePerEpisode:
    """One ongoing degradation is one alert, not one per firing.

    `drift.record` writes a fresh snapshot every run, so keying the alert on the
    snapshot id made it new every time — 14 warnings for a fortnight of one
    problem, the exact failure `source_down` avoids by keying on
    `last_success_at`. The analogue is the last snapshot that was *above* the
    floor: fixed while agreement stays down, different once it recovers.
    """

    def _measure(self, session, f1, prompt_version="v7"):
        from app.pipeline import drift as drift_mod

        metrics = {"mechanism_f1": f1, "compared": 6, "prompt_version": prompt_version}
        snapshot = drift_mod.record(session, metrics, prompt_version)
        session.commit()
        return alerts.dispatch(
            session,
            alerts.evaluate(session, CONFIG, {"drift": metrics, "snapshot_id": snapshot.id},
                            rules=("drift",)),
            CONFIG,
        )

    def test_a_sustained_dip_alerts_once(self, session):
        self._measure(session, 0.95)                    # healthy baseline
        first = self._measure(session, 0.55)            # degrades
        repeats = [self._measure(session, 0.54) for _ in range(4)]

        assert first["raised"] == 1
        assert all(r["raised"] == 0 and r["duplicate"] == 1 for r in repeats)
        assert session.scalar(
            select(func.count()).select_from(m.Alert).where(m.Alert.rule == "drift")
        ) == 1

    def test_a_recovery_then_a_new_dip_alerts_again(self, session):
        self._measure(session, 0.95)
        self._measure(session, 0.55)
        self._measure(session, 0.95)                    # recovered
        again = self._measure(session, 0.50)            # and slipped again

        assert again["raised"] == 1
        assert session.scalar(
            select(func.count()).select_from(m.Alert).where(m.Alert.rule == "drift")
        ) == 2

    def test_staying_healthy_never_alerts(self, session):
        for f1 in (0.95, 0.93, 0.91):
            assert self._measure(session, f1)["raised"] == 0

class TestDriftMeasuringNothingIsAnIncident:
    """The silent failure this catches: the gold-set check quietly not running.

    `below_floor` correctly declines to call a zero-comparison measurement
    "drift" — that would fire every time the budget ran out. But nothing else
    fired either, so a check whose every call failed produced total silence: the
    run reported `succeeded` and `mechanism_f1` was recorded as null.

    Found live on Render, where `ANTHROPIC_API_KEY` was unset on the API service:
    all 20 calls returned "Could not resolve authentication method", `compared`
    was 0, and no alert of any kind was raised (D45).
    """

    def _metrics(self, **over):
        base = {
            "sample": [str(i) for i in range(20)],
            "compared": 0, "skipped": 0,
            "errors": [{"id": "12", "error": "Could not resolve authentication method"}],
            "mechanism_f1": None, "cost_usd": 0.0,
        }
        base.update(over)
        return base

    def test_every_call_failing_raises_a_critical_system_alert(self):
        out = alerts.drift_unavailable(None, {}, {"drift": self._metrics(), "run_id": 1})
        assert len(out) == 1
        assert (out[0].kind, out[0].severity) == ("system", "critical")
        assert "measured nothing" in out[0].subject
        assert "authentication" in out[0].body

    def test_a_measurement_that_worked_raises_nothing(self):
        out = alerts.drift_unavailable(
            None, {}, {"drift": self._metrics(compared=20, errors=[], mechanism_f1=0.86)}
        )
        assert out == []

    def test_drift_not_running_this_firing_raises_nothing(self):
        """Cadence or a dry run. Not a failure."""
        assert alerts.drift_unavailable(None, {}, {}) == []
        assert alerts.drift_unavailable(None, {}, {"drift": None}) == []

    def test_a_budget_stop_is_left_to_budget_exceeded(self):
        """Two alerts for one cause trains people to mute both."""
        out = alerts.drift_unavailable(
            None, {}, {"drift": self._metrics(skipped=20, errors=[])}
        )
        assert out == []

    def test_one_outage_is_one_alert_not_one_a_night(self):
        a = alerts.drift_unavailable(None, {}, {"drift": self._metrics(), "run_id": 1})
        b = alerts.drift_unavailable(None, {}, {"drift": self._metrics(), "run_id": 2})
        assert a[0].dedupe_key == b[0].dedupe_key

    def test_the_rule_is_registered(self):
        """A rule absent from RULES never runs, however well it is written."""
        assert "drift_unavailable" in alerts.RULES
