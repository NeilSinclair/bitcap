"""Summarise the gold-set articles with a cheap model, one run file per model.

Part of the summarisation-feasibility research (research/summarisation_research.md):
can a cheap summariser compress articles before the extraction/scoring model reads
them, without losing the figures and mechanism-bearing claims the classifier needs?

Writes research/test_results/summaries_<model>.json. Never touches the gold
articles themselves.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from providers import _cost, load_env  # noqa: E402

ROOT = Path(__file__).parent.parent.parent
GOLD = Path(__file__).parent / "test" / "articles"
RESULTS = ROOT / "research" / "test_results"
PROMPT = ROOT / "prompts" / "summarisation" / "v1.md"

MODELS = {
    "haiku": ("anthropic", "claude-haiku-4-5-20251001"),
    "gpt-5-mini": ("openai", "gpt-5-mini"),
}


def build_prompt(article: dict) -> tuple[str, str]:
    """Assemble the system and user prompts for one article.

    Injects the same vocabulary blocks the scoring prompt uses, so the
    summariser is told exactly which information classes must survive.

    Args:
        article: Article record from the gold set.

    Returns:
        Tuple of (system prompt, user prompt).
    """
    import score_announcements as sa

    mech_block, cat_block, prac_block, _, _, _ = sa.vocabularies()
    system = (
        PROMPT.read_text(encoding="utf-8")
        .replace("{mechanisms}", mech_block)
        .replace("{categories}", cat_block)
        .replace("{practices}", prac_block)
    )
    user = (
        f"lab: {article['lab']}\n"
        f"date: {article['date']}\n"
        f"url: {article['url']}\n"
        f"text_source: {article['text_source']}\n\n"
        f"---\n{article['text']}\n---"
    )
    return system, user


def summarise_anthropic(model: str, system: str, user: str, url: str) -> tuple[str, dict]:
    """Summarise one article with the Anthropic Messages API.

    Args:
        model: Model id.
        system: System prompt.
        user: User message.
        url: Article URL, for the cost record.

    Returns:
        Tuple of (summary text, cost record).

    Raises:
        RuntimeError: On refusal or truncation.
    """
    import anthropic

    client = anthropic.Anthropic()
    started = time.time()
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"refused: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("truncated output; raise max_tokens")
    text = next(b.text for b in response.content if b.type == "text")
    usage = response.usage
    return text, _cost(model, url, usage.input_tokens, usage.output_tokens, started)


def summarise_openai(
    model: str, system: str, user: str, url: str, reasoning_effort: str
) -> tuple[str, dict]:
    """Summarise one article with the OpenAI Chat Completions API.

    Args:
        model: Model id.
        system: System prompt.
        user: User message.
        url: Article URL, for the cost record.
        reasoning_effort: Effort setting for GPT-5-family reasoning. Reasoning
            tokens are billed as output, so this is a cost lever and its value
            is recorded in the cost record.

    Returns:
        Tuple of (summary text, cost record).

    Raises:
        RuntimeError: On refusal or truncation.
    """
    from openai import OpenAI

    client = OpenAI()
    started = time.time()
    response = client.chat.completions.create(
        model=model,
        reasoning_effort=reasoning_effort,
        max_completion_tokens=4000,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("truncated output")
    if getattr(choice.message, "refusal", None):
        raise RuntimeError(f"refused: {choice.message.refusal}")
    usage = response.usage
    cost = _cost(model, url, usage.prompt_tokens, usage.completion_tokens, started)
    cost["reasoning_effort"] = reasoning_effort
    details = getattr(usage, "completion_tokens_details", None)
    if details is not None:
        cost["reasoning_tokens"] = details.reasoning_tokens
    return choice.message.content, cost


def main() -> None:
    """Summarise every gold article with the chosen model and record the run."""
    parser = argparse.ArgumentParser()
    parser.add_argument("model", choices=sorted(MODELS), help="which summariser")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--reasoning-effort", default="low",
        help="gpt-5-mini only: reasoning effort (billed as output tokens)",
    )
    args = parser.parse_args()

    provider, model_id = MODELS[args.model]
    load_env()
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"summaries_{args.model}.json"

    files = sorted(GOLD.glob("*.json"))
    records = [json.loads(f.read_text()) for f in files]
    print(f"{len(records)} gold articles | {model_id} | prompt summarisation/v1")

    def work(rec: dict) -> dict:
        system, user = build_prompt(rec)
        try:
            if provider == "anthropic":
                summary, cost = summarise_anthropic(model_id, system, user, rec["url"])
            else:
                summary, cost = summarise_openai(
                    model_id, system, user, rec["url"], args.reasoning_effort
                )
        except Exception as exc:
            return {"id": rec["id"], "error": str(exc)}
        return {
            "id": rec["id"], "lab": rec["lab"], "date": rec["date"],
            "title": rec["title"], "url": rec["url"],
            "text_chars": len(rec["text"]), "summary_chars": len(summary),
            "summary": summary, "cost": cost,
        }

    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(work, records))

    spend = sum(r["cost"]["usd"] for r in rows if r.get("cost"))
    failures = [r for r in rows if "error" in r]
    out.write_text(json.dumps(
        {"model": model_id, "prompt": "summarisation/v1", "results": rows,
         "usd": round(spend, 6)}, indent=2))

    done = [r for r in rows if "summary" in r]
    ratio = (sum(r["summary_chars"] for r in done)
             / max(1, sum(r["text_chars"] for r in done)))
    print(f"\n{len(done)} summarised in {time.time() - started:.0f}s, "
          f"{len(failures)} failures")
    for f in failures:
        print(f"   {f['id']}: {f['error']}")
    print(f"  compression: {ratio:.0%} of original chars")
    print(f"  ${spend:.4f} -> {out.name}")


if __name__ == "__main__":
    main()
