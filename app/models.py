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

    # At most one firing at a time, enforced by the database rather than by a
    # check in one of the processes. Two firings interleave writes to the same
    # corpus file and both do read-modify-write on the cost log, silently losing
    # records — and cost instrumentation is a graded requirement, not a nicety.
    #
    # A partial unique index rather than a lock or a flag: the manual trigger
    # (api/pipeline.py) and the nightly cron are separate processes on separate
    # containers, so nothing in-process can see both. Unique on `status` where
    # `status = 'running'` permits any number of succeeded and failed rows and
    # exactly one running one (docs/decisions.md D44).
    __table_args__ = (
        sa.Index(
            "ix_pipeline_runs_single_running",
            "status",
            unique=True,
            postgresql_where=sa.text("status = 'running'"),
            sqlite_where=sa.text("status = 'running'"),
        ),
    )

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


class RunSource(Base):
    """One (leg, source) attempt inside one run.

    `pipeline_runs` is the summary; this is the detail behind it. A run that
    ingested six sources and lost one says so here, which is what makes
    "the run succeeded" and "every source succeeded" separable claims.
    """

    __tablename__ = "run_sources"
    __table_args__ = (sa.UniqueConstraint("run_id", "leg", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(sa.ForeignKey("pipeline_runs.id"))
    leg: Mapped[str]  # announcements | papers | github
    source_id: Mapped[str]
    status: Mapped[str]  # succeeded | failed | skipped
    items_seen: Mapped[int] = mapped_column(default=0)
    items_new: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    duration_s: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None]


class SourceState(Base):
    """Cross-run state for one ingestion source.

    Per-request backoff answers a single bad request; it is the wrong tool for a
    source that is down for hours (docs/planning.md §4b). This table is the
    slower layer above it: `consecutive_failures` makes "down for N scheduled
    runs" a query rather than a guess, and `watermark` is what lets a fetch be
    incremental instead of re-deriving its window every time.

    `disabled` is a manual kill switch — an operator turning a source off is a
    different state from a source that keeps failing, and the two must not be
    confused in the alerting.
    """

    __tablename__ = "source_state"

    leg: Mapped[str] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(primary_key=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    watermark: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    last_error: Mapped[str | None]
    disabled: Mapped[bool] = mapped_column(default=False)


class Alert(Base):
    """One raised alert, of either kind, whether or not delivery succeeded.

    `kind` is the distinction CLAUDE.md requires: `system` means the pipeline
    broke, `content` means the pipeline found something. They share a table
    because they share a lifecycle (raise, deliver, record), not because they
    are the same thing — every consumer filters on `kind`.

    `dedupe_key` is what stops a week-long outage alerting on every firing. Each
    rule owns its key and builds it from whatever identifies the *episode* — the
    moment an outage began, the item an alert is about, the month a ceiling was
    breached in — never from the check itself, which repeats every run. The
    unique constraint is what actually enforces it.
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]  # system | content
    rule: Mapped[str]
    severity: Mapped[str]  # info | warning | critical
    subject: Mapped[str]
    body: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    dedupe_key: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    run_id: Mapped[int | None] = mapped_column(sa.ForeignKey("pipeline_runs.id"))
    sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    delivery_error: Mapped[str | None]
    # Acknowledgement, not deletion. The row stays in the history forever; what
    # this clears is the header badge, which counts unacknowledged system alerts
    # and so stays red until someone says they have seen them — there is no
    # window, and nothing ages out on its own.
    #
    # Cleared again by `dispatch` when the rules regenerate this row's episode
    # key, which is what stops an acknowledgement hiding a live fault. It has to
    # work that way round: `dedupe_key` holds still while a fault continues, so
    # an ongoing outage writes no new row to notice.
    acknowledged_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))



class Digest(Base):
    """One published digest: what the product said, for one audience, at one time.

    Persisted rather than recomputed on read. Every other derived table here is
    a pure function of committed files and may be dropped; a digest is not. It
    is a dated claim that these items were worth attention and the rest were
    not, and recomputing it against a later re-scored corpus would silently
    restate last week's opinions in today's terms.

    `stats` carries `considered` / `surfaced` / `suppressed`. The suppressed
    count is the point: the cut is the product, so a digest that cannot say how
    much it discarded is asserting taste rather than showing it.

    `payload` holds the rendered items verbatim, including every quote and
    source URL, so a digest resolves without re-reading the corpus it came from.
    """

    __tablename__ = "digests"

    # One digest per audience per window. A re-run of the same firing must
    # update the row it already wrote rather than publishing a second, subtly
    # different edition of the same period — the same idempotence rule the
    # rest of the pipeline follows.
    __table_args__ = (
        sa.UniqueConstraint("kind", "window_end", "prompt_version",
                            name="uq_digests_kind_window"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]  # investment | ai
    window_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    prompt_version: Mapped[str]
    stats: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    run_id: Mapped[int | None] = mapped_column(sa.ForeignKey("pipeline_runs.id"))


class FetchCache(Base):
    """One HTTP response body, keyed by URL, so a re-run does not re-fetch it.

    The papers harvesters each cached to disk under `research/docs/*_cache/`.
    That works on a laptop and does nothing in the deployment, which has no
    disk: every cron firing started cold and replayed ~70 requests at arXiv,
    which is what got the egress IP rate-limited (docs/decisions.md D53).
    Postgres is the only thing that persists there, so the cache lives here.

    `expires_at` is the part that is not optional. A permanent cache of a
    *discovery* URL — `au:"DeepSeek-AI"`, a paginated listing — would freeze
    the register at whatever it knew on the first run and report success
    forever after. Immutable content (a versioned arXiv id) stores NULL and is
    never re-fetched; everything else carries a TTL. See
    `research/papers/fetch_cache.py` for which is which and why.
    """

    __tablename__ = "fetch_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(sa.Text, unique=True)
    body: Mapped[str] = mapped_column(sa.Text)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    # NULL means immutable: no expiry, never re-fetched.
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


# Tables that survive a rebuild. Everything else in this schema is a pure
# function of committed files, so dropping it loses nothing; these seven are not
# — run history, per-source failure counts, raised alerts, drift snapshots,
# published digests and the fetch cache are only ever produced by a run that
# actually happened. `rebuild` dropping `pipeline_runs` was a real (if quiet)
# loss of history before this existed, and dropping `fetch_cache` would send
# the next run back to arXiv for everything it already has.
OPS_TABLES = frozenset(
    {"pipeline_runs", "gold_snapshots", "run_sources", "source_state", "alerts",
     "digests", "fetch_cache"}
)


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


class RawLlmResponse(Base):
    """One classifier result, payload verbatim from the per-URL score cache."""

    __tablename__ = "raw_llm_responses"
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


class RawPaper(Base):
    """One harvested paper, payload verbatim from a `<lab>_contributors.json`."""

    __tablename__ = "raw_papers"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(unique=True)  # the lab's own page, not arXiv
    lab: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONVariant)
    content_hash: Mapped[str]
    first_loaded_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    load_run_id: Mapped[int | None] = mapped_column(sa.ForeignKey("pipeline_runs.id"))


class RawGithubPerson(Base):
    """One person's aggregate for one org, verbatim from `aggregate_github`.

    Keyed on (org, login) rather than login alone: a lab can own more than one
    org, and the same login's contribution differs per org.
    """

    __tablename__ = "raw_github_people"
    __table_args__ = (sa.UniqueConstraint("org", "login"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org: Mapped[str]
    login: Mapped[str]
    lab: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONVariant)
    content_hash: Mapped[str]
    first_loaded_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    load_run_id: Mapped[int | None] = mapped_column(sa.ForeignKey("pipeline_runs.id"))


class RawGithubRepo(Base):
    """One repository's commit history for one org, verbatim from `harvest_github`.

    The bronze layer this leg was missing. `raw_github_people` holds the
    *aggregate* — one row per person, already reduced to counts and domains —
    so the commits it was computed from lived only in the on-disk
    `github_cache/`. That made the people register unrebuildable from the
    database and forced a full 12-month re-harvest on any container without a
    disk, which is the deployed shape (D32).

    `pushed_at` is the REST listing's value for the repo, and it is the
    incremental key: a repository whose `pushed_at` has not moved cannot have
    new commits, so it is never re-walked. That also closes a staleness bug in
    the disk cache it replaces, which had no max-age at all — once a repo was
    cached it was never refetched, however many commits it gained.
    """

    __tablename__ = "raw_github_repos"
    __table_args__ = (sa.UniqueConstraint("org", "repo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org: Mapped[str]
    repo: Mapped[str]
    pushed_at: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONVariant)
    content_hash: Mapped[str]
    first_loaded_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
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


class Person(Base):
    """One person at one lab.

    **Scoped to a lab, deliberately.** "Same name, different person" is a named
    failure mode for this project, and two researchers called Wei Zhang at
    DeepSeek and at DeepMind are two people until something says otherwise.
    Merging them across labs is a claim that needs evidence, so it is recorded
    as a proposal rather than performed silently — the same discipline
    `config/aliases.yaml` already applies within a lab.

    `lab` is not a foreign key, for the reason `HoldingLabExposure` gives: a
    person harvested from a lab later dropped from `sources.yaml` should survive
    as a record, not block the load.

    `source_kind` is part of the key, not decoration. Without it a GitHub login
    and a paper byline that happen to be the same string ("Ada") collapse into
    one person — the cross-leg guess this register explicitly refuses to make.
    Identity is resolved through :class:`PersonIdentity`, so the union of a
    person's leg-scoped rows is an evidence-based step, not a string match.
    """

    __tablename__ = "people"
    __table_args__ = (sa.UniqueConstraint("lab", "source_kind", "canonical_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lab: Mapped[str]
    source_kind: Mapped[str]  # paper | github
    canonical_name: Mapped[str]
    first_seen: Mapped[date | None]
    last_seen: Mapped[date | None]
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)


class PersonIdentity(Base):
    """One identifier that resolves to a person — the entity-resolution surface.

    Unique on (kind, value, lab), not (kind, value). A GitHub login really is
    globally one account, so the stricter key would correctly merge someone
    committing to two of *one* lab's orgs — but it would also merge someone
    committing to two *different* labs' orgs into a single person, which is a
    cross-lab employment claim this register is not entitled to make from commit
    data alone. Including `lab` keeps the common case right and refuses the
    interesting one, which is the correct direction to be wrong in.
    """

    __tablename__ = "person_identity"
    __table_args__ = (sa.UniqueConstraint("kind", "value", "lab"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(sa.ForeignKey("people.id"))
    kind: Mapped[str]  # paper_name | github_login
    value: Mapped[str]
    lab: Mapped[str]


class PersonEvidence(Base):
    """What proves a person exists, and how strongly they are tied to the lab.

    `ref_url` is required and is the project's "no citation, no insight" rule
    applied to people: a person with no resolvable source is not recorded.

    `tier` keeps evidence strengths apart that must never be conflated:

    * ``confirmed`` — a commit from a lab-owned email domain.
    * ``confirmed_org_wide`` — a parent-company domain (@google.com covers all
      of Alphabet, not DeepMind specifically), so real but weaker.
    * ``handle`` — matched the lab's work-account naming convention.
    * ``vendor`` — a real person, but a contractor rather than lab staff.
    * ``model_asserted`` — an LLM read the affiliation off a paper's byline.
      Kept structurally separate from every `confirmed` tier because of the hard
      rule in planning.md §11: an LLM must never populate an employment field
      unchecked. On Anthropic it asserted fellowship status for four people
      whose pages state no affiliation at all. This tier is a lead, not a fact.
    * ``unknown`` — seen, unattributed.
    """

    __tablename__ = "person_evidence"
    __table_args__ = (sa.UniqueConstraint("person_id", "source_kind", "ref_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(sa.ForeignKey("people.id"))
    source_kind: Mapped[str]  # paper | github
    ref_url: Mapped[str]
    observed_on: Mapped[date | None]
    tier: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)


class UnresolvedItem(Base):
    """Something a source found and could not resolve, kept with its reason.

    Every harvester already records these rather than dropping them
    (docs/handover.md §5). Giving them a table makes that structural instead of
    conventional: a register whose gaps are invisible reads as complete, and
    "we found 27 Meta candidates and resolved 6" is a materially different claim
    from "Meta published 6 papers".
    """

    __tablename__ = "unresolved_items"
    __table_args__ = (sa.UniqueConstraint("leg", "source_id", "identifier"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    leg: Mapped[str]
    source_id: Mapped[str]
    kind: Mapped[str]  # paper | person
    identifier: Mapped[str]  # title, url or name — whatever the source had
    reason: Mapped[str]
    first_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=utcnow)
    run_id: Mapped[int | None] = mapped_column(sa.ForeignKey("pipeline_runs.id"))


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
