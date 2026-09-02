"""The article-to-holding join: four routes, deterministic, quote-carrying.

Routes (config/categories.yaml documents the first three; provenance is stored
on every row):

* ``mechanism`` — article mechanism tag × company mechanism edge. The only
  route with sign and magnitude on both sides.
* ``category`` — article category tag × holding category membership.
  Membership carries no sign, so direction is the article's.
* ``lab_exposure`` — article's lab × company lab edge, **gated on the article
  scoring above zero**. The edge fires on the publisher, not on what was
  published, so ungated it connects every routine lab post to its counterparties
  (a Brazil office opening reaching Amazon). The gate keeps the case the route
  exists for — a funding round moves TeraWulf without tagging a semiconductor
  mechanism — because such articles still score through their event weight.
  The classifier has no lab-sentiment axis, so direction is always ``mixed``.
* ``named`` — the holding's name, a configured alias, or its ticker appears in
  the article text. A mention carries no polarity: direction ``mixed``.

Sign composition multiplies (+·+=+, +·-=-, -·-=+) and ``mixed`` on either side
propagates ``mixed``. Strength reuses scoring.yaml's own magnitude/confidence
maps; low confidence is a gate (0.0), and a zero-strength connection is not
written — same philosophy as the v4 scoring rule.

Each route's strength is then capped by ``join.route_ceiling`` in scoring.yaml,
because the routes do not carry comparable evidence and the digest sorts on one
column: a bare name match cannot be allowed to outrank a doubly-quoted mechanism
tag. The ceilings are judgement and live in config so they can be argued.

A category tag is a claim about the group ("chip demand rises"); a mechanism
edge is evidence about one company. The two can disagree — OpenAI shipping its
own inference accelerator reads positive for the accelerator category and
negative for NVIDIA specifically, via NVIDIA's own mechanism edge — and when a
holding has both on the same article, showing the group claim next to the
company-specific one it contradicts is confusing rather than informative.
``_drop_contradicted_category_rows`` removes a holding's category row wherever
its own mechanism row on the same article points the opposite way; the
mechanism row, being company-specific, stays. Only strict opposites count —
"mixed" contradicts nothing, and agreement between the two routes is left
alone (docs/decisions.md).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import models as m

ROOT = Path(__file__).parent.parent
SCORING = ROOT / "config" / "scoring.yaml"

# Trailing tokens that carry no identity, stripped (repeatedly) from display
# names before matching: "Micron Technology, Inc." -> "Micron".
LEGAL_SUFFIXES = {
    "inc", "corp", "corporation", "ltd", "limited", "plc", "ag", "se", "sa",
    "nv", "co", "company", "technologies", "technology", "manufacturing",
    "markets", "holdings", "group", "cl", "a", "reg", "shs", "ad", "rs",
    "spons", "registered", "shares", "",
}
MIN_NAME_LEN = 4
MIN_TICKER_LEN = 3
# An alias is hand-curated in config, so it is trusted below MIN_NAME_LEN —
# "AWS" and "SQM" are the point of the field. Three characters is still the
# floor; validate.py enforces it.
MIN_ALIAS_LEN = 3

OPPOSITE_DIRECTION = {"positive": "negative", "negative": "positive"}


def sign_product(a: str, b: str) -> str:
    """Compose an article-tag sign with a holding-edge sign.

    Args:
        a: Article-side sign (positive | negative | mixed).
        b: Holding-side sign.

    Returns:
        The composed direction; ``mixed`` dominates because a guess about an
        ambiguous product would be presented as evidence.
    """
    if "mixed" in (a, b):
        return "mixed"
    return "positive" if a == b else "negative"


def weight(magnitude: str, confidence: str, rules: dict) -> float:
    """One side's expected magnitude in [0, 1], from scoring.yaml's own maps.

    Args:
        magnitude: high | medium | low.
        confidence: high | medium | low — low maps to 0.0, a gate.
        rules: Parsed config/scoring.yaml.

    Returns:
        magnitude × confidence, normalised by the maximum magnitude.
    """
    return (rules["magnitude"][magnitude] * rules["confidence"][confidence]) / rules["max_mechanism"]


def match_name(holding_name: str) -> str | None:
    """Distinctive display name for mention matching.

    Trailing tokens are compared on their letters alone, so the dotted forms a
    registry uses ("N.V.", "S.A.", "Inc.") strip as readily as the bare ones.
    The empty string is a suffix by construction: a trailing "/4" reduces to
    nothing, and halting there used to strand the whole tail behind it.

    Args:
        holding_name: The registered name, e.g. "Micron Technology, Inc.".

    Returns:
        The name with trailing legal tokens stripped, or None when nothing
        distinctive enough remains.
    """
    tokens = holding_name.replace(",", " ").replace("(", " ").replace(")", " ").split()
    while tokens and re.sub(r"[^a-z]", "", tokens[-1].lower()) in LEGAL_SUFFIXES:
        tokens.pop()
    name = " ".join(tokens)
    return name if len(name) >= MIN_NAME_LEN else None


def mentions(text: str, holdings: list) -> list[tuple[str, str]]:
    """Find holdings named in the text.

    Args:
        text: Title plus body of one article.
        holdings: Rows with ``isin``, ``name``, ``aliases``, ``ticker``.

    Returns:
        (isin, via) pairs; ``via`` records what matched, e.g. ``name:Micron``
        or ``alias:AWS``. Names and aliases match case-insensitively on word
        boundaries; tickers match uppercase-exact with a minimum length so "MU"
        or "ON" cannot fire on ordinary prose. At most one hit per holding —
        the first of name, alias, ticker.
    """
    found = []
    for h in holdings:
        candidates = [("name", match_name(h.name))]
        candidates += [("alias", a) for a in (h.aliases or [])
                       if len(a) >= MIN_ALIAS_LEN]
        hit = next(
            ((kind, c) for kind, c in candidates
             if c and re.search(rf"(?<!\w){re.escape(c)}(?!\w)", text, re.IGNORECASE)),
            None,
        )
        if hit:
            found.append((h.isin, f"{hit[0]}:{hit[1]}"))
            continue
        ticker = (h.ticker or "").strip()
        if len(ticker) >= MIN_TICKER_LEN and re.search(rf"(?<!\w){re.escape(ticker)}(?!\w)", text):
            found.append((h.isin, f"ticker:{ticker}"))
    return found


def _drop_contradicted_category_rows(rows: list[m.Connection]) -> list[m.Connection]:
    """Drop a holding's category row where its own mechanism row disagrees.

    Both routes are for the same article, so this is "does this company's
    specific evidence contradict the group claim", never a cross-article
    comparison.

    Args:
        rows: One article's connection rows, across all routes.

    Returns:
        The same rows, minus any category row whose isin also has a
        mechanism row with the strictly opposite direction. A holding with
        mechanism rows on both sides (a genuinely mixed picture) still loses
        the category row — its own mechanism rows are the more specific
        story either way.
    """
    mech_directions: dict[str, set[str]] = {}
    for r in rows:
        if r.route == "mechanism":
            mech_directions.setdefault(r.isin, set()).add(r.direction)
    return [
        r for r in rows
        if not (r.route == "category"
                and OPPOSITE_DIRECTION.get(r.direction) in mech_directions.get(r.isin, ()))
    ]


def connections_for(article, cls_tags: dict, score: float, refs: dict,
                    rules: dict) -> list[m.Connection]:
    """All connections for one article, across the four routes.

    Args:
        article: The Article row (url, lab, title, text).
        cls_tags: ``{"mechanisms": [...], "categories": [...]}`` tag rows for
            the article's classification.
        score: The article's investment score. Gates the lab_exposure route
            only; the other routes carry their own evidence.
        refs: The reference layer: ``holding_mechanisms`` (rows),
            ``holding_categories`` (category_id -> [isin]), ``lab_edges``
            (rows), ``holdings`` (rows), ``routable`` (category ids).
        rules: Parsed config/scoring.yaml.

    Returns:
        Connection rows with strength > 0, unwritten, minus any category row
        contradicted by the same holding's mechanism row
        (``_drop_contradicted_category_rows``).
    """
    out: list[m.Connection] = []
    ceiling = rules["join"]["route_ceiling"]

    edges_by_mech: dict[str, list] = {}
    for e in refs["holding_mechanisms"]:
        edges_by_mech.setdefault(e.mechanism_id, []).append(e)

    for t in cls_tags["mechanisms"]:
        for e in edges_by_mech.get(t.mechanism_id, []):
            strength = (weight(t.magnitude, t.confidence, rules)
                        * weight(e.magnitude, e.confidence, rules)
                        * ceiling["mechanism"])
            if strength == 0:
                continue
            out.append(m.Connection(
                article_id=article.id, isin=e.isin, route="mechanism",
                via=t.mechanism_id, direction=sign_product(t.sign, e.sign),
                strength=round(strength, 4),
                article_sign=t.sign, article_magnitude=t.magnitude,
                article_confidence=t.confidence, article_reason=t.reason,
                article_quote=t.quote,
                holding_sign=e.sign, holding_magnitude=e.magnitude,
                holding_confidence=e.confidence, holding_why=e.why,
                holding_source=e.source))

    for t in cls_tags["categories"]:
        if t.category_id not in refs["routable"]:
            continue  # defensive: crypto never appears in the prompt anyway
        strength = rules["confidence"][t.confidence] * ceiling["category"]
        if strength == 0:
            continue
        for isin in refs["holding_categories"].get(t.category_id, []):
            out.append(m.Connection(
                article_id=article.id, isin=isin, route="category",
                via=t.category_id, direction=t.sign, strength=round(strength, 4),
                article_sign=t.sign, article_confidence=t.confidence,
                article_reason=t.reason, article_quote=t.quote))

    for e in refs["lab_edges"] if score > 0 else []:
        if e.is_dormant or e.lab != article.lab:
            continue
        strength = weight(e.magnitude, e.confidence, rules) * ceiling["lab_exposure"]
        if strength == 0:
            continue
        out.append(m.Connection(
            article_id=article.id, isin=e.isin, route="lab_exposure",
            via=f"{e.lab}:{e.kind}", direction="mixed", strength=round(strength, 4),
            holding_sign=e.sign, holding_magnitude=e.magnitude,
            holding_confidence=e.confidence, holding_why=e.why,
            holding_source=e.source))

    text = f"{article.title}\n{article.text}"
    for isin, via in mentions(text, refs["holdings"]):
        out.append(m.Connection(
            article_id=article.id, isin=isin, route="named", via=via,
            direction="mixed", strength=ceiling["named"]))

    # The classifier may tag the same mechanism twice on one article (distinct
    # quotes). One connection per join key: the strongest evidence wins; the
    # duplicate tag itself is still visible in article_mechanisms.
    best: dict[tuple, m.Connection] = {}
    for c in out:
        key = (c.isin, c.route, c.via)
        if key not in best or c.strength > best[key].strength:
            best[key] = c
    return _drop_contradicted_category_rows(list(best.values()))


def connect(session: Session, prompt_version: str, run_id: int | None = None) -> dict:
    """Rebuild the connections table for every classified article.

    Wholesale delete-and-rebuild: the table is fully derived, so rebuilding is
    the idempotency story and denormalized copies cannot drift.

    Args:
        session: Open session; this function flushes, the caller commits.
        prompt_version: Which classifications to join from.
        run_id: Accepted for CLI uniformity; connections carry no run column.

    Returns:
        Counts per route (the ``category`` count is post-suppression — it
        does not include rows a same-holding mechanism row contradicted)
        plus articles and holdings touched.
    """
    rules = yaml.safe_load(SCORING.read_text())
    refs = {
        "holding_mechanisms": session.scalars(select(m.HoldingMechanism)).all(),
        "lab_edges": session.scalars(select(m.HoldingLabExposure)).all(),
        "holdings": session.scalars(select(m.Holding)).all(),
        "routable": {c.id for c in session.scalars(select(m.RefCategory)) if c.lab_signal_routable},
        "holding_categories": {},
    }
    for hc in session.scalars(select(m.HoldingCategory)):
        refs["holding_categories"].setdefault(hc.category_id, []).append(hc.isin)

    session.execute(delete(m.Connection))
    counts = {"mechanism": 0, "category": 0, "lab_exposure": 0, "named": 0}
    articles_hit, isins_hit = set(), set()

    pairs = session.execute(
        select(m.Article, m.Classification)
        .join(m.Classification, m.Classification.article_id == m.Article.id)
        .where(m.Classification.prompt_version == prompt_version)
    ).all()
    for article, cls in pairs:
        tags = {
            "mechanisms": session.scalars(select(m.ArticleMechanism).where(
                m.ArticleMechanism.classification_id == cls.id)).all(),
            "categories": session.scalars(select(m.ArticleCategory).where(
                m.ArticleCategory.classification_id == cls.id)).all(),
        }
        rows = connections_for(article, tags, cls.score, refs, rules)
        session.add_all(rows)
        for r in rows:
            counts[r.route] += 1
            articles_hit.add(article.id)
            isins_hit.add(r.isin)

    session.flush()
    counts.update({"articles_connected": len(articles_hit), "holdings_reached": len(isins_hit)})
    return counts
