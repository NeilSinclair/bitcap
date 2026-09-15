"""Investment ranking: an item's place, and which of its two scores earned it.

An item carries two numbers on the investment axis, and they measure different
things. The event score (app/scoring.py) is how big this kind of event is, times
its strongest quoted mechanism tag. The holding score is how hard the article
reaches a position in the book: its strongest connection (app/connect.py) × 100,
counted only on routes backed by an article tag (`ranking.holding_routes` in
config/scoring.yaml). A frontier launch can score 100 on the first and 17 on the
second; a partnership can score 40 and 100. Routes in `ranking.hidden_routes`
are removed by `visible` before either surface reads a connection (D82).

Ranked on either alone, one of those is buried. So an item ranks on its higher
score, and a tie there breaks on the other one (`rank_key`). The dashboard
(api/queries.py) and the digest (app/digest.py) both read this module, so they
rank on the same key. The digest can still show a different member of a group
when its admission rule rejects the best-ranked one.

The two scores rest on different evidence, often from different tags, so the
justification follows whichever one set the rank (docs/decisions.md D81). An
event score cites its event type and its strongest tag. A holding score cites
two halves and needs both: what the article said (the connection's article-side
tag and quote) and why that reaches this company (the holding edge's `why` and
source). The quote alone would claim the lab said something about the company.
"""

from __future__ import annotations

from app.connect import weight

EVENT = "event"
HOLDING = "holding"


def band_of(value: float, rules: dict) -> str:
    """Band label for a 0-100 value, on scoring.yaml's investment bands.

    Args:
        value: A score out of 100.
        rules: Parsed config/scoring.yaml.

    Returns:
        The label of the first band whose floor `value` clears.
    """
    return next(b["label"] for b in rules["bands"] if value >= b["min"])


def strongest_tag(tags: list, rules: dict):
    """The mechanism tag that set the event score.

    Args:
        tags: ArticleMechanism rows, or anything with `magnitude`, `confidence`
            and `ordinal`.
        rules: Parsed config/scoring.yaml.

    Returns:
        The tag with the largest magnitude × confidence, lowest ordinal on a tie
        so the pick never depends on row order; None when every tag weighs zero,
        because then the score is zero and no tag earned it.
    """
    best, best_weight = None, 0.0
    for tag in sorted(tags, key=lambda t: t.ordinal):
        w = rules["magnitude"][tag.magnitude] * rules["confidence"][tag.confidence]
        if w > best_weight:
            best, best_weight = tag, w
    return best


def visible(connections, rules: dict) -> list:
    """The connections an investment surface may read at all.

    Routes in `ranking.hidden_routes` are dropped before ranking, listing or
    digest admission, so a hidden link can neither move, show on nor admit an
    item (docs/decisions.md D82). app/connect.py still writes the rows.

    Args:
        connections: Connection rows, for any number of articles.
        rules: Parsed config/scoring.yaml.

    Returns:
        The rows whose route is not hidden, in their original order.
    """
    hidden = set(rules["ranking"].get("hidden_routes") or ())
    return [c for c in connections if c.route not in hidden]


def tied_links(connections: list, routes: list[str]) -> list:
    """Every holding's link at the top holding strength, best first.

    One tag often reaches several holdings equally: inference volume at 1.00 on
    Amazon, Micron and NVIDIA. Naming only one would make the article read as
    that company's news, so all of them are kept.

    Args:
        connections: Connection rows for one article, any route, any order.
        routes: Routes allowed to set the score (`ranking.holding_routes`).

    Returns:
        One connection per holding at the top strength, `mechanism` before
        `category` (sized on both sides), then by ISIN so the order is stable.
        Empty without a qualifying link.
    """
    eligible = sorted(
        (c for c in connections if c.route in routes and c.strength > 0),
        key=lambda c: (-c.strength, c.route != "mechanism", c.isin),
    )
    if not eligible:
        return []
    top = round(eligible[0].strength, 4)
    tied, seen = [], set()
    for c in eligible:
        if round(c.strength, 4) != top:
            break
        if c.isin not in seen:
            seen.add(c.isin)
            tied.append(c)
    return tied


def holding_link(connections: list, routes: list[str]):
    """The connection that sets the holding score: the first of `tied_links`.

    Args:
        connections: Connection rows for one article, any route, any order.
        routes: Routes allowed to set the score (`ranking.holding_routes`).

    Returns:
        The strongest connection on an allowed route, or None.
    """
    links = tied_links(connections, routes)
    return links[0] if links else None


def holding_score(link) -> float:
    """A connection's strength on the 0-100 scale of the event score.

    Args:
        link: The connection from `holding_link`, or None.

    Returns:
        100 × strength, rounded to one decimal; 0.0 without a link.
    """
    return round(100 * link.strength, 1) if link is not None else 0.0


