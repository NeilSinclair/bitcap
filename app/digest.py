"""The periodic digest: one dated cut of the corpus, per audience.

The dashboard shows every classified article. A digest is the opposite claim —
that a handful of them are worth a reader's attention over the last window and
the rest are not. Everything here is that claim, made explicit:

* **The cut is the product.** Each digest records `considered`, `surfaced` and
  `suppressed`, so the taste is auditable rather than asserted. A digest that
  cannot say how much it threw away is just a shorter list.
* **The unit is the event, not the connection.** One xAI sentence — "trained
  across tens of thousands of NVIDIA GB300 GPUs" — fires against seven holdings
  at once (docs/insights.md). Rendered per connection that is seven rows for one
  fact, which is precisely the noise the brief asks us to keep out. Rendered per
  event it is one row that names its top holdings and counts the rest.
* **Two audiences, one core.** Both sides read the same `classifications` and
  the same tags. What differs is the selection rule and the framing, not the
  pipeline behind them — the brief is explicit that this must not be two
  systems.
* **Every item carries its own evidence.** Quote and source URL travel in the
  payload, so a published digest resolves without re-reading the corpus that
  produced it, and stays resolvable after the corpus moves on.

Selection thresholds live in `config/digest.yaml`, never here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.connect import match_name

CONFIG = Path(__file__).resolve().parents[1] / "config" / "digest.yaml"

INVESTMENT = "investment"
AI = "ai"
KINDS = (INVESTMENT, AI)

# Rank order for a band, so "at or above medium" is a comparison rather than a
# set membership test that silently accepts an unknown band.
_BAND_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def settings(path: Path = CONFIG) -> dict:
    """The digest block of config/digest.yaml.

    Args:
        path: Config file to read. Overridden in tests.

    Returns:
        The parsed config mapping.
    """
    return yaml.safe_load(path.read_text())


def _band_at_least(band: str, floor: str) -> bool:
    """Whether `band` ranks at or above `floor`.

    An unrecognised band ranks below everything rather than raising: a
    vocabulary change should quieten the digest, never crash the run that
    publishes it.
    """
    return _BAND_RANK.get(band, -1) >= _BAND_RANK.get(floor, 99)


# Periods are a fixed grid anchored here, not an offset from whenever a run
# happened to start. Everything in the docstring below depends on that.
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def window_for(at: datetime, config: dict) -> tuple[datetime, datetime]:
    """The period one digest covers: the last complete slot of the fixed grid.

    **Quantised, and this is the whole point.** Taking the window as
    `[run.started_at - 48h, run.started_at]` looked right and was wrong twice
    over. `PipelineRun.started_at` is a per-row wall clock, so the idempotence
    key `(kind, window_end, prompt_version)` was microsecond-unique and could
    never collide — every firing published a *new* edition instead of updating
    one, forever, and a `--dry-run` rehearsal entered the permanent record. And
    with a daily cron over a 48-hour lookback, consecutive editions overlapped
    by a day: the article published on the 3rd appeared in both the 3rd's and
    the 4th's editions, in two reports that each looked complete.

    Snapping to a grid fixes both. Periods are `[EPOCH + kW, EPOCH + (k+1)W)`,
    so any number of firings inside one period resolve to the same
    `window_end` and update one edition, and consecutive editions partition the
    timeline exactly — every article belongs to one.

    The grid also decouples the window from the cron. A daily cron over a
    48-hour period publishes each period once, on the first firing after it
    closes, and harmlessly re-publishes it on the second; the two settings no
    longer have to agree.

    The cost is freshness: the newest *published* edition can be up to one
    period behind. That is why `/api/digests/preview` exists and why the digest
    page opens on it — the live window is the default view, and the published
    editions are the archive.

    Args:
        at: A moment inside or after the period to publish, normally the run's
            start time. Naive datetimes are read as UTC.
        config: Parsed `config/digest.yaml`.

    Returns:
        Start and end of the last complete period, timezone-aware in UTC.
    """
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    at = at.astimezone(timezone.utc)
    width = timedelta(hours=config["window_hours"])
    boundary = EPOCH + ((at - EPOCH) // width) * width
    return boundary - width, boundary


def _in_window(article: m.Article, start: datetime, end: datetime) -> bool:
    """Whether an article's publication date falls in the window.

    `published_on` is a date, not a timestamp — most labs publish without a time
    and the ones that do are not comparable across timezones. So the comparison
    is at date resolution; pretending to hour precision we do not have would
    silently drop items published on the boundary day.

    **Half-open, `(start, end]`.** Closed at both ends a 48-hour window spans
    three calendar days, and consecutive editions then both carry every article
    published on the day they share — the same event reported twice, in two
    reports that each look complete. Half-open makes consecutive editions a
    partition of the timeline: every article belongs to exactly one.
    """
    return start.date() < article.published_on <= end.date()


def _holdings_line(conns: list[m.Connection], names: dict, shown: int) -> dict:
    """Roll a single event's connections up to one holding-level summary.

    An article can connect to the same holding by several routes; a reader
    wants the holding named once, at its strongest link. Above `shown`, the
    remainder becomes a count rather than more rows — the fan-out control.

    Args:
        conns: This article's connections, any order.
        names: isin -> display name.
        shown: How many holdings to name before collapsing.

    Returns:
        ``{"named": [...], "more": int, "total": int}``.
    """
    best: dict[str, m.Connection] = {}
    for c in conns:
        if c.isin not in best or c.strength > best[c.isin].strength:
            best[c.isin] = c
    ranked = sorted(best.values(), key=lambda c: c.strength, reverse=True)
    named = [
        {
            "holding": names.get(c.isin, c.isin),
            "isin": c.isin,
            "direction": c.direction,
            "strength": round(c.strength, 2),
            "why": c.holding_why or c.article_reason or "",
        }
        for c in ranked[:shown]
    ]
    return {"named": named, "more": max(0, len(ranked) - shown), "total": len(ranked)}


def build(
    session: Session,
    kind: str,
    prompt_version: str | tuple[str, ...],
    end: datetime,
    config: dict | None = None,
    quantise: bool = True,
) -> dict:
    """Select and render one audience's digest for the window ending at `end`.

    Reads only; persisting is `publish`'s job, so a digest can be previewed
    without writing one.

    Args:
        session: Open session.
        kind: `investment` or `ai`.
        prompt_version: Which classification run to read; a tuple spans several,
            so one digest can rank announcements and papers together.
        end: Right edge of the publication window.
        config: Parsed config; read from disk when omitted.
        quantise: Snap the window to the fixed grid and take the last *complete*
            period. True for anything that publishes — the grid is what makes
            `publish` idempotent and consecutive editions a partition. False for
            the unpersisted preview, which takes the rolling `[end - W, end]`
            instead: quantised, the preview's newest day is always the one that
            closed, so on the 5th it read "up to the 3rd" and looked stale.

            **This flag also selects which width is read.** True reads
            `window_hours`, False reads `preview_window_hours`, and they are
            deliberately different numbers (D79) — a daily published archive
            behind a rolling week. They were one setting until then and the
            same width by accident.

    Returns:
        ``{"kind", "window_start", "window_end", "stats", "items"}``. `stats`
        carries `considered` / `surfaced` / `suppressed` / `matched_rule` —
        `matched_rule` is how many passed the selection rule before the
        `max_items` cap, so the two causes of suppression stay separable.

    Raises:
        ValueError: `kind` is not one of `KINDS`.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown digest kind: {kind!r}")

    config = config or settings()
    if quantise:
        start, end = window_for(end, config)   # `end` becomes the period boundary
    else:
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        end = end.astimezone(timezone.utc)
        # `preview_window_hours`, NOT `window_hours`. The two surfaces are
        # deliberately different widths (D79): the archive is a daily grid, the
        # live view is a rolling week. They were one value and the same width by
        # accident, which made the 48-hour editions in the archive fossils of an
        # older setting rather than a thing the product keeps producing.
        start = end - timedelta(hours=config["preview_window_hours"])
    rules = config[kind]

    labs = {r.id: r.label for r in session.scalars(select(m.RefLab))}
    prac_labels = {r.id: r.label for r in session.scalars(select(m.RefPractice))}
    mech_labels = {r.id: r.label for r in session.scalars(select(m.RefMechanism))}
    names = {h.isin: (match_name(h.name) or h.name) for h in session.scalars(select(m.Holding))}

    classifications = {
        c.article_id: c
        for c in session.scalars(
            select(m.Classification).where(
                m.Classification.prompt_version.in_(
                    (prompt_version,) if isinstance(prompt_version, str)
                    else tuple(prompt_version)))
        )
    }

    conns_by_article: dict[int, list[m.Connection]] = defaultdict(list)
    for c in session.scalars(select(m.Connection)):
        conns_by_article[c.article_id].append(c)

    cls_ids = [c.id for c in classifications.values()]
    mechs: dict[int, list] = defaultdict(list)
    for t in session.scalars(
        select(m.ArticleMechanism)
        .where(m.ArticleMechanism.classification_id.in_(cls_ids))
        .order_by(m.ArticleMechanism.ordinal)
    ):
        mechs[t.classification_id].append(t)
    pracs: dict[int, list] = defaultdict(list)
    for t in session.scalars(
        select(m.ArticlePractice)
        .where(m.ArticlePractice.classification_id.in_(cls_ids))
        .order_by(m.ArticlePractice.ordinal)
    ):
        pracs[t.classification_id].append(t)

    # Folded near-duplicates never reach the cut. An edition carries at most
    # eight items, so publishing a launch post and its forum restatement as two
    # of them spends a quarter of the space saying one thing twice.
    #
    # **The anchor is chosen here, not read from the table.** `is_anchor` is
    # picked once over the whole corpus on a single significance score, and this
    # edition is neither. Two ways that goes wrong if trusted:
    #
    # * *Out of window.* A release train spans days; its corpus-wide anchor can
    #   sit outside this window entirely, so every member that did ship inside it
    #   folds against an absent row and the section renders empty for a repo that
    #   shipped four versions.
    # * *Wrong audience.* One anchor serves both cuts. `event_type` is a
    #   multiplicative term in the investment score and absent from the AI score,
    #   so the member that ranks highest overall can score zero on this axis —
    #   and the member carrying the investment signal is already folded. The item
    #   disappears from the investment digest and the loss reads as intentional.
    #
    # Membership is the durable fact and lives in the table; which member speaks
    # for the group is a property of the view, so each view decides it.
    axis = "score" if kind == INVESTMENT else "ai_score"
    grouping = {g.article_id: g for g in session.scalars(select(m.ArticleGroup))}
    groups = {article_id: g.group_id for article_id, g in grouping.items()}
    methods = {g.group_id: g.method for g in grouping.values()}

    in_window = [
        art for art in session.scalars(select(m.Article))
        if classifications.get(art.id) is not None and _in_window(art, start, end)
    ]

    def rank(art) -> tuple:
        """Highest score on this edition's axis, then by date.

        The date tie-break flips for release trains, and it decides every one of
        them: within a train each release usually carries the same score, so the
        secondary key is the whole decision. A repo's card must name the version
        it is on — anchoring earliest published `claude-code v2.1.258` while
        v2.1.260 sat folded inside it, which states the opposite of what the
        group means. Elsewhere earliest wins, because being early is the
        product's claim.
        """
        latest = methods.get(groups.get(art.id, f"g{art.id}")) == "release_train"
        direction = 1 if latest else -1
        return (getattr(classifications[art.id], axis) or 0.0,
                direction * art.published_on.toordinal(), direction * art.id)

    def item_for(art):
        """Render `art` under this edition's rule, or None if it does not pass."""
        cls = classifications[art.id]
        return (
            _investment_item(art, cls, conns_by_article[art.id], mechs[cls.id],
                             labs, names, mech_labels, rules, config)
            if kind == INVESTMENT
            else _ai_item(art, cls, pracs[cls.id], labs, prac_labels, rules)
        )

    # Ranked candidates per group, best first. The whole list is kept rather
    # than just the winner because the top-ranked member is not necessarily the
    # one that *passes*: `_investment_item` gates on connection strength and
    # band, and `rank` orders on score. A group whose highest scorer carries no
    # holding link would emit nothing at all while a folded member with a 0.9
    # NVIDIA connection sat behind it — the link never reaching a reader, and
    # `collapsed` claiming another row already said it when no row did.
    #
    # So the group is represented by its best member that the audience's own
    # rule accepts, and only genuinely says nothing when none of them do.
    candidates: dict[str, list] = {}
    for art in in_window:
        candidates.setdefault(groups.get(art.id, f"g{art.id}"), []).append(art)

    considered, selected, collapsed = 0, [], 0
    for members in candidates.values():
        members.sort(key=rank, reverse=True)
        considered += 1
        for index, art in enumerate(members):
            item = item_for(art)
            if item is not None:
                if len(members) > 1:
                    # A card that stands for several documents has to say so.
                    # The merge is a decision the product made on the reader's
                    # behalf, and until now the only trace of it was the
                    # edition-level `collapsed` count: a reader could see that
                    # eleven rows were folded somewhere, but not which card ate
                    # what, or why. The dashboard has carried `groupReason`
                    # since grouping shipped; this is the same string.
                    #
                    # `len(members)`, NOT `ArticleGroup.group_size`. The stored
                    # size counts the whole corpus-wide group, which can include
                    # documents published outside this window that were never
                    # candidates here. Reporting it would tell a reader the card
                    # speaks for three documents when this edition only folded
                    # two. `len(members)` is what was actually collapsed, and it
                    # is the same number `collapsed` is accumulated from below.
                    group = grouping.get(art.id)
                    item["groupSize"] = len(members)
                    item["groupMethod"] = group.method if group else "singleton"
                    item["groupReason"] = group.reason if group else ""
                selected.append(item)
                collapsed += len(members) - 1
                break
        else:
            # Nobody passed. The group is suppressed on merit, not collapsed —
            # counting it as collapsed would report a merge as the reason a
            # reader saw nothing.
            collapsed += len(members) - 1

    # TWO SORTS, AND THE ORDER OF THEM IS THE POINT.
    #
    # Selection is on `rank` alone — connection strength then score for the
    # investment cut, ai_score then how many practices are actionable for the
    # AI cut. Display is newest first, with `rank` breaking ties inside a day.
    #
    # They cannot be one sort, because `max_items` cuts between them. Sorting
    # by date before the cut fills the edition with whatever is most recent and
    # drops a higher-scoring launch from earlier in the window. That is a real
    # loss on the live view, whose `preview_window_hours` spans a week; on a
    # 24-hour published edition the two sorts nearly agree, and the ordering
    # still has to be right there because both read this one function.
    # So the edition is chosen on merit and then read in the order a reader
    # expects: latest at the top, most important first within a day.
    selected.sort(key=lambda i: i["rank"], reverse=True)
    items = selected[: rules["max_items"]]
    items.sort(key=lambda i: (i["date"], i["rank"]), reverse=True)
    for item in items:
        del item["rank"]

    return {
        "kind": kind,
        "window_start": start,
        "window_end": end,
        "stats": {
            "considered": considered,
            "surfaced": len(items),
            # Everything in the window that did not make the published cut,
            # whether it failed the rule or lost to the cap. One number, because
            # a reader asking "what did you not tell me" does not care which.
            "suppressed": considered - len(items),
            "matched_rule": len(selected),
            # Kept separate from `suppressed`: those items lost on merit or to
            # the cap, these were never candidates because another row in the
            # edition already says the same thing. Conflating them would make a
            # collapse read as a rejection.
            "collapsed": collapsed,
        },
        "items": items,
    }


