"""Two kinds of alert, one dispatcher, and a dedupe key that does the real work.

CLAUDE.md requires system-failure alerting **distinct from** content alerting,
and the distinction is not cosmetic. A `system` alert says the pipeline is
broken and someone has to fix it. A `content` alert says the pipeline is working
and found something. They share a table because they share a lifecycle — raise,
record, deliver — and nothing else; every consumer filters on `kind`.

**The dedupe key is the design.** Rules are re-evaluated on every firing, so a
naive implementation alerts about the same dead source every night for a week
and trains everyone to ignore it. Each rule therefore keys on the thing that
identifies the *episode* rather than the check:

* `source_down` keys on the source's `last_success_at` — the moment the outage
  began. That value does not change while the source stays down, so one outage
  produces one alert however many runs it spans, and a recovery followed by a
  new failure produces a genuinely new one.
* Content rules key on the item, so an article alerts once ever, not once per
  run for as long as it stays in the rolling window.
* `run_failed` keys on the run id — each failed run is a distinct event.
* `drift` keys on the last snapshot that was *above* the floor, which is the
  episode's start and does not move while agreement stays down. Keying on the
  measurement re-alerted every firing, since a snapshot is written each time.

Delivery is capped per run. Recording is not. Everything raised is written and
visible; beyond the cap it is simply not pushed, with the reason recorded —
which is what stops the first run (16 high-band articles already in the corpus)
from firing sixteen notifications, without silently dropping fifteen of them.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.models import utcnow
from app.pipeline import state as state_mod

CONFIG = Path(__file__).parent.parent.parent / "config" / "pipeline.yaml"

SYSTEM, CONTENT = "system", "content"
INFO, WARNING, CRITICAL = "info", "warning", "critical"

# Ascending, so "at or above `content_band`" is an index comparison.
BANDS = ("none", "low", "medium", "high")

WEBHOOK_ENV = "ALERT_WEBHOOK_URL"
DELIVERY_TIMEOUT_S = 10


@dataclass(frozen=True)
class Candidate:
    """An alert a rule wants raised, before dedupe decides whether it is new."""

    kind: str
    rule: str
    severity: str
    subject: str
    body: str
    dedupe_key: str
    payload: dict = field(default_factory=dict)
    run_id: int | None = None


def settings(path: Path = CONFIG) -> dict:
    """The `alerts` block of config/pipeline.yaml."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))["alerts"]


# --------------------------------------------------------------------------
# System rules — the pipeline is broken
# --------------------------------------------------------------------------

def run_failed(session: Session, config: dict, context: dict) -> list[Candidate]:
    """Runs that failed and have not been alerted on.

    This is the standing query `app/runs.py` was written around
    (``status='failed' AND alerted_at IS NULL``) and which nothing has ever
    executed until now.
    """
    runs = session.scalars(
        select(m.PipelineRun).where(
            m.PipelineRun.status == "failed", m.PipelineRun.alerted_at.is_(None)
        )
    ).all()
    return [
        Candidate(
            kind=SYSTEM, rule="run_failed", severity=CRITICAL,
            subject=f"Pipeline run {run.id} ({run.kind}) failed",
            body=(run.error or "no error recorded")[:2000],
            dedupe_key=f"run_failed:{run.id}",
            payload={"run_id": run.id, "kind": run.kind, "stats": run.stats or {}},
            run_id=run.id,
        )
        for run in runs
    ]


def source_down(session: Session, config: dict, context: dict) -> list[Candidate]:
    """Sources that have failed `source_down_runs` scheduled runs in a row.

    One firing down and back up is noise and the next run's own per-request
    retries handle it; N in a row is an incident (planning.md §4b).

    Keyed on `last_success_at`, which is when the outage started and does not
    move while it continues. That is what makes a week-long outage one alert.
    """
    threshold = int(config.get("source_down_runs", 3))
    out = []
    for st in state_mod.failing(session, threshold):
        since = st.last_success_at.isoformat() if st.last_success_at else "never"
        out.append(Candidate(
            kind=SYSTEM, rule="source_down", severity=WARNING,
            subject=f"{st.leg}/{st.source_id} has failed {st.consecutive_failures} runs",
            body=(
                f"Last succeeded: {since}. "
                f"Last error: {(st.last_error or 'none recorded')[:500]}"
            ),
            dedupe_key=f"source_down:{st.leg}:{st.source_id}:{since}",
            payload={
                "leg": st.leg, "source_id": st.source_id,
                "consecutive_failures": st.consecutive_failures,
                "last_success_at": since,
            },
            run_id=context.get("run_id"),
        ))
    return out


