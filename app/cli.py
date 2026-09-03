"""`bitcap-db`: build and inspect the pipeline database.

Commands:
    rebuild   Drop everything, recreate the schema, and run the full load —
              always safe, because the DB is derived from committed files.
    load      Refs + raw + transform, incrementally (upserts).
    connect   Rebuild the connections table from the current clean tables.
    status    Last runs, table counts, watermarks, and load cost.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from app import models as m
from app.connect import connect as run_connect
from app.db import drop_all, ensure_schema, get_engine, get_session, load_env
from app.load_raw import load_articles, load_classifications, load_costs
from app.load_refs import load_refs
from app.runs import tracked, watermarks
from app.transform import transform

PROMPT_VERSION = "v7"


_load_env = load_env  # kept as a name here; the implementation lives in app.db


def cmd_load(session, prompt_version: str, kind: str = "load") -> None:
    """Run refs + raw + transform + connect as one tracked transaction.

    Connect is chained deliberately: the ref reload deletes the connections
    (they reference holdings and are derived), so a load that stopped before
    the join would leave the table empty and looking like a finding.

    `stats` is filled stage by stage and handed to `tracked`, which records it
    on either path — so a run that dies in `transform` says so, rather than
    leaving an operator to guess from an empty stats blob.

    Args:
        session: Open session; `tracked` owns the commit.
        prompt_version: Which classifications to derive and join from.
        kind: Recorded on the run row — `rebuild` when the schema was dropped
            first, `load` when it was not. The two are not interchangeable in
            a run history.
    """
    stats: dict = {}
    with tracked(session, kind, stats) as run:
        stats["refs"] = load_refs(session)
        stats["articles"] = load_articles(session, run_id=run.id)
        stats["classifications"] = load_classifications(session, prompt_version, run_id=run.id)
        stats["costs"] = costs = load_costs(session, run_id=run.id)
        stats["transform"] = transform(session, prompt_version, run_id=run.id)
        stats["connections"] = run_connect(session, prompt_version, run_id=run.id)
        run.cost_usd = costs["new_usd"]
        run.watermarks = watermarks(session)


def cmd_connect(session, prompt_version: str) -> None:
    """Rebuild connections under a tracked run."""
    stats: dict = {}
    with tracked(session, "connect", stats) as run:
        stats["connections"] = run_connect(session, prompt_version, run_id=run.id)
        run.watermarks = watermarks(session)


def cmd_status(session) -> None:
    """Print the last runs and the shape of the database."""
    runs = session.scalars(select(m.PipelineRun).order_by(m.PipelineRun.id.desc()).limit(5)).all()
    if not runs:
        print("no runs recorded")
    for r in runs:
        finished = r.finished_at or "…"
        print(f"[{r.id}] {r.kind:8} {r.status:9} started {r.started_at:%Y-%m-%d %H:%M:%S} "
              f"finished {finished if isinstance(finished, str) else f'{finished:%H:%M:%S}'} "
              f"cost ${r.cost_usd:.2f}")
        if r.error:
            print(f"      error: {r.error}")
    latest = runs[0] if runs else None
    if latest and latest.watermarks:
        print("watermarks:")
        for lab, w in sorted(latest.watermarks.items()):
            print(f"  {lab:10} {w['max_published']}  ({w['articles']} articles)")
    print("tables:")
    for table in (m.RawArticle, m.Article, m.Classification, m.ArticleMechanism,
                  m.ArticleCategory, m.ArticlePractice, m.Connection, m.Holding,
                  m.RawCost, m.GoldSnapshot):
        n = session.scalar(select(func.count()).select_from(table))
        print(f"  {table.__tablename__:22} {n}")


def main() -> int:
    """Entry point for the `bitcap-db` script."""
    parser = argparse.ArgumentParser(prog="bitcap-db", description=__doc__)
    parser.add_argument("command", choices=["rebuild", "load", "connect", "status"])
    parser.add_argument("--prompt", default=PROMPT_VERSION, help="classification version")
    args = parser.parse_args()

    _load_env()
    engine = get_engine()
    session = get_session(engine)

    if args.command == "rebuild":
        drop_all(engine)
        ensure_schema(engine)
        cmd_load(session, args.prompt, kind="rebuild")
        cmd_status(session)
    elif args.command == "load":
        ensure_schema(engine)
        cmd_load(session, args.prompt)
    elif args.command == "connect":
        cmd_connect(session, args.prompt)
    elif args.command == "status":
        cmd_status(session)
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
