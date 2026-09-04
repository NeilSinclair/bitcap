"""The LLM stage: classify what is new, and only what is new.

The work list is a database query, not a file scan. `raw_articles` LEFT JOIN
`raw_llm_responses` at the current prompt version is exactly "articles this
version has never seen", which is the only set worth paying for — every other
article already has a cached result, and re-running it costs nothing but also
achieves nothing.

Two things this stage must get right, because both cost money:

* **Bounded.** Every call is made under a :class:`~app.pipeline.budget.Budget`.
  Reaching the ceiling stops further calls and lets the run finish with what it
  has, rather than failing and discarding an ingest that already happened.
* **Honest about stopping.** A run that classified 4 of 60 articles because it
  hit its ceiling looks identical to a run with 4 new articles unless it says so.
  `skipped_for_budget` is what makes those distinguishable, and it is what the
  system alert fires on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.pipeline.budget import Budget, BudgetExceeded

ROOT = Path(__file__).parent.parent.parent
CONFIG = ROOT / "config" / "pipeline.yaml"

_ANNOUNCEMENTS = str(ROOT / "research" / "announcements")
if _ANNOUNCEMENTS not in sys.path:
    sys.path.insert(0, _ANNOUNCEMENTS)


def pending_urls(session: Session, prompt_version: str,
                 exclude_source_files: tuple[str, ...] = ()) -> list[str]:
    """URLs with a raw article but no classification at this prompt version.

    Ordered by url so a budget ceiling cuts the same list the same way twice.
    Without it the order is whatever the engine returns, which differs between
    the sqlite the tests use and the Postgres that runs -- so *which* articles
    a truncated run paid for would be unreproducible and untestable.

    Args:
        session: Open session.
        prompt_version: Classifier version, e.g. "v7".
        exclude_source_files: `raw_articles.source_file` values to leave out.
            This is how a leg switched off in config stops costing money: its
            rows are already in the table from earlier firings, and skipping
            the fetch does nothing about them.

    Returns:
        URLs needing classification. Empty means there is nothing to pay for,
        which is the steady state of a healthy schedule.
    """
    classified = select(m.RawLlmResponse.url).where(
        m.RawLlmResponse.prompt_version == prompt_version
    )
    query = select(m.RawArticle.url).where(m.RawArticle.url.not_in(classified))
    if exclude_source_files:
        query = query.where(
            m.RawArticle.source_file.not_in(tuple(exclude_source_files)))
    return list(session.scalars(query.order_by(m.RawArticle.url)))


def settings(path: Path = CONFIG) -> dict:
    """The `classification` block of config/pipeline.yaml."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))["classification"]


def classify_new(
    session: Session,
    prompt_version: str,
    budget: Budget | None = None,
    articles_path: Path | None = None,
    config_path: Path = CONFIG,
    exclude_source_files: tuple[str, ...] = (),
) -> dict:
    """Classify every article this prompt version has not seen.

    Args:
        session: Open session, used for the work list and month-to-date spend.
        prompt_version: Classifier version to fill in.
        budget: Ceiling to run under; built from config when omitted.
        articles_path: Read the article records from this file instead of
            from `raw_articles`. Kept for tests and one-off runs over a
            corpus that is not loaded; the default reads the database, so
            every source file's rows are classified.
        config_path: Pipeline config.
        exclude_source_files: Corpus files whose rows must not be
            classified, for a leg switched off in config.

    Returns:
        ``{pending, classified, skipped_for_budget, failures, cost_usd, bands,
        budget}``. `pending: 0` short-circuits before importing the classifier
        at all, so a run with nothing to do needs no API key.
    """
    import json

    pending = pending_urls(session, prompt_version, exclude_source_files)
    if not pending:
        return {
            "pending": 0, "classified": 0, "skipped_for_budget": 0,
            "failures": [], "cost_usd": 0.0, "bands": {},
            "budget": budget.snapshot() if budget else None,
        }

    import score_announcements as scorer

    if articles_path is not None:
        corpus = json.loads(articles_path.read_text(encoding="utf-8"))
        wanted = set(pending)
        articles = [a for a in corpus if a["url"] in wanted]
    else:
        # Read the payloads from the rows the work list was built from, rather
        # than re-reading one corpus file. Two things were wrong with the file.
        #
        # It is not the only source any more: `raw_articles` carries a row per
        # leg that produces articles, and filtering one file against a work
        # list built from the whole table silently drops every row loaded from
        # any other file. Those rows stay pending, are re-listed every firing,
        # never cost anything, and never produce a classification -- while the
        # leg reports a healthy ingest.
        #
        # And on the deployed container the file resets to the image copy every
        # firing, because there is no disk (D31). An article ingested tonight
        # and left pending by a budget ceiling could then never be classified
        # at all: the work list still names it, and the file no longer holds
        # its text. `_settled_urls` documents that hazard and works around it
        # by refetching the page; reading the payload here removes it.
        articles = [
            row.payload for row in session.scalars(
                select(m.RawArticle).where(m.RawArticle.url.in_(pending))
            )
        ]

    budget = budget if budget is not None else Budget.from_config(session, config_path)
    config = settings(config_path)

    summary = scorer.run(
        articles,
        model=config["model"],
        workers=config["workers"],
        batch=config.get("batch", False),
        budget=budget,
    )

    return {
        "pending": len(pending),
        "classified": summary["classified"],
        "skipped_for_budget": summary["skipped_for_budget"],
        "failures": summary["failures"],
        "cost_usd": summary["cost_usd"],
        "bands": summary["bands"],
        "budget": budget.snapshot(),
    }


def budget_breach(result: dict) -> BudgetExceeded | None:
    """The exception a completed classification implies, if any.

    Returned rather than raised: the stage deliberately does not fail the run —
    the ingest already happened and the ETL should still commit — but the alerter
    needs to know a ceiling was hit and which one.

    Args:
        result: A :func:`classify_new` result.

    Returns:
        The breach, or None if the run stayed inside its ceilings.
    """
    if not result.get("skipped_for_budget"):
        return None
    snapshot = result.get("budget") or {}
    if snapshot.get("run_usd", 0) >= snapshot.get("run_limit", float("inf")):
        return BudgetExceeded("per-run", snapshot["run_usd"], snapshot["run_limit"])
    return BudgetExceeded(
        "per-month", snapshot.get("month_usd", 0), snapshot.get("month_limit", 0)
    )