def _investment_item(art, cls, conns, mech_tags, labs, names, mech_labels, rules, config):
    """Render one event for the investment audience, or None if it is suppressed.

    Two ways in. A connection at or above `min_strength` means the event reaches
    a name in the book. A band at `always_band` with no connection at all still
    surfaces: a lab-level shift that does not yet touch a holding is the early
    signal this system exists to catch, and requiring a connection would filter
    out exactly that case.

    Returns:
        The rendered item with a private `rank` key, or None.
    """
    strong = [c for c in conns if c.strength >= rules["min_strength"]]
    if not strong and not _band_at_least(cls.band, rules["always_band"]):
        return None

    # Only the connections that cleared the bar. `strong or conns` fell back to
    # *every* connection when an item surfaced on band alone — so a card selected
    # precisely because it touches nothing yet rendered "2 positions · NVIDIA
    # 0.20" beside `peakStrength: 0.0`, and a PM read it as a hit on NVIDIA.
    # The weaker links are counted instead, so the card can say they exist
    # without claiming them.
    rolled = _holdings_line(strong, names, config["max_holdings_shown"])
    below = len({c.isin for c in conns}) - rolled["total"]
    top = mech_tags[0] if mech_tags else None
    peak = max((c.strength for c in strong), default=0.0)

    return {
        "rank": (peak, cls.score),
        "id": art.id,
        "date": str(art.published_on),
        "lab": labs.get(art.lab, art.lab),
        "title": art.title,
        "sourceUrl": art.url,
        "score": cls.score,
        "band": cls.band,
        "eventType": cls.event_type,
        "summary": cls.summary,
        # What it means, in the system's own words, and the sentence it means it
        # from. The brief's "actionable" test is that a reader knows what to do,
        # which needs the mechanism and the direction, not just the headline.
        "mechanism": (
            {
                "label": mech_labels.get(top.mechanism_id, top.mechanism_id),
                "sign": top.sign,
                "magnitude": top.magnitude,
                "confidence": top.confidence,
                "reason": top.reason,
                "quote": top.quote,
            }
            if top
            else None
        ),
        "holdings": rolled,
        # Links this article has that did not clear `min_strength`. Shown as a
        # count, never as named positions: naming them would be the digest
        # asserting exactly what it just declined to assert.
        "belowThreshold": below,
        "peakStrength": round(peak, 2),
    }