def rank_key(event: float, holding: float) -> tuple[float, float]:
    """The sort key: the higher of the two scores, then the other one.

    Sorted descending, the higher score orders items and the other breaks ties.
    (100, 67), (40, 100), (100, 17), (80, 80) sort as A, B, C, D — the first
    three tie at 100 and split on 67 > 40 > 17.

    Args:
        event: The event score, 0-100.
        holding: The holding score, 0-100.

    Returns:
        ``(max(event, holding), min(event, holding))``.
    """
    return (max(event, holding), min(event, holding))


def lead(event: float, holding: float) -> str:
    """Which score set the rank.

    A tie leads with the event: it is the more general claim, and the holding
    link is shown beside it either way.

    Args:
        event: The event score, 0-100.
        holding: The holding score, 0-100.

    Returns:
        `HOLDING` when the holding score is strictly higher, else `EVENT`.
    """
    return HOLDING if holding > event else EVENT


def _event_basis(cls, tag, rules: dict, mech_labels: dict) -> dict:
    """What the event score rests on: its event type and strongest tag."""
    return {
        "eventType": cls.event_type,
        "eventLabel": (cls.event_type or "").replace("_", " ").capitalize(),
        "eventWeight": rules["event_weight"].get(cls.event_type, 0),
        "maxEventWeight": rules["max_event_weight"],
        "tag": None if tag is None else {
            "id": tag.mechanism_id,
            "label": mech_labels.get(tag.mechanism_id, tag.mechanism_id),
            "sign": tag.sign,
            "magnitude": tag.magnitude,
            "confidence": tag.confidence,
            "reason": tag.reason,
            "quote": tag.quote,
        },
    }


def _holding_basis(link, rules: dict, mech_labels: dict, cat_labels: dict, names: dict) -> dict:
    """What the holding score rests on: the article half and the company half.

    A category link has no company half. Membership carries no company-specific
    evidence, and its article side has confidence but no magnitude.
    """
    mechanism = link.route == "mechanism"
    if mechanism and link.article_magnitude and link.article_confidence:
        article_weight = weight(link.article_magnitude, link.article_confidence, rules)
    elif not mechanism and link.article_confidence:
        article_weight = rules["confidence"][link.article_confidence]
    else:
        article_weight = None
    company_weight = (
        weight(link.holding_magnitude, link.holding_confidence, rules)
        if mechanism and link.holding_magnitude and link.holding_confidence else None
    )
    return {
        "holding": names.get(link.isin, link.isin),
        "isin": link.isin,
        "route": link.route,
        "label": (mech_labels if mechanism else cat_labels).get(link.via, link.via),
        "direction": link.direction,
        "strength": round(link.strength, 2),
        "routeCeiling": rules["join"]["route_ceiling"][link.route],
        "article": {
            "sign": link.article_sign,
            "magnitude": link.article_magnitude,
            "confidence": link.article_confidence,
            "reason": link.article_reason,
            "quote": link.article_quote,
            "weight": None if article_weight is None else round(article_weight, 2),
        },
        "company": None if not mechanism else {
            "sign": link.holding_sign,
            "magnitude": link.holding_magnitude,
            "confidence": link.holding_confidence,
            "why": link.holding_why,
            "source": link.holding_source,
            "weight": None if company_weight is None else round(company_weight, 2),
        },
    }


def rank_fields(cls, tags: list, connections: list, rules: dict, *,
                mech_labels: dict, cat_labels: dict, names: dict) -> dict:
    """Everything an investment surface needs to place an item and say why.

    Args:
        cls: The Classification row (`score`, `event_type`).
        tags: Its ArticleMechanism rows.
        connections: The article's Connection rows, every route.
        rules: Parsed config/scoring.yaml.
        mech_labels: mechanism id -> label.
        cat_labels: category id -> label.
        names: isin -> display name.

    Returns:
        ``holdingScore``, ``rankLead``, ``rankValue``, ``rankBand``,
        ``eventBasis`` and ``holdingBasis`` (None without a qualifying link).
        ``holdingBasis.tiedWith`` holds the basis of every other holding at the
        same top strength. The sort key itself is
        ``rank_key(cls.score, holdingScore)``.
    """
    event = cls.score or 0.0
    links = tied_links(connections, rules["ranking"]["holding_routes"])
    link = links[0] if links else None
    holding = holding_score(link)
    value = max(event, holding)
    return {
        "holdingScore": holding,
        "rankLead": lead(event, holding),
        "rankValue": value,
        "rankBand": band_of(value, rules),
        "eventBasis": _event_basis(cls, strongest_tag(tags, rules), rules, mech_labels),
        "holdingBasis": None if link is None else {
            **_holding_basis(link, rules, mech_labels, cat_labels, names),
            "tiedWith": [_holding_basis(t, rules, mech_labels, cat_labels, names)
                         for t in links[1:]],
        },
    }