def budget_exceeded(session: Session, config: dict, context: dict) -> list[Candidate]:
    """The run hit a spending ceiling and stopped classifying.

    Not derived from the database: the breach is a fact about the run in
    progress, handed in by the worker as `context["budget_breach"]`.
    """
    breach = context.get("budget_breach")
    if breach is None:
        return []
    run_id = context.get("run_id")
    # A per-run breach is this run's problem; a per-month breach is the month's,
    # so it must not re-alert on every subsequent run in the same month.
    scope_key = (
        f"run:{run_id}" if breach.scope == "per-run"
        else datetime.now(timezone.utc).strftime("%Y-%m")
    )
    return [Candidate(
        kind=SYSTEM, rule="budget_exceeded",
        severity=WARNING if breach.scope == "per-run" else CRITICAL,
        subject=f"{breach.scope} LLM budget exceeded",
        body=(
            f"${breach.spent:.4f} spent against a ${breach.limit:.2f} ceiling. "
            "Classification stopped; ingested data was still committed."
        ),
        dedupe_key=f"budget_exceeded:{breach.scope}:{scope_key}",
        payload={"scope": breach.scope, "spent": breach.spent, "limit": breach.limit,
                 "skipped": context.get("skipped_for_budget")},
        run_id=run_id,
    )]


def prompt_version_of(context: dict) -> str:
    """Which classifier the drift alert is about; versions are not comparable."""
    return str((context.get("drift") or {}).get("prompt_version") or "current")


def drift(session: Session, config: dict, context: dict) -> list[Candidate]:
    """Classifier agreement fell below the floor.

    A **system** alert, deliberately. A scorer quietly becoming less consistent
    is the pipeline degrading, not a finding about the world — it is precisely
    the silent failure this project exists to catch (planning.md §6a).
    """
    from app.pipeline import drift as drift_mod

    metrics = context.get("drift")
    if not metrics:
        return []
    floor = float(config.get("drift_agreement_floor", 0.8))
    if not drift_mod.below_floor(metrics, floor):
        return []
    # Keyed on the episode, not the measurement. `record()` writes a fresh
    # snapshot every firing, so keying on its id re-alerted nightly for one
    # ongoing degradation -- the failure `source_down` avoids by keying on
    # `last_success_at`. The analogue here is the last snapshot that was *above*
    # the floor: it does not move while agreement stays down, and it changes the
    # moment the scorer recovers and slips again.
    last_ok = session.scalar(
        select(m.GoldSnapshot.id)
        .where(m.GoldSnapshot.metrics["mechanism_f1"].as_float() >= floor)
        .order_by(m.GoldSnapshot.id.desc()).limit(1)
    ) if session is not None else None
    episode = last_ok if last_ok is not None else "since-first-measurement"
    return [Candidate(
        kind=SYSTEM, rule="drift", severity=WARNING,
        subject=f"Classifier agreement {metrics['mechanism_f1']:.2f} is below {floor:.2f}",
        body=(
            f"Mechanism micro-F1 {metrics['mechanism_f1']:.3f} over "
            f"{metrics.get('compared')} gold items. Mechanisms gate every "
            "non-zero investment score, so this is score drift."
        ),
        dedupe_key=f"drift:{prompt_version_of(context)}:since:{episode}",
        payload=metrics,
        run_id=context.get("run_id"),
    )]