def _ai_item(art, cls, prac_tags, labs, prac_labels, rules):
    """Render one event for the AI team, or None if it is suppressed.

    The gate is the classification's own recommendation: a practice tagged
    `adopt` or `investigate`. `watch` is the null action, and surfacing an item
    whose own analysis says to do nothing pads the digest with non-decisions.

    Returns:
        The rendered item with a private `rank` key, or None.
    """
    actionable = [t for t in prac_tags if t.action in rules["actions"]]
    if not actionable or not _band_at_least(cls.ai_band, rules["min_band"]):
        return None

    return {
        "rank": (cls.ai_score, len(actionable)),
        "id": art.id,
        "date": str(art.published_on),
        "lab": labs.get(art.lab, art.lab),
        "title": art.title,
        "sourceUrl": art.url,
        "score": cls.ai_score,
        "band": cls.ai_band,
        "eventType": cls.event_type,
        "summary": cls.summary,
        "practices": [
            {
                "label": prac_labels.get(t.practice_id, t.practice_id),
                "action": t.action,
                "impact": t.impact,
                "confidence": t.confidence,
                "dimensions": t.dimensions,
                "reason": t.reason,
                "quote": t.quote,
            }
            for t in actionable
        ],
    }


def publish(
    session: Session,
    prompt_version: str | tuple[str, ...],
    end: datetime,
    run_id: int | None = None,
    config: dict | None = None,
) -> list[m.Digest]:
    """Build and persist both audiences' digests for one window.

    Idempotent on `(kind, window_start, window_end, prompt_version)`: re-running
    a firing updates the edition it already published rather than issuing a
    second, subtly different one for the same period.

    **The span, not just its end.** Two editions can end at the same midnight
    over different periods — a 48-hour report and a 24-hour one, which is
    exactly what changing `window_hours` produces. Keying on the end alone made
    those one row and overwrote the older edition in place (migration 0013).

    A digest may read several classification versions (announcements and papers
    carry their own), but the uniqueness key is one column. The *first* version
    given is the label, and the full set is recorded in `stats["versions"]` --
    so the key stays stable while the edition still says what produced it.

    Args:
        session: Open session; the caller commits.
        prompt_version: Which classification run to read. A tuple spans several.
        end: Right edge of the publication window.
        run_id: Firing that produced this, when there is one.
        config: Parsed config; read from disk when omitted.

    Returns:
        The persisted rows, investment first.
    """
    config = config or settings()
    versions = ((prompt_version,) if isinstance(prompt_version, str)
                else tuple(prompt_version))
    label = versions[0]
    rows = []
    for kind in KINDS:
        built = build(session, kind, prompt_version, end, config)
        # Matched on the WHOLE span. Keying on `window_end` alone let an
        # edition of a different width claim an existing row and overwrite it,
        # keeping the old `window_start` because the start is only assigned on
        # creation -- a published record claiming 48 hours while holding 24.
        # See migration 0013; measured on the live database, it emptied two.
        row = session.scalar(
            select(m.Digest).where(
                m.Digest.kind == kind,
                m.Digest.window_start == built["window_start"],
                m.Digest.window_end == built["window_end"],
                m.Digest.prompt_version == label,
            )
        )
        if row is None:
            row = m.Digest(
                kind=kind,
                window_start=built["window_start"],
                window_end=built["window_end"],
                prompt_version=label,
            )
            session.add(row)
        row.stats = {**built["stats"], "versions": list(versions)}
        row.payload = {"items": built["items"]}
        row.run_id = run_id
        rows.append(row)
    session.flush()
    return rows


def history(session: Session, kind: str | None = None, limit: int = 20) -> list[dict]:
    """Published digests, newest first — the "past reports" the brief asks for.

    Args:
        session: Open session.
        kind: Restrict to one audience, or None for both.
        limit: Maximum rows.

    Returns:
        One dict per digest, payload included.
    """
    stmt = select(m.Digest).order_by(m.Digest.window_end.desc(), m.Digest.kind)
    if kind is not None:
        stmt = stmt.where(m.Digest.kind == kind)
    return [
        {
            "id": d.id,
            "kind": d.kind,
            "windowStart": d.window_start.isoformat(),
            "windowEnd": d.window_end.isoformat(),
            "promptVersion": d.prompt_version,
            "createdAt": d.created_at.isoformat(),
            "runId": d.run_id,
            "stats": d.stats,
            "items": (d.payload or {}).get("items", []),
        }
        for d in session.scalars(stmt.limit(limit))
    ]
