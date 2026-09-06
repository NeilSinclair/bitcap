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
from app.load_raw import PAPERS as PAPERS_CORPUS_FILE
from app.load_raw import POSTS as POSTS_CORPUS_FILE
from app.pipeline import dedupe as dedupe_mod
from app.pipeline.registry import CORPUS_LABELS, PAPERS_CORPUS, POSTS_CORPUS
from app.load_raw import (PAPER_SCORES_DIR, POST_SCORES_DIR, load_articles,
                          load_classifications, load_costs, load_repo_verdicts)
from app.load_refs import load_refs
from app.runs import tracked, watermarks
from app.transform import transform

PROMPT_VERSION = "v9"

# Papers are scored under their own version against prompts/paper_scoring/p1.md.
# Sharing PROMPT_VERSION would force a v10 for every announcement too -- ~$16.70
# to re-classify 647 rows that ask the same question of unchanged text -- so the
# two corpora carry their own versions and the readers below take both.
PAPER_PROMPT_VERSION = "p1"

# Posts are scored under their own version against prompts/post_scoring/t2.md,
# for the same reason papers are: `classifications.prompt_version` is a plain
# string, so a third corpus costs nothing to add and re-classifies nothing.
#
# t2 replaces t1 (D70). t1 measurably lost stated prices: all four posts it
# classified `pricing_change` -- the joint-highest event weight -- returned zero
# mechanism tags and scored 0.0, including "80% drop... now $0.20 per million
# input tokens". Scoring requires one quote-backed mechanism, so no tag is no
# score. Both versions' rows coexist in `classifications`; nothing re-classifies.
POST_PROMPT_VERSION = "t2"

# Every reader of `classifications` must span both, and `connect` especially:
# it deletes the whole table before rebuilding, so running it once per version
# would leave only the second version's rows (app/connect.py).
PROMPT_VERSIONS = (PROMPT_VERSION, PAPER_PROMPT_VERSION, POST_PROMPT_VERSION)

# What the DIGEST and the content alerts read. Posts were held out of this while
# the corpus was unproven (D63); D69 admits them, so all four corpora are now
# eligible for a digest.
#
# Alerts are a separate decision and are still off: `content_mute_prompt_versions`
# in config/pipeline.yaml keeps `t1` muted, so a post can reach a digest a reader
# chooses to open without being able to page anyone. Two surfaces, two switches.
#
# `app/pipeline/worker.py` builds its own tuple here rather than importing this
# one, because it honours a `--prompt` override. The two must agree at the
# default, and `test_digest.py` asserts it: a divergence would show posts in
# `/api/digests/preview` while the published digest omitted them, which is the
# same "two places disagree and both look right" failure D67 and D69 are about.
DIGEST_VERSIONS = (PROMPT_VERSION, PAPER_PROMPT_VERSION, POST_PROMPT_VERSION)


_load_env = load_env  # kept as a name here; the implementation lives in app.db


def _group(session, prompt_version: str, run_id: int) -> dict:
    """Rebuild `article_groups` and `article_links`, or say why it could not.

    Split out of `cmd_load` so the guard is visible rather than buried in a
    twenty-line comment, and so the two callers of the same reasoning — this and
    `worker._phases` — look alike.

    Args:
        session: Open session; `dedupe.assign` commits, so this must be called
            outside any transaction the caller means to keep atomic.
        prompt_version: Announcement version to group. Once, not once per
            version: `assign` rebuilds both tables wholesale on every call, so
            looping would leave only the last version's rows — the same trap
            `connect` carries, one table over, and why that one takes a tuple.
        run_id: Run to attribute the rows to.

    Returns:
        `assign`'s stats, or `{"error": ...}` if it raised.
    """
    try:
        # `adjudicate_pairs=False` is what makes this safe in a rebuild: it runs
        # the exact pass, the release trains and the cached-cosine gate, and
        # skips the one call that costs money. `budget=None` would NOT do it —
        # to `adjudicate` that means unlimited, not do not call.
        return dedupe_mod.assign(session, prompt_version, run_id=run_id,
                                 adjudicate_pairs=False)
    except Exception as error:  # noqa: BLE001 - duplicates are not a failed load
        session.rollback()
        return {"error": str(error)[:500]}