def drift_unavailable(session: Session, config: dict, context: dict) -> list[Candidate]:
    """The drift check ran and measured nothing.

    `below_floor` is right that a measurement which compared zero items is not
    drift — reporting it as drift would fire the alert every time the budget ran
    out. But nothing else fired either, so a check whose every call failed was
    total silence: the run reported `succeeded`, the snapshot recorded
    `mechanism_f1: null`, and the one thing standing between a prompt regression
    and the register had quietly stopped running.

    Found on the deployed API, where `ANTHROPIC_API_KEY` was unset: all 20 calls
    returned "Could not resolve authentication method", `compared` was 0, and no
    alert of any kind was raised (docs/decisions.md D45).

    A **system** alert and a `critical` one. "The scorer drifted" is a warning
    that invites a look; "the scorer is not being checked at all" means every
    later firing is unverified, and it stays true until somebody acts.
    """
    metrics = context.get("drift")
    if not metrics:
        # Drift did not run this firing — cadence, or a dry run. Not a failure.
        return []
    if metrics.get("compared"):
        return []

    errors = metrics.get("errors") or []
    skipped = metrics.get("skipped") or 0
    sample = len(metrics.get("sample") or [])
    if skipped and not errors:
        # Every item skipped for budget. `budget_exceeded` already reports that
        # and says why; two alerts for one cause trains people to mute both.
        return []

    reason = errors[0].get("error", "") if errors else "no reason recorded"
    return [Candidate(
        kind=SYSTEM, rule="drift_unavailable", severity=CRITICAL,
        subject=f"Gold-set check measured nothing ({len(errors)}/{sample} calls failed)",
        body=(
            f"The drift check compared 0 of {sample} gold articles, so the "
            f"classifier is running unverified. First error: {reason[:300]}"
        ),
        # Keyed on the prompt version rather than the run, so one outage is one
        # alert rather than one a night — the same reasoning as `drift` above.
        dedupe_key=f"drift_unavailable:{prompt_version_of(context)}",
        payload=metrics,
        run_id=context.get("run_id"),
    )]


# --------------------------------------------------------------------------
# Content rules — the pipeline found something
# --------------------------------------------------------------------------

def _recent_enough(config: dict):
    """Publication-age bound shared by both content rules.

    A content alert is a claim that a lab published something worth seeing now.
    Neither rule was bounded by age, which was harmless while every corpus was a
    rolling window and stopped being harmless the moment a backfill landed: the
    papers leg carries documents back to 2023, several correctly scoring 100.

    Bounds `published_on`, not discovery. Alerting on everything found tonight
    would fire on the whole backfill at once, which is the same problem wearing
    a different hat.

    Args:
        config: The `alerts` block of config/pipeline.yaml.

    Returns:
        A SQLAlchemy criterion for use in a `.where()`.
    """
    days = int(config.get("content_max_age_days", 120))
    return m.Article.published_on >= (date.today() - timedelta(days=days))


def high_band_items(session: Session, config: dict, context: dict) -> list[Candidate]:
    """Newly classified articles at or above the configured band.

    Keyed on the article **URL**, not its row id. `articles` is derived: every
    load wipes the clean layer and rebuilds it, handing every article a fresh
    autoincrement id. Keying on the id therefore invents a new dedupe key on
    every firing and re-raises every content alert forever — measured live,
    135 alerts re-raised on the very next run. The URL is the one identifier
    that survives the rebuild, and it is also the citation.
    """
    floor = config.get("content_band", "high")
    wanted = set(BANDS[BANDS.index(floor):]) if floor in BANDS else {floor}

    rows = session.execute(
        select(m.Article, m.Classification)
        .join(m.Classification, m.Classification.article_id == m.Article.id)
        .where(m.Classification.band.in_(wanted))
        .where(_recent_enough(config))
        .order_by(m.Classification.score.desc())
    ).all()

    return [
        Candidate(
            kind=CONTENT, rule="high_band_item", severity=INFO,
            subject=f"[{article.lab}] {article.title}"[:300],
            body=f"{cls.summary}\n\nScore {cls.score:.1f} ({cls.band}). {article.url}",
            dedupe_key=f"high_band:{article.url}:{cls.prompt_version}",
            payload={
                "article_id": article.id, "lab": article.lab, "url": article.url,
                "score": cls.score, "band": cls.band,
                "published_on": str(article.published_on),
            },
            run_id=context.get("run_id"),
        )
        for article, cls in rows
    ]


