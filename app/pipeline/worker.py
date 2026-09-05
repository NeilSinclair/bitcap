"""`bitcap-worker`: one scheduled firing, start to finish.

    sync schema -> ingest -> land (corpus + raw_articles + register)
                -> classify (budgeted) -> drift -> ETL -> alerts -> exit code

Both LLM stages run before the ETL on purpose: each writes its cost records to
the shared append-only log as it goes, and the ETL's `load_costs` is then the
single place a firing's spend is totalled — one reading of one durable log,
rather than each stage adding its own figure on top.

Two things here are decisions rather than plumbing, and both are about what
"failed" means.

**A dead source is not a dead run.** The orchestrator isolates sources precisely
so one broken lab does not cost the other six; the run status has to say the
same thing or the alerting contradicts the design one layer down. Marking the
run failed whenever any source fails produced a `critical` `run_failed` alert on
*every* firing while one source stayed down — duplicating what `source_down`
already reports once per outage (docs/decisions.md D26). So a firing that
completes is `succeeded` even with failures underneath it, and those failures
are escalated by `source_down` after N consecutive runs, not immediately.

**The exit code follows the same rule.** Non-zero means the firing itself broke
and the platform's own cron alerting should fire. A source being down exits
zero, because a nightly red cron for a transient outage makes the platform's
alerting the noisy channel instead of ours.

Every leg runs on every firing except GitHub, which runs every 3rd. Skipping a
*feed* leg is what was rejected: it produces "no new papers" meaning "we did not
look", which nothing downstream can distinguish from "nothing was published".
The GitHub leg is not a feed — it re-derives contribution weight over a full
3-month history, so each run restates the whole picture and a skipped one loses
nothing, while costing 14 minutes of wall-clock to do it.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sqlalchemy.exc import IntegrityError

from app import digest as digest_mod
from app import models as m
from app.cli import PROMPT_VERSION
from app.connect import connect as run_connect
from app.db import ensure_schema, get_engine, get_session, load_env
from app.load_raw import load_articles, load_classifications, load_costs
from app.load_refs import load_refs
from app.pipeline import alerts as alerts_mod
from app.pipeline import dedupe as dedupe_mod
from app.pipeline import drift as drift_mod
from app.pipeline import register as register_mod
from app.pipeline.budget import Budget
from app.pipeline.classify import budget_breach, classify_new
from app.pipeline.orchestrator import ingest
from app.pipeline.registry import CORPUS_LABELS, LEGS, load_sources
from app.pipeline.sink import merge_announcements
from app.runs import tracked, watermarks
from app.transform import transform

CONFIG = Path(__file__).parent.parent.parent / "config" / "pipeline.yaml"
KIND = "scheduled"


class ConcurrentRunRefused(RuntimeError):
    """Raised when a firing will not start because another is in flight.

    Not a failure of this firing — nothing broke, and the work will happen on
    the next one. `main` therefore exits 0: a nightly red cron for a run that
    correctly declined to trample another makes the platform's alerting the
    noisy channel, which is the same reasoning as a dead source (D26).
    """


def load_config(path: Path = CONFIG) -> dict:
    """Read config/pipeline.yaml."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def firing_number(session: Session) -> int:
    """Which scheduled firing this is, counting from 1.

    Cadence is derived from this rather than from wall-clock dates, so a
    platform that misses a night does not skip a leg's turn as well.
    """
    done = session.scalar(
        select(func.count()).select_from(m.PipelineRun).where(m.PipelineRun.kind == KIND)
    )
    return int(done or 0) + 1



def live_legs(config: dict) -> tuple[str, ...]:
    """Legs not switched off in config, ignoring cadence.

    Cadence says *when* a leg runs; this says whether it exists at all. The
    landing and classification phases need the second question: a leg that is
    merely not due this firing still has rows in bronze that should be derived
    and classified as usual.

    Args:
        config: Parsed pipeline config.

    Returns:
        Leg names, in registry order.
    """
    enabled = config.get("enabled", {})
    return tuple(leg for leg in LEGS if enabled.get(leg, True))