def cmd_load(session, prompt_version: str, kind: str = "load") -> None:
    """Run refs + raw + transform + connect as one tracked transaction, then group.

    Connect is chained deliberately: the ref reload deletes the connections
    (they reference holdings and are derived), so a load that stopped before
    the join would leave the table empty and looking like a finding.

    **Grouping is chained for the same reason, and was not (D74).**
    `load_refs` deletes `ArticleGroup` and `ArticleLink` by name — it has to,
    because both carry a plain foreign key to `articles` with no cascade, so
    leaving them would make its `DELETE FROM articles` raise. Nothing then
    rebuilt them. A `bitcap-db load` therefore left both at zero rows and every
    surface silently flat: no "5 more on this" on the dashboard, no fold badge
    in the digest, no related-document pills anywhere. Nothing errored, because
    from the pipeline's side nothing had.

    It matters beyond an operator's afternoon: `bitcap-db rebuild` is
    `drop_all` + `ensure_schema` + this function, and README.md makes it the
    first command a new reader runs. Without the chain they clone the repo,
    follow the instructions, and open an ungrouped feed with no way to tell it
    is not the finished product.

    **The grouping call is outside the tracked block, and that is not tidiness.**
    `dedupe.assign` commits internally, and `tracked` owns the single commit
    precisely so a failure rolls back to the previous good state rather than to
    an empty database. See the comment at the call site.

    `stats` is filled stage by stage and handed to `tracked`, which records it
    on either path — so a run that dies in `transform` says so, rather than
    leaving an operator to guess from an empty stats blob. `dedupe` is added
    after, and the run row is updated with it.

    Args:
        session: Open session; `tracked` owns the commit.
        prompt_version: Which classifications to derive and join from.
        kind: Recorded on the run row — `rebuild` when the schema was dropped
            first, `load` when it was not. The two are not interchangeable in
            a run history.
    """
    stats: dict = {}
    with tracked(session, kind, stats) as run:
        versions = (prompt_version, PAPER_PROMPT_VERSION, POST_PROMPT_VERSION)
        stats["refs"] = load_refs(session)
        stats["articles"] = load_articles(session, run_id=run.id)
        # The papers corpus is a committed artifact, so `rebuild` reproduces it
        # without a network call. The leg still refreshes it on a live firing.
        stats["paper_corpus"] = load_articles(session, path=PAPERS_CORPUS_FILE,
                                              run_id=run.id)
        stats["posts_corpus"] = load_articles(session, path=POSTS_CORPUS_FILE,
                                              run_id=run.id)
        stats["classifications"] = load_classifications(
            session, prompt_version, run_id=run.id,
            source_files=tuple(c for c in CORPUS_LABELS.values()
                               if c not in (PAPERS_CORPUS, POSTS_CORPUS)))
        stats["paper_classifications"] = load_classifications(
            session, PAPER_PROMPT_VERSION, scores_dir=PAPER_SCORES_DIR, run_id=run.id,
            source_files=(PAPERS_CORPUS,))
        stats["post_classifications"] = load_classifications(
            session, POST_PROMPT_VERSION, scores_dir=POST_SCORES_DIR, run_id=run.id,
            source_files=(POSTS_CORPUS,))
        stats["costs"] = costs = load_costs(session, run_id=run.id)
        # Before `transform`, because transform's relevance gate reads these and
        # only these. Loaded from a committed artifact so a rebuild needs no API
        # key: verdicts are the one derived thing here that cannot be recomputed
        # from files, and without them the rebuilt database renders every
        # off-topic release again (D65).
        stats["repo_verdicts"] = load_repo_verdicts(session, run_id=run.id)
        # Once per version: `transform` scopes its delete by prompt_version, so
        # the two derivations are additive. `connect` is not -- it rebuilds the
        # whole table -- so it is called once, spanning both.
        stats["transform"] = transform(session, prompt_version, run_id=run.id)
        stats["paper_transform"] = transform(session, PAPER_PROMPT_VERSION, run_id=run.id)
        stats["post_transform"] = transform(session, POST_PROMPT_VERSION, run_id=run.id)
        stats["connections"] = run_connect(session, versions, run_id=run.id)
        run.cost_usd = costs["new_usd"]
        run.watermarks = watermarks(session)

    # AFTER the block, and OUTSIDE the transaction it owns. Both halves of that
    # are deliberate and were got wrong first time.
    #
    # `tracked` says it plainly: "The body's stages flush rather than commit, so
    # this context manager owns the single commit... a failure anywhere
    # downstream would leave the database empty rather than stale, and empty is
    # the worse of the two." `dedupe.assign` commits internally. Calling it
    # inside the body made the ref wipe durable mid-load, so a later failure no
    # longer rolled back to the previous good state — and on a first-ever
    # `bitcap-db rebuild`, where there is no previous state, a grouping fault
    # left the new reader with an EMPTY database instead of an ungrouped one.
    # Measured, not reasoned about: articles 577 -> 0.
    #
    # Grouping is also guarded, for the reason `worker._phases` gives at its own
    # call: "a duplicate row must not fail a run". A feed with duplicates in it
    # is a worse product, not a broken load, and it must not cost a new reader
    # their whole database. The failure is recorded on the run row instead, where
    # `alerts.dedupe_unavailable` already knows how to read it.
    stats["dedupe"] = _group(session, prompt_version, run.id)
    run.stats = {**(run.stats or {}), **stats}
    session.commit()


def cmd_connect(session, prompt_version: str) -> None:
    """Rebuild connections under a tracked run.

    Spans the announcement version *and* the paper version, always. `connect`
    deletes the whole table before rebuilding, so passing one version here would
    delete every paper's holding connections and rebuild announcements only --
    silently, leaving the UI showing papers with an empty impact row and the
    investment digest missing every paper on the holding route.
    """
    stats: dict = {}
    with tracked(session, "connect", stats) as run:
        stats["connections"] = run_connect(
            session, (prompt_version, PAPER_PROMPT_VERSION, POST_PROMPT_VERSION),
            run_id=run.id)
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
