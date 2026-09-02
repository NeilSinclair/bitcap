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
SCORES_DIR = ROOT / "research" / "docs" / "announcement_scores"
COSTS = ROOT / "research" / "docs" / "announcement_cost.json"


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
        session: Open session; this function commits.
        path: The announcements register file.
        run_id: pipeline_runs row to attribute inserts/updates to.
        limit: Load only the first N records (the n=1 proving path).

    Returns:
        Counts: inserted / updated / unchanged.
    """
    records = json.loads(path.read_text())[:limit]
    existing = {r.url: r for r in session.scalars(select(m.RawArticle))}
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    now = utcnow()
    for rec in records:
        digest = content_hash(rec)
        row = existing.get(rec["url"])
        if row is None:
            source = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name
            session.add(m.RawArticle(url=rec["url"], payload=rec, content_hash=digest,
                                     source_file=source, load_run_id=run_id))
            counts["inserted"] += 1
        elif row.content_hash != digest:
            row.payload, row.content_hash = rec, digest
            row.last_seen_at, row.load_run_id = now, run_id
            counts["updated"] += 1
        else:
            row.last_seen_at = now
            counts["unchanged"] += 1
    session.commit()
    return counts


def load_classifications(session: Session, prompt_version: str,
                         articles_path: Path = ARTICLES,
                         scores_dir: Path = SCORES_DIR,
                         run_id: int | None = None, limit: int | None = None) -> dict:
    """Upsert the per-URL score cache into raw_classifications.

    Reads the cache files (the true per-call provenance) rather than the merged
    register, keyed back to the real URL via the cache-key function.

    Args:
        session: Open session; this function commits.
        prompt_version: Which cache directory to read (e.g. "v7").
        articles_path: Register supplying the URL list.
        scores_dir: Parent of the per-version cache directories.
        run_id: pipeline_runs row to attribute writes to.
        limit: Only the first N articles.

    Returns:
        Counts: inserted / updated / unchanged / missing (no cache file).
    """
    urls = [rec["url"] for rec in json.loads(articles_path.read_text())[:limit]]
    cache = scores_dir / prompt_version
    existing = {
        r.url: r for r in session.scalars(
            select(m.RawClassification).where(m.RawClassification.prompt_version == prompt_version)
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
            session.add(m.RawClassification(url=url, prompt_version=prompt_version,
                                            payload=payload, load_run_id=run_id))
            counts["inserted"] += 1
        elif row.payload != payload:
            row.payload, row.load_run_id = payload, run_id
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
    session.commit()
    return counts


def load_costs(session: Session, path: Path = COSTS, run_id: int | None = None) -> dict:
    """Insert cost-log rows not yet present, keyed on (url, at).

    Args:
        session: Open session; this function commits.
        path: The append-only cost log.
        run_id: pipeline_runs row to attribute inserts to.

    Returns:
        Counts: inserted / unchanged, and the summed USD of the new rows.
    """
    rows = json.loads(path.read_text()) if path.exists() else []
    seen = set(session.execute(select(m.RawCost.url, m.RawCost.at)).all())
    counts = {"inserted": 0, "unchanged": 0, "new_usd": 0.0}
    for r in rows:
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
    session.commit()
    counts["new_usd"] = round(counts["new_usd"], 6)
    return counts
