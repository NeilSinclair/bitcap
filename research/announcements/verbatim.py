"""Enforce that every tag's quote really appears in the document it cites.

The citation guarantee is the product: an insight without a resolvable source is
worth less than no insight, because it looks like evidence and is not. Until now
that guarantee rested on the prompt asking for a verbatim quote, and a prompt
that asks has already been observed not to get -- one run produced a quote
spliced from the first word of one sentence and the body of another, stating a
true fact in words the document never used.

This module checks deterministically, and does one thing beyond checking: when a
quote matches only after normalisation, it is **snapped** to the exact substring
of the source. The rewrite happens only when the source proves it correct, so
nothing can be invented. The payoff is stronger than rejecting hallucinations --
every stored quote becomes a literal substring of the article, so a reader who
searches for it finds it.

Failures seen on real output, all repaired by snapping rather than dropped:

- a literal ``\\u2019`` escape emitted instead of the apostrophe
- a trailing full stop the source does not have
- ``Terminal-Bench 2.1 ,`` -- a stray space left by HTML extraction
- ``(opens in a new window)`` link chrome correctly omitted by the model

Ellipsis is allowed. ``A ... B`` passes when both halves are found **and A
precedes B**, which is what the ellipsis claims. Ordering is enforced because
without it the notation licenses exactly the splice this exists to catch.
"""

from __future__ import annotations

import re
import unicodedata

# Below this a segment carries no evidence and would let "a ... b" pass.
MIN_SEGMENT = 12

ELLIPSIS = re.compile(r"\s*(?:\.\.\.+|…)\s*")
ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")

FOLD = {
    "’": "'", "‘": "'", "“": '"', "”": '"',
    "—": "-", "–": "-", "‑": "-", "‐": "-",
    " ": " ", " ": " ", "​": "",
}


def fold(text: str) -> tuple[str, list[int]]:
    """Normalise text for matching, keeping a map back to the original offsets.

    The map is what makes snapping possible: after finding a match in the folded
    text, the original substring can be recovered exactly.

    Args:
        text: Raw text.

    Returns:
        Tuple of (folded text, offset of each folded character in the original).
    """
    # Decode literal \uXXXX sequences the model sometimes emits instead of the
    # character. Done first so the decoded character can then be folded.
    decoded, offsets = [], []
    i = 0
    while i < len(text):
        m = ESCAPE.match(text, i)
        if m:
            decoded.append(chr(int(m.group(1), 16)))
            offsets.append(i)
            i = m.end()
        else:
            decoded.append(text[i])
            offsets.append(i)
            i += 1

    out, index, space = [], [], False
    for ch, offset in zip(decoded, offsets):
        ch = FOLD.get(ch, ch)
        if not ch:
            continue
        ch = unicodedata.normalize("NFKC", ch)
        if ch.isspace():
            # Collapse a whitespace run to one space, anchored at its start.
            if not space and out:
                out.append(" ")
                index.append(offset)
                space = True
            continue
        # HTML extraction leaves a space before punctuation ("Bench 2.1 ,").
        # The model quotes what a reader sees, so drop it on both sides.
        if ch in ",.;:!?)]" and out and out[-1] == " ":
            out.pop()
            index.pop()
        space = False
        out.append(ch)
        index.append(offset)
    while out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def lower(text: str) -> str:
    """Lowercase without changing length, so an offset map stays valid.

    Args:
        text: Folded text.

    Returns:
        The text lowercased, except where lowercasing would change a
        character's width.
    """
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


def locate(needle: str, folded: str, start: int) -> int:
    """Find a folded needle, relaxing case and terminal punctuation in turn.

    Both relaxations are ordinary quoting practice rather than evidence of
    fabrication: a quote beginning mid-sentence is conventionally capitalised
    ("it's" -> "It's"), and a quote ending mid-sentence conventionally takes a
    full stop where the document has a colon. Relaxing costs nothing, because
    the caller returns the document's own wording either way.

    Args:
        needle: Folded quote segment.
        folded: Folded document text.
        start: Offset to search from.

    Returns:
        Offset of the match, or -1.
    """
    at = folded.find(needle, start)
    if at < 0:
        at = lower(folded).find(lower(needle), start)
    if at < 0 and needle[-1] in ".,;:!?":
        at = lower(folded).find(lower(needle[:-1]), start)
        if at >= 0:
            return at  # caller measures length from the trimmed needle
    return at


def find(segment: str, text: str, folded: str, index: list[int], start: int = 0):
    """Locate one quote segment in the document.

    Args:
        segment: The segment to find.
        text: Original document text.
        folded: Folded document text.
        index: Offset map from `fold`.
        start: Folded-text offset to search from, so ordering can be enforced.

    Returns:
        Tuple of (exact source substring, folded end offset), or None.
    """
    if len(segment.strip()) < MIN_SEGMENT:
        return None
    needle, _ = fold(segment)
    if not needle:
        return None
    at = locate(needle, folded, start)
    if at < 0:
        return None
    if folded[at:at + len(needle)] != needle and needle[-1] in ".,;:!?":
        # Matched on the trimmed form; the document does not have that mark.
        if lower(folded[at:at + len(needle)]) != lower(needle):
            needle = needle[:-1]
    first = index[at]
    last = index[at + len(needle) - 1]
    # index[] holds the start offset of each folded character, so the final
    # character's own width has to be added back to include it.
    return text[first:last + 1], at + len(needle)


def snap(quote: str, text: str, folded: str = None, index: list[int] = None):
    """Verify a quote against its document and return it as the document has it.

    Args:
        quote: The quote as the model wrote it.
        text: The document the tag cites.
        folded: Pre-folded document text, for reuse across many tags.
        index: Pre-computed offset map, for reuse across many tags.

    Returns:
        The quote rewritten to the exact source wording, or None if it is not
        in the document. Ellipsis segments are rejoined with " ... ".
    """
    if not quote or not quote.strip():
        return None
    if folded is None or index is None:
        folded, index = fold(text)

    # A whole-string match wins outright: the document may genuinely contain an
    # ellipsis, and splitting one that is really part of the source would be
    # wrong.
    whole = find(quote, text, folded, index)
    if whole:
        return whole[0]

    segments = [s for s in ELLIPSIS.split(quote) if s.strip()]
    if len(segments) < 2:
        return None

    found, cursor = [], 0
    for segment in segments:
        hit = find(segment, text, folded, index, cursor)
        if not hit:
            return None
        found.append(hit[0])
        cursor = hit[1]  # segments must appear in the order the ellipsis claims
    return " ... ".join(found)


def enforce(result: dict, text: str) -> list[str]:
    """Drop every tag whose quote is not in the document, and snap the rest.

    Args:
        result: Parsed model output, modified in place.
        text: The article text the model was shown.

    Returns:
        Descriptions of the dropped tags, for the run's `dropped_tags` record.
    """
    folded, index = fold(text)
    dropped = []
    for key in ("mechanisms", "categories", "practices"):
        kept = []
        for tag in result.get(key, []):
            fixed = snap(tag.get("quote", ""), text, folded, index)
            if fixed is None:
                dropped.append(f"{key}:{tag.get('id', '?')}:quote not in document")
                continue
            if fixed != tag["quote"]:
                # Counted, because how often a quote needs normalising is a
                # quality signal in its own right: a rise means the model is
                # drifting away from the document's own wording.
                tag["quote_repaired"] = True
            tag["quote"] = fixed
            kept.append(tag)
        if key in result:
            result[key] = kept
    return dropped
