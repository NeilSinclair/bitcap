"""Provider shim so the same prompt and schema can be run against two vendors.

The prompt, the voting rule and the scoring rule are all provider-agnostic —
they operate on a JSON object, not on an SDK. Only the call itself differs, so
this module isolates that difference and returns an identical
``(result, cost)`` pair either way.

It exists to answer a cost question: whether a cheaper model reaches the same
verdicts as Sonnet on the same six articles. Prices are recorded per call from
the provider's own usage figures, never estimated afterwards.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

# USD per million tokens (input, output).
#
# Anthropic figures are from the model table used elsewhere in this repo.
# The OpenAI figure is UNVERIFIED against OpenAI's published pricing and is
# recorded here so the number the pipeline reports is at least explicit. Token
# counts in the cost log are exact; only this conversion is uncertain.
PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "gpt-5-mini": (0.25, 2.00),  # UNVERIFIED
}


def load_env() -> None:
    """Load .env into the environment without overriding what is already set."""
    from pathlib import Path

    path = Path(__file__).parent.parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _cost(model: str, url: str, tokens_in: int, tokens_out: int, started: float) -> dict:
    """Build a cost record from a provider's own usage figures.

    Args:
        model: Model id.
        url: Article URL, so spend is attributable per item.
        tokens_in: Prompt tokens reported by the provider.
        tokens_out: Completion tokens reported by the provider.
        started: Wall time the call began.

    Returns:
        Cost record.
    """
    in_rate, out_rate = PRICES[model]
    return {
        "url": url,
        "model": model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "usd": round(tokens_in / 1e6 * in_rate + tokens_out / 1e6 * out_rate, 6),
        "seconds": round(time.time() - started, 2),
        "at": datetime.now(timezone.utc).isoformat(),
    }


def classify_anthropic(model: str, system: str, user: str, schema: dict, url: str):
    """Classify one article with the Anthropic Messages API.

    Args:
        model: Model id.
        system: System prompt.
        user: User message.
        schema: JSON schema for structured output.
        url: Article URL, for the cost record.

    Returns:
        Tuple of (parsed result, cost record).

    Raises:
        RuntimeError: On refusal or truncation, rather than returning a partial
            classification that would look like a real one.
    """
    import anthropic

    client = anthropic.Anthropic()
    started = time.time()
    with client.messages.stream(
        model=model,
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError(f"refused: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("truncated output; raise max_tokens")

    text = next(b.text for b in response.content if b.type == "text")
    usage = response.usage
    return json.loads(text), _cost(
        model, url, usage.input_tokens, usage.output_tokens, started
    )


def classify_openai(model: str, system: str, user: str, schema: dict, url: str):
    """Classify one article with the OpenAI Chat Completions API.

    Uses strict structured output, which enforces the same schema the Anthropic
    path enforces, so the two are comparable on identical output constraints.

    Args:
        model: Model id.
        system: System prompt.
        user: User message.
        schema: JSON schema for structured output.
        url: Article URL, for the cost record.

    Returns:
        Tuple of (parsed result, cost record).

    Raises:
        RuntimeError: On refusal or truncation.
    """
    from openai import OpenAI

    client = OpenAI()
    started = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "classification", "strict": True, "schema": schema},
        },
    )

    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("truncated output")
    if getattr(choice.message, "refusal", None):
        raise RuntimeError(f"refused: {choice.message.refusal}")

    usage = response.usage
    return json.loads(choice.message.content), _cost(
        model, url, usage.prompt_tokens, usage.completion_tokens, started
    )


PROVIDERS = {"anthropic": classify_anthropic, "openai": classify_openai}


def classify(provider: str, model: str, system: str, user: str, schema: dict, url: str):
    """Dispatch a classification to the named provider.

    Args:
        provider: Key in PROVIDERS.
        model: Model id.
        system: System prompt.
        user: User message.
        schema: JSON schema for structured output.
        url: Article URL, for the cost record.

    Returns:
        Tuple of (parsed result, cost record).
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    return PROVIDERS[provider](model, system, user, schema, url)
