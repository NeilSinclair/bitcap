"""Drop the posts that cannot carry signal, before any of them cost a token.

Per-item classification costs roughly the same whether the item is 280
characters or 4,000: a ~3,000-token prompt dwarfs the document. The papers leg
measured $0.015 an item. So classifying 473 posts unfiltered would cost more
than retrieving them did, and most of that money would be spent confirming that
"check our model out!" is not an investment signal.

This module is the cheap half of the noise filter. It is deterministic, it runs
at zero marginal cost, and it only removes posts that *cannot* carry a claim --
never posts that merely look unpromising. Judgement belongs to the prompt; this
is arithmetic.

It also computes the number the leg exists to produce. A post whose links
already point at a document in `raw_articles` is a pointer to coverage we have,
not new signal. That test is an exact URL match, so unlike a similarity
threshold it needs no calibration and cannot drift -- which is what makes it a
defensible answer to "does X tell us anything the lab announcements do not".
"""

from __future__ import annotations

import re

# Furniture that carries no claim: the link itself, the @mention, the hashtag
# symbol, and emoji. Stripped only to MEASURE length -- the stored text is
# always the post as written, because a quote must resolve against it.
_URL = re.compile(r"https?://\S+")
_MENTION = re.compile(r"@[A-Za-z0-9_]{1,15}")
_HASH = re.compile(r"#")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF⬀-⯿"
    "️‍←-⇿⌀-⏿]")


def substance(text: str) -> str:
    """The part of a post that could contain a claim.

    Args:
        text: The post as written.

    Returns:
        Text with links, mentions, hashes and emoji removed and whitespace
        collapsed. Used for length tests only; never stored.
    """
    stripped = _EMOJI.sub("", _HASH.sub("", _MENTION.sub("", _URL.sub("", text))))
    return re.sub(r"\s+", " ", stripped).strip()


def verdict(record: dict, rules: dict) -> str | None:
    """Why this post should be dropped, or None to keep it.

    Args:
        record: An article-shaped post record.
        rules: The `prefilter` block of config/posts_sources.yaml.

    Returns:
        A reason string, or None if the post survives.
    """
    core = substance(record["text"])
    if rules.get("drop_link_only", True) and record["links"] and not core:
        return "link only: no text of its own"
    if len(core) < rules["min_chars"]:
        # The threshold is on substance, so "congrats @someone 🎉 <link>" is
        # eight characters rather than forty-eight.
        return f"under {rules['min_chars']} characters of substance ({len(core)})"
    return None


def apply(records: list[dict], rules: dict, known_urls: set[str]) -> tuple[list[dict], list[dict]]:
    """Split a pulled corpus into what is worth scoring and what is not.

    Every kept record gains `duplicates_announcement`, and the dropped ones keep
    their reason, because "we filtered this" and "we never saw this" must not
    look the same to anyone reading the corpus later.

    Args:
        records: Article-shaped post records.
        rules: The `prefilter` block of config/posts_sources.yaml.
        known_urls: Every URL already in `raw_articles`.

    Returns:
        Tuple of (kept, dropped). Kept records carry
        `duplicates_announcement`: True when one of the post's links resolves to
        a document the register already holds.
    """
    kept, dropped = [], []
    for record in records:
        reason = verdict(record, rules)
        if reason:
            dropped.append({**record, "dropped": reason})
            continue
        hits = [u for u in record["links"] if u.rstrip("/") in known_urls]
        kept.append({**record,
                     "duplicates_announcement": bool(hits),
                     "duplicate_of": hits[0] if hits else None})
    return kept, dropped