def holding_impact(session: Session, config: dict, context: dict) -> list[Candidate]:
    """Article-to-holding links above a strength threshold.

    One alert per (article, holding) rather than per connection row: an article
    can reach the same holding by several routes, and a reader does not want the
    same position named four times for one event.

    Keyed on the article URL and the ISIN, both of which survive the rebuild
    that `load_refs` performs on every load. Row ids do not — see
    :func:`high_band_items`.
    """
    threshold = float(config.get("content_min_strength", 0.5))
    rows = session.execute(
        select(m.Connection, m.Article, m.Holding)
        .join(m.Article, m.Article.id == m.Connection.article_id)
        .join(m.Holding, m.Holding.isin == m.Connection.isin)
        .where(m.Connection.strength >= threshold)
        .where(_recent_enough(config))
        .order_by(m.Connection.strength.desc())
    ).all()

    best: dict[tuple[int, str], tuple] = {}
    for conn, article, holding in rows:
        key = (conn.article_id, conn.isin)
        if key not in best:  # rows arrive strongest-first
            best[key] = (conn, article, holding)

    return [
        Candidate(
            kind=CONTENT, rule="holding_impact", severity=INFO,
            subject=f"{holding.name}: {article.title}"[:300],
            body=(
                f"{conn.direction} via {conn.route} ({conn.via}), "
                f"strength {conn.strength:.2f}\n"
                f"Evidence: {(conn.article_quote or conn.holding_why or '')[:400]}\n"
                f"{article.url}"
            ),
            dedupe_key=f"holding_impact:{article.url}:{conn.isin}",
            payload={
                "article_id": conn.article_id, "isin": conn.isin,
                "holding": holding.name, "ticker": holding.ticker,
                "route": conn.route, "via": conn.via,
                "direction": conn.direction, "strength": conn.strength,
            },
            run_id=context.get("run_id"),
        )
        for conn, article, holding in best.values()
    ]


def extraction_downgraded(session: Session, config: dict, context: dict) -> list[Candidate]:
    """A lab's abstracts stopped coming from the element config names.

    Extraction falls back through the weaker strategies so one lab changing its
    page shape degrades rather than dropping papers out of the register. That
    resilience is also the hazard: `_lead_section` returns the whole document
    when it finds no `<article>`, and arXiv's `/abs/` furniture clears the
    200-character floor comfortably. Renaming one CSS class would land every new
    DeepSeek paper as navigation text, scoring zero on both axes, with
    `unresolved` at zero and the ingest counts looking healthy.

    A SYSTEM alert, not a content one: nothing has been learned about the world.
    Keyed on lab and strategy pair, so one page-shape change is one alert rather
    than one per firing.

    Args:
        session: Open session, unused -- this reads the run's own counts.
        config: The `alerts` block.
        context: Needs `paper_extraction`, a list of
            ``{lab, configured, actual, n}`` rows from the landing phase.

    Returns:
        One candidate per (lab, configured, actual) that disagrees.
    """
    out = []
    for row in context.get("paper_extraction") or []:
        if row["configured"] == row["actual"]:
            continue
        out.append(Candidate(
            kind=SYSTEM, rule="extraction_downgraded", severity=WARNING,
            subject=f"{row['lab']}: abstracts now extracted by {row['actual']}"[:300],
            body=(f"config/papers_sources.yaml names `{row['configured']}` for "
                  f"{row['lab']}, but {row['n']} paper(s) fell back to "
                  f"`{row['actual']}`. The page shape has probably changed; the "
                  f"text is still being scored, so check it is not furniture."),
            dedupe_key=f"extraction_downgraded:{row['lab']}:"
                       f"{row['configured']}:{row['actual']}",
            payload=row,
            run_id=context.get("run_id"),
        ))
    return out


