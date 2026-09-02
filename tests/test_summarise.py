"""Tests for the summarisation research script.

The failure these exist to catch: a prompt edit that leaves a vocabulary
placeholder unfilled, so the summariser is silently never told what to
preserve — the summaries would still look plausible.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "research" / "announcements"))

from summarise import GOLD, MODELS, PROMPT, build_prompt


ARTICLE = {
    "lab": "openai",
    "date": "2026-01-01",
    "url": "https://example.com/post",
    "text_source": "full_text",
    "text": "Example announcement body.",
}


def test_prompt_file_exists():
    assert PROMPT.exists()


def test_vocab_placeholders_filled():
    system, _ = build_prompt(ARTICLE)
    for placeholder in ("{mechanisms}", "{categories}", "{practices}"):
        assert placeholder not in system
    # The vocab actually made it in, not just the braces stripped.
    assert "inference_cost_down" in system
    assert "serving_efficiency" in system


def test_user_prompt_carries_article():
    _, user = build_prompt(ARTICLE)
    assert "Example announcement body." in user
    assert "text_source: full_text" in user
    assert "https://example.com/post" in user


def test_models_priced():
    """Every summariser model must have a price row, or costs are wrong."""
    from providers import PRICES

    for _, model_id in MODELS.values():
        assert model_id in PRICES


def test_gold_set_present():
    assert len(list(GOLD.glob("*.json"))) == 20
