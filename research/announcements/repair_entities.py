"""Decode HTML entities left in already-stored article text.

`strip_html` did not unescape entities until 2026-09-01, so 28 articles fetched
before the fix hold literal `&amp;` and `&#x27;`. That is not cosmetic: the
model reads the stored text and quotes the decoded form, so verbatim quote
checking reports hallucinations that did not happen -- it did, on 9 of 90 tags.

Re-fetching would give the same answer. `strip_html` strips tags first and
unescapes last, so applying the unescape to stored text is the same operation
on the same input. This is a one-off repair kept in the repo because it is the
record of what was done to the corpus; it is idempotent and safe to re-run.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

ARTICLES = Path(__file__).parent.parent.parent / "research" / "docs" / "announcements.json"
ENTITY = re.compile(r"&(amp|lt|gt|quot|apos|nbsp|#\d+|#x[0-9a-fA-F]+);")


def repair(text: str) -> str:
    """Apply the entity decoding `strip_html` now performs.

    Args:
        text: Stored article text.

    Returns:
        Text with entities decoded twice and whitespace collapsed.
    """
    return " ".join(html.unescape(html.unescape(text)).split())


def main() -> None:
    """Repair every stored article whose text still holds raw entities."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    articles = json.loads(ARTICLES.read_text())
    changed = 0
    for article in articles:
        if not ENTITY.search(article["text"]):
            continue
        fixed = repair(article["text"])
        if fixed == article["text"]:
            continue
        print(f"  {article['lab']:10} {article['date']}  {article['title'][:52]}")
        article["text"] = fixed
        changed += 1

    if args.dry_run:
        print(f"\ndry run: {changed} articles would change")
        return
    ARTICLES.write_text(json.dumps(articles, indent=2))
    print(f"\nrepaired: {changed} articles")


if __name__ == "__main__":
    main()