RULES = {
    "run_failed": run_failed,
    "extraction_downgraded": extraction_downgraded,
    "source_down": source_down,
    "budget_exceeded": budget_exceeded,
    "drift": drift,
    "drift_unavailable": drift_unavailable,
    "high_band_item": high_band_items,
    "holding_impact": holding_impact,
}


def evaluate(
    session: Session, config: dict | None = None, context: dict | None = None,
    rules: tuple[str, ...] | None = None,
) -> list[Candidate]:
    """Run every rule and return what wants raising.

    Args:
        session: Open session.
        config: The `alerts` block; read from disk when omitted.
        context: This run's facts that no query can supply — `run_id`,
            `budget_breach`, `drift`, `snapshot_id`.
        rules: Restrict to named rules; defaults to all.

    Returns:
        Candidates, system first, then by severity. A rule that raises is a bug
        in the rule, and is allowed to propagate: an alerter that silently
        swallows its own failures is the worst component in the system to have
        fail quietly.
    """
    config = config if config is not None else settings()
    context = context or {}
    names = rules if rules is not None else tuple(RULES)
    out = [c for name in names for c in RULES[name](session, config, context)]
    order = {CRITICAL: 0, WARNING: 1, INFO: 2}
    return sorted(out, key=lambda c: (c.kind != SYSTEM, order.get(c.severity, 3)))


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------

def deliver_stdout(alert: m.Alert) -> None:
    """Print. The dev channel, and the fallback when no webhook is configured."""
    print(f"[{alert.severity.upper()}] {alert.kind}/{alert.rule}: {alert.subject}")


def deliver_webhook(alert: m.Alert) -> None:
    """POST the alert as JSON to `ALERT_WEBHOOK_URL`.

    Raises:
        RuntimeError: If no URL is configured — a webhook channel with no URL is
            a misconfiguration, not a silent no-op.
        urllib.error.URLError: On a delivery failure, which the caller records
            rather than treating as fatal.
    """
    url = os.environ.get(WEBHOOK_ENV)
    if not url:
        raise RuntimeError(f"channel is 'webhook' but {WEBHOOK_ENV} is not set")
    body = json.dumps({
        "kind": alert.kind, "rule": alert.rule, "severity": alert.severity,
        "subject": alert.subject, "text": alert.body, "payload": alert.payload,
    }).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=DELIVERY_TIMEOUT_S) as response:
        response.read()


CHANNELS = {"stdout": deliver_stdout, "webhook": deliver_webhook}


