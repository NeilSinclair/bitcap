"""Load the research pipeline's JSON artifacts into the raw tables, verbatim.

The research scripts stay the writers of record for their files; this module
only reads. Raw tables are upsert-only on natural keys, so the DB accumulates
history while the file register remains a rolling window — an article falling
out of fetch's 3-month window stays in the DB.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.models import utcnow

ROOT = Path(__file__).parent.parent
ARTICLES = ROOT / "research" / "docs" / "announcements.json"
# Committed like the announcements corpus, and for the same reason: `rebuild`
# must reproduce the whole register from files in the repo, with no API key and
# no network. Without it a fresh clone has no papers at all -- the leg's rows
# only ever reached bronze from a live firing.
PAPERS = ROOT / "research" / "docs" / "papers_corpus.json"
SCORES_DIR = ROOT / "research" / "docs" / "announcement_scores"
# Papers are cached under their own prompt version, so their per-call
# provenance lives in its own tree (score_announcements.PAPERS).
PAPER_SCORES_DIR = ROOT / "research" / "docs" / "paper_scores"
COSTS = ROOT / "research" / "docs" / "announcement_cost.json"
# Repository relevance verdicts. Committed because they are the one derived
# artifact here that a rebuild cannot recompute from files -- only re-buy.
REPO_VERDICTS = ROOT / "research" / "docs" / "repo_relevance_verdicts.json"


def content_hash(payload: dict) -> str:
    """Canonical sha256 of a JSON payload, for change detection."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def cache_key(url: str) -> str:
    """The score cache's filename for a URL.

    Must match ``classify_one`` in score_announcements.py exactly, or the load
    reads the wrong provenance.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", url)[:140] + ".json"


def load_articles(session: Session, path: Path = ARTICLES,
                  run_id: int | None = None, limit: int | None = None) -> dict:
    """Upsert announcements.json into raw_articles.

    Args:
        session: Open session; this function flushes, the caller commits.
        path: The announcements register file.
        run_id: pipeline_runs row to attribute inserts/updates to.
        limit: Load only the first N records (the n=1 proving path).

    Returns:
        Counts: inserted / updated / unchanged.
    """
    records = json.loads(path.read_text())[:limit]
    source = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name
    return load_article_records(session, records, source, run_id)


def load_article_records(session: Session, records: list[dict], source_file: str,
                         run_id: int | None = None) -> dict:
    """Upsert article records into raw_articles.

    Split out of :func:`load_articles` because not every leg has a file. The
    releases leg fetches its documents and hands them straight over: there is
    no committed corpus for them to merge into, and the deployed container has
    no disk to keep one on (D31), so bronze is the only store.

    Args:
        session: Open session; this function flushes, the caller commits.
        records: Article records, each carrying a resolvable `url`.
        source_file: Provenance recorded on new rows. A shared table needs it
            to say which leg a row came from, and the kill switch matches on it.
        run_id: pipeline_runs row to attribute inserts/updates to.

    Returns:
        Counts: inserted / updated / unchanged.
    """
    existing = {r.url: r for r in session.scalars(select(m.RawArticle))}
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    now = utcnow()
    for rec in records:
        digest = content_hash(rec)
        row = existing.get(rec["url"])
        if row is None:
            row = m.RawArticle(url=rec["url"], payload=rec, content_hash=digest,
                               source_file=source_file, load_run_id=run_id)
            session.add(row)
            existing[rec["url"]] = row  # a repeated URL in one batch updates, not IntegrityError
            counts["inserted"] += 1
        elif row.content_hash != digest:
            row.payload, row.content_hash = rec, digest
            row.last_seen_at, row.load_run_id = now, run_id
            counts["updated"] += 1
        else:
            row.last_seen_at = now
            counts["unchanged"] += 1
    session.flush()
    return counts


def load_classifications(session: Session, prompt_version: str,
                         articles_path: Path | None = None,
                         scores_dir: Path = SCORES_DIR,
                         run_id: int | None = None, limit: int | None = None,
                         source_files: tuple[str, ...] = ()) -> dict:
    """Upsert the per-URL score cache into raw_llm_responses.

    Reads the cache files (the true per-call provenance) rather than the merged
    register, keyed back to the real URL via the cache-key function.

    Args:
        session: Open session; this function flushes, the caller commits.
        prompt_version: Which cache directory to read (e.g. "v7").
        articles_path: Take the URL list from this corpus file instead of
            from `raw_articles`. For one-off loads over a corpus that is
            not in the database; the default covers every source file.
        scores_dir: Parent of the per-version cache directories.
        run_id: pipeline_runs row to attribute writes to.
        limit: Only the first N articles.
        source_files: Restrict the URL list to these corpora. Without it the
            `missing` count below is meaningless once a second prompt version
            exists: every announcement is "missing" from the papers version and
            vice versa, so a healthy firing reports ~647 and ~47 missing. That
            number is a diagnostic -- `classifications.missing: 4` is how the
            D38 failure was caught -- and a permanently non-zero one is noise.

    Returns:
        Counts: inserted / updated / unchanged / missing (no cache file).
    """
    if articles_path is not None:
        urls = [rec["url"] for rec in json.loads(articles_path.read_text())[:limit]]
    else:
        # From bronze, not from one corpus file. `raw_articles` carries a row
        # per article-producing leg, and driving this from announcements.json
        # alone leaves every release scored, paid for and cached on disk but
        # never loaded: `transform` finds no classification, counts it under
        # `no_classification` and skips it. The next firing re-lists the same
        # URLs as pending, serves every one from cache for free, and reports
        # `classified: N, cost_usd: 0.0` -- indistinguishable from a healthy
        # incremental run, for ever.
        query = select(m.RawArticle.url)
        if source_files:
            query = query.where(m.RawArticle.source_file.in_(tuple(source_files)))
        urls = list(session.scalars(query))[:limit]
    cache = scores_dir / prompt_version
    existing = {
        r.url: r for r in session.scalars(
            select(m.RawLlmResponse).where(m.RawLlmResponse.prompt_version == prompt_version)
        )
    }
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "missing": 0}
    for url in urls:
        file = cache / cache_key(url)
        if not file.exists():
            counts["missing"] += 1
            continue
        payload = json.loads(file.read_text())
        row = existing.get(url)
        if row is None:
            row = m.RawLlmResponse(url=url, prompt_version=prompt_version,
                                      payload=payload, load_run_id=run_id)
            session.add(row)
            existing[url] = row  # same-file duplicates must not abort the load
            counts["inserted"] += 1
        elif row.payload != payload:
            row.payload, row.load_run_id = payload, run_id
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
    session.flush()
    return counts


def load_repo_verdicts(session: Session, path: Path = REPO_VERDICTS,
                       run_id: int | None = None) -> dict:
    """Replay the committed repository relevance verdicts into the cache.

    `raw_llm_responses` is not an ops table, so `bitcap-db rebuild` drops it.
    Every other derived thing in this repo survives that because it is rebuilt
    from a committed artifact — but the relevance verdicts cannot be recomputed
    from files, only re-bought from a provider. Without this, a fresh clone
    starts with an empty cache and, because the derivation gate only ever
    *reads* the cache, renders every off-topic release again while
    `source_state` (which does survive) still reports them excluded.

    That would also break the README's promise that a rebuild needs no API key.

    Keyed on `(url, prompt_version)` like every other row in the table, so a
    verdict already bought tonight is left alone rather than overwritten.

    Args:
        session: Open session; this function flushes, the caller commits.
        path: The committed verdicts artifact.
        run_id: pipeline_runs row to attribute inserts to.

    Returns:
        Counts: inserted / unchanged / malformed.
    """
    rows = json.loads(path.read_text()) if path.exists() else []
    seen = set(session.execute(
        select(m.RawLlmResponse.url, m.RawLlmResponse.prompt_version)
        .where(m.RawLlmResponse.url.startswith("repo:"))
    ).all())
    counts = {"inserted": 0, "unchanged": 0, "malformed": 0}
    for r in rows:
        # A verdict without a repo, a version, or a real boolean cannot be
        # stored as one. Counted rather than raised, so one bad row does not
        # take down a load that is otherwise fine.
        if not (r.get("repo") and r.get("prompt_version")) or not isinstance(
                r.get("relevant"), bool):
            counts["malformed"] += 1
            continue
        key = (f"repo:{r['repo']}", r["prompt_version"])
        if key in seen:
            counts["unchanged"] += 1
            continue
        seen.add(key)
        session.add(m.RawLlmResponse(
            url=key[0], prompt_version=key[1], load_run_id=run_id,
            payload={"relevant": r["relevant"], "reason": r.get("reason", "")}))
        counts["inserted"] += 1
    session.flush()
    return counts


def load_costs(session: Session, path: Path = COSTS, run_id: int | None = None) -> dict:
    """Insert cost-log rows not yet present, keyed on (url, at).

    Args:
        session: Open session; this function flushes, the caller commits.
        path: The append-only cost log.
        run_id: pipeline_runs row to attribute inserts to.

    Returns:
        Counts: inserted / unchanged, and the summed USD of the new rows.
    """
    rows = json.loads(path.read_text()) if path.exists() else []
    seen = set(session.execute(select(m.RawCost.url, m.RawCost.at)).all())
    counts = {"inserted": 0, "unchanged": 0, "new_usd": 0.0, "malformed": 0}
    for r in rows:
        # (url, at) is the key. A record missing either cannot be stored, and
        # one bad row must not take down a load that is otherwise fine — it is
        # counted and reported instead, so the gap stays visible.
        if not (r.get("url") and r.get("at")):
            counts["malformed"] += 1
            continue
        key = (r["url"], r["at"])
        if key in seen:
            counts["unchanged"] += 1
            continue
        seen.add(key)
        session.add(m.RawCost(url=r["url"], model=r["model"],
                              input_tokens=r["input_tokens"],
                              output_tokens=r["output_tokens"],
                              cache_write_tokens=r.get("cache_write_tokens", 0),
                              cache_read_tokens=r.get("cache_read_tokens", 0),
                              usd=r["usd"], seconds=r["seconds"], at=r["at"],
                              load_run_id=run_id))
        counts["inserted"] += 1
        counts["new_usd"] += r["usd"]
    session.flush()
    counts["new_usd"] = round(counts["new_usd"], 6)
    return counts