def dead_corpora(config: dict) -> tuple[str, ...]:
    """`raw_articles.source_file` values belonging to legs switched off.

    A leg's rows outlive the firing that fetched them, so stopping the fetch
    does nothing about the corpus already in bronze. Without this the kill
    switch stops ingestion and nothing else: the operator who switched the leg
    off after looking at its output still pays to classify that output on the
    next firing.

    Args:
        config: Parsed pipeline config.

    Returns:
        Source-file labels to exclude from the classification work list.
    """
    live = set(live_legs(config))
    return tuple(label for leg, label in CORPUS_LABELS.items() if leg not in live)


def due_legs(firing: int, config: dict, only: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Which legs run on this firing.

    ``(firing - 1) % cadence == 0``, so firing 1 runs every leg — a fresh
    deployment does a full sweep rather than waiting six days for GitHub.

    `enabled: false` outranks both cadence and an explicit `--legs`. A leg
    switched off must stay off however the worker is invoked — including from
    the manual trigger and including firing 1's full sweep — or the switch is
    only a suggestion, and the reason to reach for it is usually that the leg
    is producing something wrong.

    Args:
        firing: 1-based firing number.
        config: Parsed pipeline config.
        only: Explicit override; cadence is ignored when given. `None` means no
            override. An **empty tuple means run no ingestion legs at all**, and
            the difference matters: the manual trigger sends an empty selection
            when the operator ticked only the drift check, and reading that as
            "no override" ran every cadence-due leg instead of none of them —
            live on run 16, which fetched 2,000 GitHub repos nobody asked for
            (docs/decisions.md D40).

    Returns:
        Leg names, in registry order.
    """
    live = live_legs(config)
    if only is not None:
        return tuple(leg for leg in live if leg in only)
    cadence = config.get("cadence", {})
    return tuple(
        leg for leg in live if (firing - 1) % max(1, int(cadence.get(leg, 1))) == 0
    )


def ingest_cost(session: Session, run_id: int) -> float:
    """LLM spend recorded against this run's sources.

    Only the papers leg is ever non-zero: its harvesters record cost to per-lab
    logs that carry no timestamp, so those rows cannot key into `raw_costs`
    (unique on `url, at`) without inventing one. Attributing the delta to the
    source that spent it is precise and needs no fabricated field.
    """
    return float(session.scalar(
        select(func.coalesce(func.sum(m.RunSource.cost_usd), 0.0))
        .where(m.RunSource.run_id == run_id)
    ) or 0.0)


def _etl(session: Session, run: m.PipelineRun, prompt_version: str, stats: dict) -> None:
    """Refs + raw + transform + connect, as one transaction on this run row.

    Mirrors `app.cli.cmd_load`'s ordering, including why connect is chained: the
    ref reload deletes the connections, so stopping before the join would leave
    the table empty and looking like a finding.
    """
    with tracked(session, KIND, stats, run=run):
        stats["refs"] = load_refs(session)
        # `load_articles` is deliberately absent: phase 2 already put this
        # firing's articles into bronze so the classifier could see them.
        stats["classifications"] = load_classifications(session, prompt_version, run_id=run.id)
        stats["costs"] = costs = load_costs(session, run_id=run.id)
        stats["transform"] = transform(session, prompt_version, run_id=run.id)
        stats["connections"] = run_connect(session, prompt_version, run_id=run.id)
        # Assign, never accumulate: `new_usd` is this run's whole delta on the
        # shared cost log, covering classification and drift alike. Adding it to
        # a figure those stages had already contributed double-counted them.
        # The papers leg is the one spender that writes a log with no timestamp,
        # so it cannot key into `raw_costs`; its cost is attributed per source in
        # `run_sources` and added here.
        run.cost_usd = costs["new_usd"] + ingest_cost(session, run.id)
        run.watermarks = watermarks(session)


def run_once(
    session: Session,
    legs: tuple[str, ...] | None = None,
    config_path: Path = CONFIG,
    prompt_version: str = PROMPT_VERSION,
    spend: bool = True,
    deliver: bool = True,
    drift: bool | None = None,
    run: m.PipelineRun | None = None,
) -> tuple[m.PipelineRun, dict]:
    """Execute one firing.

    Args:
        session: Open session.
        legs: Override which legs run; cadence decides when omitted.
        config_path: Pipeline config.
        prompt_version: Classifier version to fill in and join on.
        spend: False skips every LLM stage (classification and drift). Used by
            `--dry-run` to exercise the whole shape for free.
        deliver: False records alerts without pushing them to the channel.
        drift: Force the drift check on or off; None lets cadence decide. The
            manual trigger needs this — an operator ticking a box has said what
            they want, and silently overriding it with the schedule's opinion
            would make the button lie.
        run: An already-created run row to execute against, instead of opening
            one. The manual trigger (`api/pipeline.py`) needs this: it has to
            claim the row *before* starting a thread, or two clicks in the same
            instant both pass the concurrency check. Its row carries
            `kind="manual"`, which is also what keeps a hand-started run out of
            `firing_number` — that counts scheduled firings to decide cadence,
            and a button press must not consume the GitHub leg's turn.

    Returns:
        The run row and its stats.

    Raises:
        Whatever the ETL raised. Ingestion failures never reach here — they are
        the orchestrator's to isolate.
    """
    config = load_config(config_path)
    stats: dict = {}

    # Counted before this firing's own row exists, or it counts itself.
    firing = firing_number(session)

    if run is None:
        run = m.PipelineRun(kind=KIND)
        session.add(run)
        try:
            session.commit()
        except IntegrityError as exc:
            # The single-running index (D44). Another firing is in flight — the
            # manual trigger, or a previous cron that has not finished. Refusing
            # is the whole point: two firings interleave writes to the corpus
            # file and lose cost records between them.
            session.rollback()
            raise ConcurrentRunRefused(
                "another pipeline run is already in flight; this firing did not start"
            ) from exc

    try:
        return _phases(session, run, firing, config, config_path, legs,
                       prompt_version, spend, deliver, stats, drift)
    except (Exception, SystemExit) as exc:
        # Any failure outside the ETL — the sink, the register load, the
        # classifier, the alerter — must still close the run out. Without this
        # the row stays `running` forever: a corpse that the `run_failed` rule
        # never matches and no operator can distinguish from a firing still in
        # progress. `SystemExit` is included because code under research/ is
        # scripts first and calls `sys.exit()` on missing config; it derives
        # from BaseException and would otherwise slip past.
        session.rollback()
        run.status, run.error, run.finished_at = "failed", str(exc)[:2000], m.utcnow()
        run.stats = {**(run.stats or {}), **stats}
        session.commit()
        raise


def _phases(
    session: Session, run: m.PipelineRun, firing: int, config: dict, config_path: Path,
    legs: tuple[str, ...] | None, prompt_version: str, spend: bool, deliver: bool,
    stats: dict, drift: bool | None = None,
) -> tuple[m.PipelineRun, dict]:
    """The firing's phases, in order. Wrapped by :func:`run_once` for failure."""
    chosen = due_legs(firing, config, legs)
    stats["firing"] = firing
    stats["legs"] = list(chosen)

    def note(phase: str, **extra) -> None:
        """Commit where the firing has got to, so a watcher can see it.

        A firing is 9 to 30 minutes and everything it records — `stats`, the
        drift metrics, the cost — was written at the *end*, so anything watching
        saw one long silence and then an answer. That is fine for a cron job
        writing a log nobody reads and useless behind a button somebody is
        standing in front of: 8 minutes of "Running…" is indistinguishable from
        broken, and the honest response to it was to reach for the database
        (D41).

        Not called inside `_etl`: that phase is deliberately one transaction
        spanning a wipe and a reload, and committing partway through would
        defeat the guarantee it exists for.
        """
        run.stats = {**(run.stats or {}), **stats, "phase": phase, **extra}
        session.commit()

    # 1. Ingest. Commits per source; a dead source is recorded, not fatal.
    #    Under the ceiling: the papers leg pays for LLM byline extraction, so
    #    ingestion is not a free phase.
    budget = Budget.from_config(session, config_path)
    note("ingest")
    report = ingest(session, load_sources(legs=chosen), run_id=run.id,
                    budget=budget if spend else None)
    stats["ingest"] = report.stats

    # 2. Land what was ingested where the loaders read it.
    note("landing")
    if "announcements" in chosen:
        stats["corpus"] = merge_announcements(report.items_for("announcements"))
    # Releases are deliberately absent here. They go into bronze inside their
    # own adapter, in the same transaction that advances the cursor gating
    # every future fetch — landing them a phase later would let a failure in
    # between lose them permanently and silently (docs/decisions.md D52).
    if "releases" in chosen:
        stats["releases"] = {"landed_in": "adapter"}
    # Into bronze here, not in the ETL. `classify_new` picks its work list from
    # `raw_articles` (a LEFT JOIN against `raw_llm_responses`), so while this
    # ran in phase 5 the classifier in phase 3 could only ever see articles
    # ingested by a *previous* firing: found tonight, scored tomorrow. Measured
    # on run 12 — four OpenAI articles ingested, `classify` reported
    # `pending: 0`, and the ETL then reported `classifications.missing: 4`
    # (docs/decisions.md D38).
    stats["articles"] = load_articles(session, run_id=run.id)
    stats["register"] = register_mod.load_report(session, report, run.id)
    session.commit()

    # 3. Classify what is new, under a ceiling.
    # Every LLM stage runs before the ETL, deliberately. Both write their cost
    # records to the shared append-only log as they go, and `_etl`'s
    # `load_costs` is then the single place run cost is accounted — one reading
    # of one durable log. Adding each stage's own figure on top of that counted
    # the same money twice.
    if spend:
        note("classify")
        stats["classify"] = classify_new(
            session, prompt_version, budget, config_path=config_path,
            # A leg switched off must stop costing money, not just
            # stop fetching: its rows are already in bronze from
            # earlier firings and would otherwise be classified.
            exclude_source_files=dead_corpora(config))
        session.commit()

    # 4. Is the scorer still agreeing with itself? Before the ETL so its spend
    #    is in the log by the time load_costs reads it.
    drift_metrics, snapshot_id = None, None
    drift_due = (
        drift if drift is not None
        else (firing - 1) % max(1, int(config.get("cadence", {}).get("drift", 1))) == 0
    )
    if spend and drift_due:
        note("drift")
        drift_metrics = drift_mod.measure(
            budget=budget,
            on_progress=lambda done, total: note(
                "drift", progress={"done": done, "total": total}
            ),
        )
        stats["drift"] = {
            "mechanism_f1": drift_metrics.get("mechanism_f1"),
            "compared": drift_metrics.get("compared"),
            "cost_usd": drift_metrics.get("cost_usd"),
        }
        session.commit()

    # 5. Derive the clean layer and the joins, atomically. Also the point where
    #    this firing's cost is totalled.
    note("etl")
    _etl(session, run, prompt_version, stats)

    if drift_metrics is not None:
        snapshot_id = drift_mod.record(session, drift_metrics, prompt_version, run.id).id
        session.commit()

    # 5b. Collapse near-duplicates into one row per event.
    #
    #     Between the ETL and the digest because it needs `event_type` and
    #     `summary` (so after `transform`) and the digest selects what surfaces
    #     (so before it). Deliberately NOT inside `_etl`: that is one
    #     transaction and this phase calls two external APIs, so an OpenAI
    #     timeout there would roll back the whole rebuild.
    #
    #     Degrades rather than fails. If embedding is unavailable the exact pass
    #     and the release trains still run, which is most of the collapse by
    #     volume; the run records the failure and carries on, because a feed
    #     with duplicate rows is a worse product, not a broken one.
    note("dedupe")
    try:
        embedded = dedupe_mod.embed(session, prompt_version, budget=budget)
        grouped = dedupe_mod.assign(session, prompt_version, run_id=run.id, budget=budget)
        stats["dedupe"] = {**grouped, "embedded": embedded["embedded"],
                           "embedding_usd": embedded["usd"]}
    except Exception as error:  # noqa: BLE001 - a duplicate row must not fail a run
        session.rollback()
        stats["dedupe"] = {"error": str(error)[:500]}
    session.commit()

    # 6. Publish both audiences' digests for the window this firing closes.
    #    After the ETL because the investment cut reads `connections`, which the
    #    ETL rebuilds; free and deterministic, so it runs on every firing
    #    including a dry one. Idempotent on (kind, window_end, prompt_version) —
    #    a re-run updates the edition it already published rather than issuing a
    #    second one for the same period.
    note("digest")
    published = digest_mod.publish(session, prompt_version, run.started_at, run_id=run.id)
    stats["digest"] = {d.kind: d.stats for d in published}
    session.commit()

    # 7. A source that failed is escalated by `source_down`, not by this run's
    #    status. See the module docstring.
    run.status = "succeeded"
    run.finished_at = m.utcnow()
    run.stats = {**(run.stats or {}), **stats}
    session.commit()

    # 8. Alerts last, so they see the finished state of everything above.
    context = {
        "run_id": run.id,
        "budget_breach": budget_breach(stats.get("classify", {})),
        "drift": drift_metrics,
        "snapshot_id": snapshot_id,
        "skipped_for_budget": stats.get("classify", {}).get("skipped_for_budget"),
    }
    stats["alerts"] = alerts_mod.dispatch(
        session,
        alerts_mod.evaluate(session, config["alerts"], context),
        config["alerts"],
        channel=config["alerts"].get("channel") if deliver else "silent",
    )
    run.stats = {**(run.stats or {}), **stats}
    session.commit()
    return run, stats


def _silent(alert) -> None:
    """Record-only channel, for --dry-run and for backfilling a first firing."""


alerts_mod.CHANNELS.setdefault("silent", _silent)


def main(argv: list[str] | None = None) -> int:
    """Entry point for `bitcap-worker`.

    Returns:
        0 when the firing completed — including one where a source was down,
        which `source_down` escalates rather than the exit code. Non-zero only
        when the firing itself broke, so the platform's cron alerting stays a
        signal rather than a nightly red light.
    """
    parser = argparse.ArgumentParser(prog="bitcap-worker", description=__doc__)
    parser.add_argument("--legs", nargs="*", choices=LEGS,
                        help="override cadence and run exactly these legs")
    parser.add_argument("--prompt", default=PROMPT_VERSION)
    parser.add_argument("--dry-run", action="store_true",
                        help="run the whole shape with no LLM spend and no alert delivery")
    args = parser.parse_args(argv)

    load_env()
    engine = get_engine()
    ensure_schema(engine)
    session = get_session(engine)

    try:
        run, stats = run_once(
            session,
            # `is not None`, not truthiness: `--legs` with no values means "no
            # ingestion legs", and `or None` would hand that back to cadence —
            # the same conflation `due_legs` was fixed for, left behind on this
            # path (D43).
            legs=tuple(args.legs) if args.legs is not None else None,
            prompt_version=args.prompt,
            spend=not args.dry_run,
            deliver=not args.dry_run,
        )
        # Read off the row before the session closes; a detached instance
        # cannot refresh, and the summary is the only thing a cron log shows.
        summary = (run.status, run.cost_usd or 0.0)
    except ConcurrentRunRefused as exc:
        # Exit 0: nothing broke. Another firing holds the lock and this one
        # correctly stood down; the work happens on the next tick. A non-zero
        # exit here would page somebody every time a manual run overlapped 3am.
        print(f"firing skipped — {exc}", file=sys.stderr)
        session.close()
        return 0
    except (Exception, SystemExit):
        traceback.print_exc()
        print("firing FAILED", file=sys.stderr)
        return 1
    finally:
        session.close()

    status, cost = summary
    ingest_stats = stats.get("ingest", {})
    print(
        f"\nfiring {stats['firing']} [{', '.join(stats['legs'])}] {status} "
        f"— {ingest_stats.get('succeeded', 0)}/{ingest_stats.get('sources', 0)} sources, "
        f"${cost:.4f}"
    )
    if ingest_stats.get("failed"):
        print(f"  {ingest_stats['failed']} source(s) failed — see run_sources and alerts")
    print(f"  alerts: {stats.get('alerts')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
