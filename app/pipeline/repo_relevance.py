"""Judge whether a repository is LLM work, once, and remember the answer.

The releases leg watches repositories by star rank, and a large share of what
that surfaces is not language-model work: physics simulators, protein structure
pipelines, a fusion solver, a weather model, vision libraries. They are not
mis-scored — `alphafold3` averages 35.6 on the AI axis, third of all 87 watched
repositories — which is exactly why no score threshold removes them. Relevance
is a different axis and it needs a different question.

The question is asked **once per repository**, not once per release: a
repository's subject does not change between version bumps, so judging releases
would re-buy the same answer 380 times and growing. Verdicts are cached in
`raw_llm_responses`, the table classifications and duplicate adjudications
already use.

Two callers, and they are deliberately different:

* `judge` may call the provider. Used inside the ranking walk, where a verdict
  is needed for a repository nobody has judged before.
* `off_topic` only ever reads the cache. Used by `transform`, which runs over
  the whole corpus on every firing and must never start buying anything.

**Failure keeps the repository.** The house convention is not to prefer the
cheap outcome, it is to decline the destructive action when no answer came back.
For duplicate adjudication the destructive action is merging, so a malformed
verdict does not merge. Here it is *exclusion*: a wrongly dropped repository
stops producing articles, and nothing downstream can tell that apart from a
repository that shipped nothing. A wrongly kept one costs a classification and
is visible in the feed. The asymmetry points at fail-open, and
`alerts.repo_filter_unavailable` is what stops fail-open becoming a silent
no-op when a provider is down.
"""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app import models as m

ROOT = Path(__file__).parent.parent.parent
PROMPTS = ROOT / "prompts" / "repo_relevance"

# Verdict urls are namespaced so the table stays readable and so no other reader
# can collide with them: `classify` and the adapters intersect pending work
# against `raw_articles.url`, and nothing there is ever `repo:...`.
PREFIX = "repo:"

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relevant": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["relevant", "reason"],
}


def _record_cost(cost: dict) -> None:
    """Append one cost record to the shared log, immediately.

    Mirrors `dedupe._record_cost`, including the guard: a record missing `url`
    or `at` is dropped rather than written, because those two are `raw_costs`'s
    key and an unloadable record would sit in an append-only file until
    `load_costs` crashed on it.

    This is the only place the filter's spend is written. It deliberately does
    **not** also reach `FetchResult.cost_usd`: `budget.month_to_date` sums
    `raw_costs` *plus* `run_sources.cost_usd` on the stated assumption that the
    two never overlap, and writing both would double-count the filter against
    the monthly ceiling — silently, and in the direction that stops the pipeline
    early.

    Args:
        cost: Record from `providers._cost`.
    """
    import json

    import score_announcements as sa_module

    if not (cost.get("url") and cost.get("at")):
        return
    log = json.loads(sa_module.COST.read_text()) if sa_module.COST.exists() else []
    log.append({**cost, "workflow": "repo_relevance"})
    sa_module.COST.write_text(json.dumps(log, indent=2))


def cache_key(config: dict) -> str:
    """The `prompt_version` a verdict is stored under.

    The model is part of the key, which is one thing more than duplicate
    adjudication records. It has to be: `UniqueConstraint(url, prompt_version)`
    means a version-only key would serve verdicts bought from a *different*
    model after `relevance.model` changes in config — and the bake-off that
    chooses that model puts both candidates' verdicts in the table. Re-buying 87
    verdicts after a model change costs about $0.07; inheriting the loser's
    answers while reporting the winner's name costs the ability to say what the
    filter did.

    Args:
        config: The `relevance` block from config/repo_signals.yaml.

    Returns:
        The composite version key.
    """
    return f"{config['prompt_version']}:{config['model']}"


def prompt(config: dict) -> str:
    """Load the system prompt, with its rationale comment stripped.

    Args:
        config: The `relevance` block from config/repo_signals.yaml.

    Returns:
        The prompt text as the model sees it.
    """
    # Bare, unlike `providers` below, and the difference is not stylistic.
    # `score_announcements` imports its own siblings by bare name
    # (`from verbatim import enforce`), so importing it as
    # `research.announcements.score_announcements` does not put its directory on
    # `sys.path` and it raises ModuleNotFoundError. `dedupe` splits the two
    # spellings for the same reason.
    #
    # The path is added here rather than relied on, because `adapters` happens to
    # add it at import time and depending on that made this function work only
    # when it was reached through the adapter. Measured the hard way, twice: with
    # the import wrong every verdict came back None, the gate failed open on all
    # of them, and the filter did nothing whatever while reporting a full watch
    # list. Fail-open plus a swallowed import error is silent by construction.
    announcements = str(ROOT / "research" / "announcements")
    if announcements not in sys.path:
        sys.path.insert(0, announcements)
    import score_announcements as sa_module

    path = PROMPTS / f"{config['prompt_version']}.md"
    return sa_module.strip_comments(path.read_text(encoding="utf-8"))


