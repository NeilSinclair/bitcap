"""Refresh the gold set's article text against the current register.

The gold set stores its own copy of each article's text -- deliberately, so a
label always refers to exactly the bytes the model was shown. That copy went
stale when the OpenAI backfill replaced 205-character RSS summaries with full
archived articles: nine gold files still held the summary, so the gold set was
testing the model on inputs the pipeline no longer produces.

Text is taken from the register where the article is still in it, and recovered
straight from the archive where it is not (the gold set predates the window
being cut to three months, so eleven of its twenty articles are no longer in the
corpus).

The `gold` block is never touched. It is the human-editable slot, and the labels
it holds refer to the article, not to a particular extraction of it -- but see
the warning printed at the end: a label pre-filled from a 205-character summary
is a poor starting point for reviewing a 10,000-character article.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from backfill_openai import recover, slug_of  # noqa: E402

ROOT = Path(__file__).parent.parent.parent
GOLD = Path(__file__).parent / "test" / "articles"
ARTICLES = ROOT / "research" / "docs" / "announcements.json"
INDEX = ROOT / "research" / "docs" / "wayback_index_gold.json"


def main() -> None:
    """Bring every gold article's text up to date with the register."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    register = {a["url"]: a for a in json.loads(ARTICLES.read_text())}
    index = json.loads(INDEX.read_text())

    changed, gained, missing = 0, 0, []
    for path in sorted(GOLD.glob("*.json")):
        record = json.loads(path.read_text())
        fresh = register.get(record["url"])

        if fresh is None and record["text_source"] == "rss_summary":
            # Out of the current window, so not in the register, but the archive
            # still has it. Recover directly rather than leave a summary behind.
            timestamp = index.get(slug_of(record["url"]))
            if not timestamp:
                missing.append(record["id"])
                continue
            got = recover(record, timestamp)
            if got is None:
                missing.append(record["id"])
                continue
            text, snapshot = got
            fresh = {"text": text, "text_source": "full_text_archived",
                     "archive_snapshot": snapshot, "date": record["date"]}

        if fresh is None or fresh["text"] == record["text"]:
            continue

        before = len(record["text"])
        print(f"  {record['id']}  {record['text_source']:18} -> "
              f"{fresh['text_source']:18} {before:6,} -> {len(fresh['text']):6,} chars"
              f"   {record['title'][:38]}")
        record["text"] = fresh["text"]
        record["text_source"] = fresh["text_source"]
        record["date"] = fresh["date"]
        if fresh.get("archive_snapshot"):
            record["archive_snapshot"] = fresh["archive_snapshot"]
        gained += len(fresh["text"]) - before
        changed += 1
        if not args.dry_run:
            path.write_text(json.dumps(record, indent=2))

    print(f"\n{'would change' if args.dry_run else 'refreshed'}: {changed} articles, "
          f"{gained:,} characters gained")
    if missing:
        print(f"no snapshot: {missing}")
    if changed and not args.dry_run:
        print("\nWARNING: the `gold` blocks on these files were pre-filled from the\n"
              "old text. A label written against a 205-character summary is not a\n"
              "valid label for the full article. Re-prefill before reviewing.")


if __name__ == "__main__":
    main()
