"""Database schema: raw payloads, config mirrors, clean tables, and the joins.

Three layers, loaded in order:

* **raw_*** — verbatim JSON payloads from the research pipeline's artifacts,
  upsert-only. The DB accumulates history; the file register is a rolling
  window (fetch drops articles older than ~3 months silently).
* **ref_* / holding*** — mirrors of config/*.yaml, wiped and reloaded each run.
  YAML stays the source of truth; the mirror exists so joins are queryable.
* **articles / classifications / article_* / connections** — derived from raw,
  deleted and rebuilt per article. `connections` is the investment-side join;
  `article_practices` ranked by `ai_score` is the AI-team side.

Vocabulary values (sign, magnitude, action, ...) are TEXT, not DB enums: the
vocabulary lives in config/ and is enforced by config/validate.py at load time.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB on Postgres, plain JSON elsewhere (sqlite in tests).
JSONVariant = sa.JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    """Timezone-aware now, used as a Python-side default."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for every table."""


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------

class PipelineRun(Base):
    """One invocation of a pipeline command, with its outcome.

    ``status='failed' AND alerted_at IS NULL`` is the query a system-failure
    alerter will poll; the notifier itself is deferred (docs/decisions.md).
    """

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]  # load | rebuild | connect — what main() actually writes
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    status: Mapped[str] = mapped_column(default="running")  # running | succeeded | failed
    stats: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    watermarks: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None]
    alerted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class GoldSnapshot(Base):
    """A gold-set evaluation captured at a point in time, for the drift view."""

    __tablename__ = "gold_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_version: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    metrics: Mapped[dict] = mapped_column(JSONVariant)
    run_id: Mapped[int | None] = mapped_column(sa.ForeignKey("pipeline_runs.id"))


# --------------------------------------------------------------------------
# Raw layer
# --------------------------------------------------------------------------

class RawArticle(Base):
    """One fetched article, payload verbatim from announcements.json."""

    __tablename__ = "raw_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(unique=True)
    payload: Mapped[dict] = mapped_column(JSONVariant)
    content_hash: Mapped[str]
    source_file: Mapped[str]
    first_loaded_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    load_run_id: Mapped[int | None] = mapped_column(sa.ForeignKey("pipeline_runs.id"))


class RawClassification(Base):
    """One classifier result, payload verbatim from the per-URL score cache."""

    __tablename__ = "raw_classifications"
    __table_args__ = (sa.UniqueConstraint("url", "prompt_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str]
    prompt_version: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONVariant)
    loaded_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    load_run_id: Mapped[int | None] = mapped_column(sa.ForeignKey("pipeline_runs.id"))


class RawCost(Base):
    """One LLM call's cost record, verbatim from announcement_cost.json."""

    __tablename__ = "raw_costs"
    __table_args__ = (sa.UniqueConstraint("url", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str]
    model: Mapped[str]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cache_write_tokens: Mapped[int] = mapped_column(default=0)
    cache_read_tokens: Mapped[int] = mapped_column(default=0)
    usd: Mapped[float]
    seconds: Mapped[float]
    at: Mapped[str]  # ISO string as recorded; precision is the log's, not ours
    load_run_id: Mapped[int | None] = mapped_column(sa.ForeignKey("pipeline_runs.id"))


# --------------------------------------------------------------------------
# Reference layer (mirrors of config/*.yaml)
# --------------------------------------------------------------------------

class RefLab(Base):
    """A tracked lab from sources.yaml."""

    __tablename__ = "ref_labs"

    id: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str]
    config_version: Mapped[int]


class RefMechanism(Base):
    """A transmission mechanism from mechanisms.yaml."""

    __tablename__ = "ref_mechanisms"

    id: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str]
    description: Mapped[str]
    polarity_note: Mapped[str]
    config_version: Mapped[int]


class RefCategory(Base):
    """A holding category from categories.yaml."""

    __tablename__ = "ref_categories"

    id: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str]
    definition: Mapped[str]
    boundary: Mapped[str]
    lab_signal_routable: Mapped[bool]
    config_version: Mapped[int]


class RefPractice(Base):
    """An engineering practice from practices.yaml."""

    __tablename__ = "ref_practices"

    id: Mapped[str] = mapped_column(primary_key=True)
    label: Mapped[str]
    description: Mapped[str]
    dimensions: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    config_version: Mapped[int]


class Holding(Base):
    """A Technology Leaders position: holdings.yaml joined to companies.yaml."""

    __tablename__ = "holdings"

    isin: Mapped[str] = mapped_column(primary_key=True)
    # The legal name from companies.yaml, not the custodian string in
    # holdings.yaml: the latter is a mangled statement field ("FT Inter Inc.
    # Reg. Shares Cl. Ao. N.") that no article will ever contain.
    name: Mapped[str]
    custodian_name: Mapped[str]  # kept: the tie back to the Vermoegensaufstellung
    aliases: Mapped[list] = mapped_column(JSONVariant, default=list)
    ticker: Mapped[str | None]
    ticker_verified: Mapped[bool] = mapped_column(default=False)
    weight_pct: Mapped[float]
    ai_role: Mapped[str]
    holdings_version: Mapped[int]
    companies_version: Mapped[int]


class HoldingCategory(Base):
    """Category membership of a holding (holdings.yaml `categories`)."""

    __tablename__ = "holding_categories"

    isin: Mapped[str] = mapped_column(sa.ForeignKey("holdings.isin"), primary_key=True)
    category_id: Mapped[str] = mapped_column(sa.ForeignKey("ref_categories.id"), primary_key=True)