def describe(row: dict) -> str:
    """Render one ranked repository row as the user message.

    Args:
        row: A row from `rank_repos.rank`, with the live listing overlaid.

    Returns:
        The user message.
    """
    topics = ", ".join(row.get("topics") or ()) or "(none)"
    return (
        f"Organisation: {row['org']}\n"
        f"Repository: {row['repo']}\n"
        f"Stars: {row.get('stars', 0)}\n"
        f"Language: {row.get('language') or '(none)'}\n"
        f"Topics: {topics}\n"
        f"Description: {row.get('description') or '(none)'}"
    )


def judge(session: Session | None, row: dict,
          config: dict) -> tuple[dict | None, float, str | None]:
    """Decide whether one repository is LLM-relevant.

    Args:
        session: Open session for the verdict cache. None means no caching —
            every call hits the provider.
        row: A ranked row carrying `org`, `repo`, and the listing fields.
        config: The `relevance` block from config/repo_signals.yaml.

    Returns:
        Tuple of (verdict, usd, error). The verdict is `{"relevant": bool,
        "reason": str}`, or None if the call failed or came back malformed.
        None means "not judged", which every caller treats as **keep** — see the
        module docstring. The cost is returned even when the verdict is None,
        because a call the provider billed still spent money.

        `error` carries why, and it is the third element rather than a swallowed
        exception because failing open is silent by construction: an import
        error in this function once made every verdict None, and the run
        reported a full watch list and no fault. `source_down` keeps
        `last_error` for the same reason.
    """
    # Imported the way `dedupe` imports it, not as a bare `providers`. The two
    # spellings resolve to two different module objects, so mixing them means a
    # monkeypatch in a test patches one and the code calls the other — which is
    # a test that cannot fail, and this repo has shipped three of those.
    from research.announcements import providers

    version = cache_key(config)
    key = f"{PREFIX}{row['org']}/{row['repo']}"

    if session is not None:
        cached = session.scalar(
            sa.select(m.RawLlmResponse).where(
                m.RawLlmResponse.url == key,
                m.RawLlmResponse.prompt_version == version,
            )
        )
        if cached is not None:
            return cached.payload, 0.0, None

    try:
        result, cost = providers.classify(
            config["provider"], config["model"],
            prompt(config), describe(row), SCHEMA, key,
        )
    except Exception as exc:
        # Network, rate limit, refusal, truncation, a broken import. No verdict
        # and no spend we can attribute; the caller keeps the repository, counts
        # the error, and carries the reason so somebody can act on it.
        return None, 0.0, f"{type(exc).__name__}: {exc}"[:300]

    # Recorded here, at the call site, with tokens -- CLAUDE.md's third
    # non-negotiable, and the same `_record_cost` path dedupe and the drift check
    # use. Two reasons it belongs here rather than on the return value:
    # `FetchResult.cost_usd` is only read on the adapter's success path, so a
    # later failure inside `fetch_releases` (one bad token, every repository
    # 404ing) discarded spend that had already left the card; and a rolled-up
    # float on `run_sources` cannot answer "how much of this is input?" later.
    _record_cost(cost)

    if not isinstance(result, dict) or not isinstance(result.get("relevant"), bool):
        # Never cached. A cached malformed verdict would freeze the failure in
        # place, for free, for ever -- the one outcome worse than re-buying.
        return None, float(cost["usd"]), f"malformed verdict: {str(result)[:200]}"

    if session is not None:
        session.add(m.RawLlmResponse(url=key, prompt_version=version, payload=result))
        session.flush()
    return result, float(cost["usd"]), None


def off_topic(session: Session, config: dict) -> set[str]:
    """Every repository cached as off-topic, as `"org/repo"` strings.

    Reads the cache and never calls a provider, because the caller is
    `transform`, which runs over the whole corpus on every firing. A repository
    with no cached verdict is absent from this set and therefore kept, which is
    the same fail-open direction `judge` takes.

    Args:
        session: Open session.
        config: The `relevance` block from config/repo_signals.yaml.

    Returns:
        Set of `"org/repo"`.
    """
    rows = session.scalars(
        sa.select(m.RawLlmResponse).where(
            m.RawLlmResponse.url.startswith(PREFIX),
            m.RawLlmResponse.prompt_version == cache_key(config),
        )
    ).all()
    return {
        r.url[len(PREFIX):] for r in rows
        if isinstance(r.payload, dict) and r.payload.get("relevant") is False
    }
