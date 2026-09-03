"""Where ingested items land.

The announcements leg writes the same `research/docs/announcements.json` the
harvester has always written, and `app.load_raw` reads it unchanged. Keeping
that seam is deliberate: the file is committed, which is what makes
"clone-to-running with real data" true, and it keeps `bitcap-db load` and the
research scripts working exactly as before.

The one thing that had to change is *how* it is written. The harvester writes
the whole file from a full sweep of every lab. The orchestrator does not have a
full sweep — it has whatever the sources that worked returned — so writing the
same way would silently delete a failed lab's articles from the corpus. Merging
by URL is what makes a partial run partial rather than destructive.
"""

from __future__ import annotations

import json
from pathlib import Path

DOCS = Path(__file__).parent.parent.parent / "research" / "docs"
ANNOUNCEMENTS = DOCS / "announcements.json"


def merge_announcements(items: list[dict], path: Path = ANNOUNCEMENTS) -> dict:
    """Merge freshly-fetched articles into the committed corpus.

    Merge, never replace. A run in which OpenAI was down still has to leave
    OpenAI's 138 previously-fetched articles in the file — rewriting it from
    only the labs that answered would present an outage as a shrinking corpus,
    and the next `bitcap-db load` would carry that into a corpus-wide count
    change that looks like a finding.

    Args:
        items: Articles from this run's successful sources.
        path: Corpus file; defaults to the committed one.

    Returns:
        ``{added, updated, unchanged, kept, total}`` — `kept` counts articles
        already in the file that this run did not refetch, which is the number
        that would have been lost by an overwrite.
    """
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    by_url = {a["url"]: a for a in existing}
    seen = {a["url"] for a in items}

    added = updated = unchanged = 0
    for article in items:
        current = by_url.get(article["url"])
        if current is None:
            added += 1
        elif current != article:
            updated += 1
        else:
            unchanged += 1
        by_url[article["url"]] = article

    merged = sorted(by_url.values(), key=lambda a: a["date"], reverse=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    return {
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "kept": len(by_url) - len(seen),
        "total": len(merged),
    }