class HoldingMechanism(Base):
    """A mechanism edge a company declares (companies.yaml `mechanisms`)."""

    __tablename__ = "holding_mechanisms"
    __table_args__ = (sa.UniqueConstraint("isin", "mechanism_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    isin: Mapped[str] = mapped_column(sa.ForeignKey("holdings.isin"))
    mechanism_id: Mapped[str] = mapped_column(sa.ForeignKey("ref_mechanisms.id"))
    sign: Mapped[str]
    magnitude: Mapped[str]
    confidence: Mapped[str]
    why: Mapped[str]
    source: Mapped[str | None]


class HoldingLabExposure(Base):
    """A direct lab edge (companies.yaml `lab_exposure`).

    `lab` is deliberately not a foreign key: an edge naming a lab that is not
    yet in sources.yaml is a *dormant* edge, kept and flagged rather than
    rejected — adding the lab to sources.yaml activates it.
    """

    __tablename__ = "holding_lab_exposure"
    __table_args__ = (sa.UniqueConstraint("isin", "lab", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    isin: Mapped[str] = mapped_column(sa.ForeignKey("holdings.isin"))
    lab: Mapped[str]
    kind: Mapped[str]  # equity | revenue_contract | cloud_partnership | supply | credit_support
    sign: Mapped[str]
    magnitude: Mapped[str]
    confidence: Mapped[str]
    why: Mapped[str]
    source: Mapped[str | None]
    is_dormant: Mapped[bool] = mapped_column(default=False)


# --------------------------------------------------------------------------
# Clean layer
# --------------------------------------------------------------------------

class Article(Base):
    """Fetch-derived fields of one announcement."""

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(unique=True)
    raw_article_id: Mapped[int] = mapped_column(sa.ForeignKey("raw_articles.id"))
    lab: Mapped[str] = mapped_column(sa.ForeignKey("ref_labs.id"))
    title: Mapped[str]
    published_on: Mapped[date]
    text: Mapped[str]
    text_source: Mapped[str]
    feed_category: Mapped[str | None]
    archive_snapshot: Mapped[str | None]


class Classification(Base):
    """Classifier output for one article, with both scores recomputed."""

    __tablename__ = "classifications"
    __table_args__ = (sa.UniqueConstraint("article_id", "prompt_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(sa.ForeignKey("articles.id"))
    prompt_version: Mapped[str]
    scoring_version: Mapped[int]
    event_type: Mapped[str]
    summary: Mapped[str]
    is_signal: Mapped[bool | None]
    notable: Mapped[bool]
    notable_reason: Mapped[str]
    dropped_tags: Mapped[list] = mapped_column(JSONVariant, default=list)
    score: Mapped[float]
    band: Mapped[str]
    ai_score: Mapped[float]
    ai_band: Mapped[str]


class ArticleMechanism(Base):
    """One mechanism tag on one classification."""

    __tablename__ = "article_mechanisms"

    id: Mapped[int] = mapped_column(primary_key=True)
    classification_id: Mapped[int] = mapped_column(sa.ForeignKey("classifications.id"))
    mechanism_id: Mapped[str] = mapped_column(sa.ForeignKey("ref_mechanisms.id"))
    sign: Mapped[str]
    magnitude: Mapped[str]
    confidence: Mapped[str]
    reason: Mapped[str]
    quote: Mapped[str]
    quote_repaired: Mapped[bool] = mapped_column(default=False)
    ordinal: Mapped[int]


class ArticleCategory(Base):
    """One category tag on one classification. Category tags carry no magnitude."""

    __tablename__ = "article_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    classification_id: Mapped[int] = mapped_column(sa.ForeignKey("classifications.id"))
    category_id: Mapped[str] = mapped_column(sa.ForeignKey("ref_categories.id"))
    sign: Mapped[str]
    confidence: Mapped[str]
    reason: Mapped[str]
    quote: Mapped[str]
    quote_repaired: Mapped[bool] = mapped_column(default=False)
    ordinal: Mapped[int]


class ArticlePractice(Base):
    """One practice tag on one classification — the AI-team join row."""

    __tablename__ = "article_practices"

    id: Mapped[int] = mapped_column(primary_key=True)
    classification_id: Mapped[int] = mapped_column(sa.ForeignKey("classifications.id"))
    practice_id: Mapped[str] = mapped_column(sa.ForeignKey("ref_practices.id"))
    action: Mapped[str]  # adopt | investigate | watch
    impact: Mapped[str]
    confidence: Mapped[str]
    dimensions: Mapped[list] = mapped_column(JSONVariant, default=list)
    reason: Mapped[str]
    quote: Mapped[str]
    quote_repaired: Mapped[bool] = mapped_column(default=False)
    ordinal: Mapped[int]


class Connection(Base):
    """One article-to-holding link, denormalized so a row explains itself.

    Derived and rebuilt wholesale, so the denormalized copies cannot drift.
    `route` says which join fired (mechanism | category | lab_exposure | named);
    `via` is the join key (mechanism id, category id, "lab:kind", or the
    matched name). The article- and holding-side columns are NULL where the
    route has no such side (a category membership has no sign; a named mention
    has no holding edge).
    """

    __tablename__ = "connections"
    __table_args__ = (sa.UniqueConstraint("article_id", "isin", "route", "via"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(sa.ForeignKey("articles.id"))
    isin: Mapped[str] = mapped_column(sa.ForeignKey("holdings.isin"))
    route: Mapped[str]
    via: Mapped[str]
    direction: Mapped[str]  # positive | negative | mixed
    strength: Mapped[float]
    article_sign: Mapped[str | None]
    article_magnitude: Mapped[str | None]
    article_confidence: Mapped[str | None]
    article_reason: Mapped[str | None]
    article_quote: Mapped[str | None]
    holding_sign: Mapped[str | None]
    holding_magnitude: Mapped[str | None]
    holding_confidence: Mapped[str | None]
    holding_why: Mapped[str | None]
    holding_source: Mapped[str | None]