def dispatch(
    session: Session, candidates: list[Candidate], config: dict | None = None,
    channel: str | None = None,
) -> dict:
    """Record new alerts and deliver what the cap allows.

    Recording and delivery are separate on purpose. Everything new is written
    and visible in the app; delivery is capped so a first run over an existing
    corpus, or a genuinely busy day, does not fire dozens of notifications. The
    undelivered ones say why in `delivery_error` rather than looking like
    failures.

    Args:
        session: Open session; committed here.
        candidates: What the rules produced.
        config: The `alerts` block; read from disk when omitted.
        channel: Override the configured channel.

    Returns:
        ``{raised, duplicate, reopened, delivered, suppressed, failed}``.
        `reopened` counts alerts whose acknowledgement this firing withdrew
        because the rules produced their episode key again.
    """
    config = config if config is not None else settings()
    channel = channel or config.get("channel", "stdout")
    cap = int(config.get("max_deliveries_per_run", 10))
    send = CHANNELS.get(channel)
    if send is None:
        raise ValueError(f"unknown alert channel {channel!r}")

    stats = {"raised": 0, "duplicate": 0, "reopened": 0,
             "delivered": 0, "suppressed": 0, "failed": 0}
    fresh: list[m.Alert] = []

    for candidate in candidates:
        exists = session.scalar(
            select(m.Alert).where(m.Alert.dedupe_key == candidate.dedupe_key)
        )
        if exists is not None:
            stats["duplicate"] += 1
            # A duplicate is proof the condition is still live, and this is the
            # only place in the system that knows it. Dedupe keys identify the
            # *episode* and deliberately do not move while a fault continues —
            # `source_down` keys on `last_success_at` precisely so a week-long
            # outage is one alert. That is what makes acknowledgement dangerous
            # on its own: an operator clears the badge, the source stays down,
            # no new row is ever written, and the badge stays green through the
            # entire outage. Acknowledging is a claim the fault had settled; the
            # rules regenerating the same key withdraws that claim.
            if exists.acknowledged_at is not None:
                exists.acknowledged_at = None
                stats["reopened"] += 1
            continue
        alert = m.Alert(
            kind=candidate.kind, rule=candidate.rule, severity=candidate.severity,
            subject=candidate.subject, body=candidate.body,
            payload=candidate.payload, dedupe_key=candidate.dedupe_key,
            run_id=candidate.run_id,
        )
        session.add(alert)
        fresh.append(alert)
        stats["raised"] += 1
    session.flush()

    for index, alert in enumerate(fresh):
        if index >= cap:
            alert.delivery_error = f"suppressed: over the per-run cap of {cap}"
            stats["suppressed"] += 1
            continue
        try:
            send(alert)
        except Exception as exc:  # noqa: BLE001 — delivery is best-effort
            # A channel being down must not fail the run, and must not lose the
            # alert: the row is the record of truth, delivery is a courtesy.
            alert.delivery_error = f"{type(exc).__name__}: {exc}"[:1000]
            stats["failed"] += 1
        else:
            alert.sent_at = utcnow()
            stats["delivered"] += 1

    # Runs alerted on are marked so the standing query stops returning them.
    for alert in fresh:
        if alert.rule == "run_failed" and alert.run_id:
            run = session.get(m.PipelineRun, alert.run_id)
            if run is not None:
                run.alerted_at = utcnow()

    session.commit()
    return stats


def recent(session: Session, kind: str | None = None, limit: int = 50) -> list[dict]:
    """Recent alerts, newest first, for the API and the ops view."""
    query = select(m.Alert).order_by(m.Alert.id.desc()).limit(limit)
    if kind:
        query = query.where(m.Alert.kind == kind)
    return [
        {
            "id": a.id, "kind": a.kind, "rule": a.rule, "severity": a.severity,
            "subject": a.subject, "body": a.body, "payload": a.payload,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "sent_at": a.sent_at.isoformat() if a.sent_at else None,
            "delivery_error": a.delivery_error, "run_id": a.run_id,
            "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        }
        for a in session.scalars(query)
    ]


def acknowledge(session: Session, kind: str = "system") -> int:
    """Mark every unacknowledged alert of one kind as seen. Returns how many.

    Acknowledgement is the only thing this clears: the rows stay in the history
    and the ops page still lists them, marked. What it resets is the header
    badge, which counts unacknowledged system alerts and stays red until someone
    says they have seen them — nothing ages out by itself.

    **A live fault reopens this, and that safety property lives in `dispatch`,
    not here.** It is tempting to argue that a still-broken source raises a new
    alert on its next firing — it does not. Dedupe keys identify the *episode*
    and hold still while a fault continues (`source_down` keys on
    `last_success_at` for exactly that reason), so nothing new is ever written
    during an outage. Acknowledging alone would therefore green the badge for
    the whole of it. `dispatch` closes that: when the rules regenerate a key
    whose row is acknowledged, it clears `acknowledged_at` and the badge
    reddens on the next firing.

    Args:
        session: Open session; the caller commits.
        kind: `system` or `content`.

    Returns:
        Number of alerts acknowledged.
    """
    rows = list(session.scalars(
        select(m.Alert).where(m.Alert.kind == kind, m.Alert.acknowledged_at.is_(None))
    ))
    now = datetime.now(timezone.utc)
    for row in rows:
        row.acknowledged_at = now
    session.flush()
    return len(rows)
