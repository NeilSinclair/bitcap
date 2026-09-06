"""The operational read model: runs, sources, alerts, drift, spend.

The silent failure this whole surface exists to catch: the pipeline recording
its own health into tables nobody can see. `pipeline_runs`, `source_state` and
`alerts` were all being written before any of this existed, and none of it was
reachable outside psql — which makes "system-failure alerting distinct from
content alerting" true in the schema and false in practice.

The specific silent failures these tests catch:

* **A failed source invisible in the run history.** A firing that lost one lab
  is `succeeded` by design (D27), so a view showing only the run row reports a
  green night on a broken source.
* **`confirmed` and `confirmed_org_wide` flattened**, or failing sources buried
  under healthy ones, so nobody reads the page that exists to be scanned.
* **Drift compared across prompt versions**, which silently compares two
  different questions and makes a version bump look like degradation.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api import ops
from app import models as m
from app.db import create_all
from app import digest as digest_mod
from app.cli import PROMPT_VERSION
from app.pipeline import drift as drift_mod

NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    # StaticPool, unlike the default for :memory:, shares one connection across
    # threads. FastAPI's TestClient serves each request on a worker thread, and
    # a per-thread connection to an in-memory database is a *different* empty
    # database — the endpoint tests would fail with "no such table".
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_all(engine)
    with Session(engine) as s:
        yield s


def a_run(session, kind="scheduled", status="succeeded", cost=0.0):
    run = m.PipelineRun(kind=kind, status=status, cost_usd=cost)
    session.add(run)
    session.flush()
    return run


class TestRunHistory:
    def test_newest_first(self, session):
        for _ in range(3):
            a_run(session)
        session.commit()
        history = ops.run_history(session)
        assert [r["id"] for r in history] == sorted((r["id"] for r in history), reverse=True)

    def test_a_failed_source_is_visible_on_a_succeeded_run(self, session):
        """The whole reason run_sources is joined in. D27: a dead source is not
        a dead run, so the run row alone reports a green night."""
        run = a_run(session, status="succeeded")
        session.add_all([
            m.RunSource(run_id=run.id, leg="announcements", source_id="anthropic",
                        status="succeeded", items_seen=35),
            m.RunSource(run_id=run.id, leg="announcements", source_id="openai",
                        status="failed", error="503 from the archive"),
        ])
        session.commit()

        entry = ops.run_history(session)[0]

        assert entry["status"] == "succeeded"
        assert entry["sources_failed"] == 1
        assert any("503" in (s["error"] or "") for s in entry["sources"])

    def test_a_run_with_no_sources_still_renders(self, session):
        a_run(session, kind="load")
        session.commit()
        assert ops.run_history(session)[0]["sources"] == []

    def test_empty_history_is_an_empty_list_not_an_error(self, session):
        assert ops.run_history(session) == []

    def test_sources_are_attributed_to_the_right_run(self, session):
        first, second = a_run(session), a_run(session)
        session.add_all([
            m.RunSource(run_id=first.id, leg="papers", source_id="mistral", status="succeeded"),
            m.RunSource(run_id=second.id, leg="github", source_id="openai", status="failed"),
        ])
        session.commit()

        by_id = {r["id"]: r for r in ops.run_history(session)}

        assert [s["source_id"] for s in by_id[first.id]["sources"]] == ["mistral"]
        assert [s["leg"] for s in by_id[second.id]["sources"]] == ["github"]


class TestSourceStates:
    def test_worst_first(self, session):
        """A page meant to be scanned must put the broken thing at the top."""
        session.add_all([
            m.SourceState(leg="announcements", source_id="healthy", consecutive_failures=0),
            m.SourceState(leg="announcements", source_id="broken", consecutive_failures=5),
            m.SourceState(leg="papers", source_id="wobbly", consecutive_failures=2),
        ])
        session.commit()

        states = ops.source_states(session)

        assert [s["source_id"] for s in states] == ["broken", "wobbly", "healthy"]

    def test_a_disabled_source_is_distinguishable_from_a_broken_one(self, session):
        session.add(m.SourceState(leg="papers", source_id="xai", disabled=True))
        session.commit()
        state = ops.source_states(session)[0]
        assert state["disabled"] is True
        assert state["consecutive_failures"] == 0


class TestHealth:
    def test_reports_the_latest_run_and_the_month_spend(self, session):
        a_run(session, cost=1.08)
        session.add(m.RawCost(
            url="https://x/1", model="claude-sonnet-5", input_tokens=1, output_tokens=1,
            usd=1.08, seconds=1.0,
            at=f"{datetime.now(timezone.utc):%Y-%m}-01T00:00:00+00:00",
        ))
        session.commit()

        health = ops.health(session)

        assert health["latest_run"]["cost_usd"] == 1.08
        assert health["spend"]["month_usd"] == pytest.approx(1.08)
        assert health["spend"]["month_limit"] > 0

    def test_failing_sources_are_called_out_separately(self, session):
        session.add_all([
            m.SourceState(leg="announcements", source_id="ok", consecutive_failures=0),
            m.SourceState(leg="announcements", source_id="bad", consecutive_failures=3),
        ])
        session.commit()

        health = ops.health(session)

        assert len(health["sources"]) == 2
        assert [s["source_id"] for s in health["sources_failing"]] == ["bad"]

    def test_counts_distinguish_a_quiet_run_from_an_empty_database(self, session):
        health = ops.health(session)
        assert health["counts"]["articles"] == 0
        assert set(health["counts"]) == {
            "articles", "classifications", "connections", "people", "unresolved_items"
        }

    def test_an_empty_database_reports_no_run_rather_than_failing(self, session):
        assert ops.health(session)["latest_run"] is None


class TestDriftHistory:
    def test_defaults_to_one_prompt_version(self, session):
        """Comparing agreement across versions compares two different questions."""
        drift_mod.record(session, {"mechanism_f1": 0.55}, "v6")
        drift_mod.record(session, {"mechanism_f1": 0.95}, "v7")
        session.commit()

        assert [p["mechanism_f1"] for p in drift_mod.history(session, "v7")] == [0.95]

    def test_oldest_first_for_a_chart(self, session):
        for f1 in (0.80, 0.90, 0.95):
            drift_mod.record(session, {"mechanism_f1": f1}, "v7")
        session.commit()
        assert [p["mechanism_f1"] for p in drift_mod.history(session, "v7")] == [0.80, 0.90, 0.95]


class TestEndpoints:
    """Wiring only — the query logic is covered above.

    Uses FastAPI's TestClient against a temporary sqlite database, so a broken
    route signature or a bad status code is caught without a live Postgres.
    """

    @pytest.fixture()
    def client(self, session, monkeypatch):
        from fastapi.testclient import TestClient

        import api.main as main
        from api import auth

        # Each request gets its own Session over the shared connection: the
        # endpoints close what they are handed, which would otherwise close
        # the fixture's session out from under the rest of the test.
        monkeypatch.setattr(main, "get_session", lambda engine: Session(session.get_bind()))

        # The whole API is behind a session token now, so a client that does not
        # send one gets 401 from every route and tests nothing about the route
        # it meant to exercise. Signed in here; `tests/test_api_pipeline.py` is
        # where the gate itself is tested.
        monkeypatch.setenv("AUTH_SECRET", "test-secret")
        client = TestClient(main.app)
        client.headers["Authorization"] = f"Bearer {auth.issue_token('ops@test')}"
        return client

    def test_runs_endpoint(self, client, session):
        run = a_run(session)
        session.add(m.RunSource(run_id=run.id, leg="papers", source_id="mistral",
                                status="failed", error="boom"))
        session.commit()

        body = client.get("/api/runs").json()

        assert body[0]["sources_failed"] == 1

    def test_alerts_endpoint_filters_by_kind(self, client, session):
        session.add_all([
            m.Alert(kind="system", rule="source_down", severity="warning", subject="s",
                    body="b", dedupe_key="k1"),
            m.Alert(kind="content", rule="high_band_item", severity="info", subject="c",
                    body="b", dedupe_key="k2"),
        ])
        session.commit()

        assert len(client.get("/api/alerts").json()) == 2
        assert {a["kind"] for a in client.get("/api/alerts?kind=system").json()} == {"system"}

    def test_an_unknown_alert_kind_is_rejected(self, client):
        """Silently returning everything would look like a working filter."""
        assert client.get("/api/alerts?kind=urgent").status_code == 400

    def test_acknowledging_clears_the_badge_without_deleting_the_history(self, client, session):
        """The badge is the only thing that gets the ops page opened.

        Windowed alone, one transient outage held it red for seven days, and a
        badge that is red on a healthy pipeline is a badge nobody reads. The
        failure this catches is the obvious over-fix: clearing by deleting, so
        the alert history — the record of what broke and when — quietly empties
        every time someone tidies the badge.
        """
        session.add(m.Alert(kind="system", rule="source_down", severity="warning",
                            subject="s", body="b", dedupe_key="k1"))
        session.commit()
        assert client.get("/api/health").json()["unacknowledged_system_alerts"] == 1

        assert client.post("/api/alerts/acknowledge").json() == {
            "acknowledged": 1, "kind": "system",
        }

        assert client.get("/api/health").json()["unacknowledged_system_alerts"] == 0
        rows = client.get("/api/alerts?kind=system").json()
        assert len(rows) == 1 and rows[0]["acknowledged_at"]

    def test_acknowledging_leaves_content_alerts_alone(self, client, session):
        """The two kinds are the distinction the alerting rests on. Clearing the
        pipeline-health badge must not also mark the findings as seen."""
        session.add_all([
            m.Alert(kind="system", rule="source_down", severity="warning",
                    subject="s", body="b", dedupe_key="k1"),
            m.Alert(kind="content", rule="high_band_item", severity="info",
                    subject="c", body="b", dedupe_key="k2"),
        ])
        session.commit()

        client.post("/api/alerts/acknowledge")

        assert client.get("/api/alerts?kind=content").json()[0]["acknowledged_at"] is None

    def test_a_live_outage_turns_the_badge_red_again(self, client, session):
        """What makes the button safe to expose, driven through the real rules.

        An earlier version of this test hand-wrote two dedupe keys
        (`source_down:mistral:1` and `...:2`) and asserted the badge reddened.
        It passed while the product was broken: two different keys only occur
        when an outage *ended and restarted*, so it tested a recovery, never the
        dangerous case. `source_down` keys on `last_success_at`, which holds
        still while a fault continues, so an ongoing outage writes no new row —
        acknowledging once would have greened the badge for the whole incident.
        Driving `evaluate` + `dispatch` uses whatever key the rule really emits.
        """
        from app.pipeline import alerts as alerts_mod
        from app.pipeline import state as state_mod

        st = state_mod.load(session, "announcements", "openai")
        st.consecutive_failures = 5
        st.last_success_at = datetime(2026, 8, 29, tzinfo=timezone.utc)
        st.last_error = "503"
        session.commit()

        def firing():
            alerts_mod.dispatch(
                session,
                alerts_mod.evaluate(session, alerts_mod.settings(), rules=("source_down",)),
                alerts_mod.settings(),
            )
            session.commit()

        firing()
        assert client.get("/api/health").json()["unacknowledged_system_alerts"] == 1
        client.post("/api/alerts/acknowledge")
        assert client.get("/api/health").json()["unacknowledged_system_alerts"] == 0

        firing()   # still down, and the rule emits the same episode key

        assert client.get("/api/health").json()["unacknowledged_system_alerts"] == 1, (
            "acknowledging silenced the badge for an outage that never ended"
        )

    def test_acknowledging_twice_is_a_no_op(self, client, session):
        session.add(m.Alert(kind="system", rule="source_down", severity="warning",
                            subject="s", body="b", dedupe_key="k1"))
        session.commit()
        client.post("/api/alerts/acknowledge")

        assert client.post("/api/alerts/acknowledge").json()["acknowledged"] == 0

    def test_content_alerts_cannot_be_acknowledged(self, client, session):
        """Not exposed, because it could not work. Content rules regenerate
        every firing, so acknowledging findings would be undone by the next cron
        run; and the badge does not count them, so it would clear nothing."""
        session.add(m.Alert(kind="content", rule="high_band_item", severity="info",
                            subject="c", body="b", dedupe_key="k2"))
        session.commit()

        client.post("/api/alerts/acknowledge?kind=content")   # ignored, not honoured

        assert client.get("/api/alerts?kind=content").json()[0]["acknowledged_at"] is None

    def test_health_endpoint(self, client, session):
        a_run(session)
        session.commit()
        body = client.get("/api/health").json()
        assert "spend" in body and "counts" in body

    def test_the_health_payload_carries_both_badge_keys(self, client, session):
        """The rename has to survive a staggered deploy.

        `render.yaml` ships the API and the static frontend as two independent
        services, so a blueprint sync leaves a window where one is live and the
        other is not. A browser holding the old bundle reads
        `recent_system_alerts`; drop that key and it gets `undefined`, falls
        through its `?? 0`, and paints the Health badge green while system
        alerts are outstanding — a false green on the one indicator this feature
        exists to keep honest, and it fails in the safe-looking direction, which
        is why nobody would notice.
        """
        session.add(m.Alert(kind="system", rule="source_down", severity="warning",
                            subject="s", body="b", dedupe_key="k1"))
        session.commit()
        body = client.get("/api/health").json()

        assert body["unacknowledged_system_alerts"] == 1
        assert body["recent_system_alerts"] == 1, "old bundles would read a green badge"

    def test_the_digest_preview_window_ends_now(self, client, session):
        """The live view must not be a period behind, and one line delivers it.

        `quantise=False` at the endpoint is the whole of the fix for "the digest
        says 48 hours to the 3rd while I am reading it on the 5th": published
        editions snap to a fixed grid, so the newest window a quantised preview
        can name is the last one that *closed*. Nothing else asserts that line
        exists, so deleting it would have gone green — the page would just go
        quietly stale again, which is exactly how it shipped the first time.
        """
        body = client.get("/api/digests/preview?kind=investment").json()
        start = datetime.fromisoformat(body["window_start"])
        end = datetime.fromisoformat(body["window_end"])
        now = datetime.now(timezone.utc)

        assert abs((now - end).total_seconds()) < 300, (
            f"preview window ends {end}, not ~now — the grid is back"
        )
        # And it is still the configured width, not an unbounded lookback.
        # `preview_window_hours`, not `window_hours` — since D79 the live view
        # and the published archive are deliberately different widths, and this
        # endpoint serves the live one.
        assert (end - start) == timedelta(
            hours=digest_mod.settings()["preview_window_hours"])

    def test_the_preview_is_wider_than_a_published_edition(self, client, session):
        """The shape D79 exists to make permanent, asserted end to end.

        The archive is a daily grid and the landing view is a rolling week. If
        this ever comes back equal, the two surfaces are the same report twice
        and the dropdown has stopped being an archive of anything narrower.
        """
        body = client.get("/api/digests/preview?kind=investment").json()
        span = (datetime.fromisoformat(body["window_end"])
                - datetime.fromisoformat(body["window_start"]))

        assert span > timedelta(hours=digest_mod.settings()["window_hours"])

    def test_the_hours_override_widens_the_preview(self, client, session):
        """`?hours=` is how a reader asks for a different span without changing
        anything published — the one route to "what mattered in the last 48
        hours" while the default view is a week.

        It writes into the config dict the builder reads, so it is exactly the
        kind of thing that survives a key rename as a silent no-op: the endpoint
        would keep returning 200 with the default width and nothing would say
        the parameter had stopped working. Nothing covered it before D79.
        """
        body = client.get("/api/digests/preview?kind=investment&hours=48").json()
        span = (datetime.fromisoformat(body["window_end"])
                - datetime.fromisoformat(body["window_start"]))

        assert span == timedelta(hours=48), (
            "the hours override did not reach the rolling window — check it "
            "writes preview_window_hours, not window_hours")

    def test_the_hours_override_is_clamped_not_trusted(self, client, session):
        """A query parameter is user input. The clamp bounds it to [1h, 90d] so
        a hand-typed `hours=0` cannot produce an empty window and a huge one
        cannot walk the whole corpus."""
        for asked, expected in ((0, 1), (24 * 400, 24 * 90)):
            body = client.get(
                f"/api/digests/preview?kind=investment&hours={asked}").json()
            span = (datetime.fromisoformat(body["window_end"])
                    - datetime.fromisoformat(body["window_start"]))
            assert span == timedelta(hours=expected)

    def test_drift_endpoint(self, client, session):
        # The endpoint defaults to the current prompt version, so the snapshot
        # has to be written at that version rather than a literal that goes
        # stale on the next bump.
        drift_mod.record(session, {"mechanism_f1": 0.94, "compared": 6}, PROMPT_VERSION)
        session.commit()
        body = client.get("/api/drift").json()
        assert body[0]["mechanism_f1"] == 0.94

    def test_limits_are_clamped(self, client, session):
        """An unbounded limit is a denial-of-service on a shared database."""
        for _ in range(3):
            a_run(session)
        session.commit()
        assert client.get("/api/runs?limit=100000").status_code == 200
        assert client.get("/api/runs?limit=0").status_code == 200
