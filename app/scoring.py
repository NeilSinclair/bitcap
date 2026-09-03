"""Deterministic scoring: tags in, numbers out.

Moved verbatim from research/announcements/score_announcements.py so the scorer
has one home; that module imports these back. The LLM never emits a score —
these rules (config/scoring.yaml) are the only place numbers come from, so
every number can be argued line by line and changed without touching code.
"""

from __future__ import annotations


def score_of(result: dict, rules: dict) -> tuple[float, str]:
    """Compute the transmission score from the model's tags.

    Everything multiplies. Within a tag, magnitude times confidence is an
    expected magnitude. Across the two axes, event weight times mechanism
    strength reads as "importance of this kind of event, times the strength of
    the evidence found" -- and, critically, makes a non-zero score impossible
    without at least one quote-backed mechanism tag.

    The model's own `is_signal` flag is ignored here; see the note in the body.

    Args:
        result: Parsed model output.
        rules: Parsed config/scoring.yaml.

    Returns:
        Tuple of (score out of 100, band label).
    """
    # `is_signal` is deliberately NOT consulted. It was a hard veto until a
    # stability check found it flipping on 9% of re-classified items, zeroing
    # articles that carried maximum-strength, quote-backed tags. A single
    # unstable boolean must not override the evidence; an item with no tags
    # scores zero through the arithmetic anyway.
    event = rules["event_weight"].get(result["event_type"], 0)

    strongest = 0
    for tag in result["mechanisms"]:
        strongest = max(
            strongest,
            rules["magnitude"][tag["magnitude"]] * rules["confidence"][tag["confidence"]],
        )

    score = round(
        100
        * (event / rules["max_event_weight"])
        * (strongest / rules["max_mechanism"]),
        1,
    )
    band = next(b["label"] for b in rules["bands"] if score >= b["min"])
    return score, band


def ai_score_of(result: dict, rules: dict) -> tuple[float, str]:
    """Compute the AI-team score from the practice tags.

    Mirrors score_of on the other axis, with one deliberate difference: there is
    no event weight. `event_weight` is an investment taxonomy -- compute
    commitments rank 5 because clusters move semiconductor demand, which tells an
    engineer nothing. The practice tag already says what kind of thing this is,
    so the first axis is `action`: what the reader should do about it.

    Args:
        result: Parsed model output.
        rules: Parsed config/scoring.yaml.

    Returns:
        Tuple of (score out of 100, band label).
    """
    ai = rules["ai_team"]
    strongest, action = 0, 0
    for tag in result.get("practices", []):
        strongest = max(
            strongest, ai["impact"][tag["impact"]] * ai["confidence"][tag["confidence"]]
        )
        action = max(action, ai["action_weight"][tag["action"]])

    score = round(
        100 * (action / ai["max_action"]) * (strongest / ai["max_practice"]), 1
    )
    band = next(b["label"] for b in ai["bands"] if score >= b["min"])
    return score, band
