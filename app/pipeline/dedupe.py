"""Collapse near-duplicate articles into one row per event.

`docs/decisions.md` D48 rejected cross-article deduplication and deferred the
embedding approach; this module is that approach, and reverses it. The case
that forced it: GPT-6 Astra reached the feed as seven rows over five days — a
launch post, a forum restatement, an API docs page, a safety overview, a
precursor and two customer stories — each independently scored, each its own
line.

Three gates decide a merge, in order, and only the last one costs money:

1. **Subject** — which thing. Deterministic, free: two items share a normalised
   model identifier (`research/corpus/first_mention.py`) and a lab, inside
   `window_days`.
2. **Event type** — what kind of happening. Deterministic, free, and the
   *separator* rather than a joiner: `classifications.event_type` must match.
   This is what keeps "Safety overview: GPT-6 Astra" — which reports a crossed
   Preparedness threshold nothing else in the cluster mentions — out of the
   release card it would otherwise vanish into.
3. **Redundancy** — does it add anything. Cosine decides the clear cases; only
   the band between the two thresholds reaches an LLM.

**Mechanism overlap is not a gate.** It was tested and rejected: over the 267
non-release articles, 23 same-lab/same-event pairs clear Jaccard 0.5 and most
are false — "TCS brings Claude to regulated industries" against "DXC integrates
Claude into systems" scores a perfect 1.0 and they are two different
partnerships. The vocabulary encodes what *kind* of event an item is, not
*which* event. It survives as an input to gate 3, never as a gate.

The anchor of a group is its highest-scoring member, earliest on a tie — not
the most recent. On Astra, "most recent" picks the API documentation page over
the launch announcement.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from pathlib import Path

import sqlalchemy as sa
import yaml
from sqlalchemy.orm import Session

from app import models as m

ROOT = Path(__file__).parent.parent.parent
CONFIG = ROOT / "config" / "dedupe.yaml"

# `research/announcements` modules import each other by bare name, so the
# directory has to be importable rather than the package path. Same approach as
# app/pipeline/drift.py, for the same reason.
for _leg in ("announcements", "corpus"):
    _path = str(ROOT / "research" / _leg)
    if _path not in sys.path:
        sys.path.insert(0, _path)


def settings(path: Path = CONFIG) -> dict:
    """Read the dedupe configuration.

    Args:
        path: Config file.

    Returns:
        Parsed config.
    """
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def embedded_text(title: str, summary: str) -> str:
    """Build the text that represents an article to the similarity check.

    Title plus the classifier's summary, never the article body. Bodies carry
    site chrome — two different Astra articles both begin "OpenAI Research
    Products Business Developers Company Foundation Log in Try ChatGPT" — which
    lifts the cosine between any two pages from one lab regardless of what they
    actually say. The summary is already the model's distilled statement of the
    article's claim, which is the thing a duplicate check needs to compare, and
    it is roughly a tenth of the tokens.

    Args:
        title: Article title.
        summary: Classifier summary. May be empty if an article is unclassified.

    Returns:
        The text to embed.
    """
    return f"{title.strip()}\n\n{summary.strip()}".strip()


def text_hash(text: str) -> str:
    """Hash the embedded text, so a re-run only re-embeds what changed.

    Deliberately over the embedded text rather than the article payload: a
    re-classification that rewrites the summary must re-embed, and a run that
    changes neither title nor summary must not.

    Args:
        text: The text that would be embedded.

    Returns:
        Hex sha256.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack(vector: list[float]) -> str:
    """Encode a vector for storage.

    Base64 of little-endian float32: ~6 KB per 1,536-dimension row against
    ~30 KB as a JSON array, and one `frombuffer` rather than a parse on the way
    back in.

    Args:
        vector: Embedding as floats.

    Returns:
        Base64 string.
    """
    import numpy as np

    return base64.b64encode(np.asarray(vector, dtype="<f4").tobytes()).decode("ascii")


def unpack(blob: str):
    """Decode a stored vector.

    Args:
        blob: Base64 string written by :func:`pack`.

    Returns:
        1-D numpy array of float32.
    """
    import numpy as np

    return np.frombuffer(base64.b64decode(blob), dtype="<f4")


def pending(session: Session, prompt_version: str) -> list[tuple[str, str]]:
    """Find the articles whose embedding is missing or stale.

    An article is pending if it has no cached vector, or if the vector it has
    was built from different text. That is what makes a second run of an
    unchanged corpus spend nothing.

    Ordered by url so a budget cut lands in a reproducible place, matching
    `classify.pending_urls`.

    Args:
        session: Open session.
        prompt_version: Classification version whose summaries to embed.

    Returns:
        List of (url, text) pairs needing an embedding call.
    """
    rows = session.execute(
        sa.select(m.Article.url, m.Article.title, m.Classification.summary)
        .join(m.Classification, m.Classification.article_id == m.Article.id)
        .where(m.Classification.prompt_version == prompt_version)
        .order_by(m.Article.url)
    ).all()

    cached = {
        url: digest
        for url, digest in session.execute(
            sa.select(m.RawArticleEmbedding.url, m.RawArticleEmbedding.content_hash)
        ).all()
    }

    out = []
    for url, title, summary in rows:
        text = embedded_text(title or "", summary or "")
        if not text:
            continue
        if cached.get(url) != text_hash(text):
            out.append((url, text))
    return out


def embed(session: Session, prompt_version: str, budget=None, path: Path = CONFIG) -> dict:
    """Embed every article whose cached vector is missing or stale.

    Batched: 647 articles as 647 requests is minutes of round trips for a few
    cents of tokens. Each batch is one billable call, so each batch is one
    `begin_call`/`end_call` pair and one cost record.

    Cost is recorded at the call site and appended to the shared cost log
    immediately rather than at the end, so a crash partway through does not
    lose the spend already incurred — the same reason `run_batch` writes each
    item's record as it goes.

    Args:
        session: Open session; the caller owns the commit.
        prompt_version: Classification version whose summaries to embed.
        budget: Optional :class:`app.pipeline.budget.Budget`. When it refuses a
            call the phase stops early and reports what it managed, rather than
            raising — a partial embedding degrades gate 1b's recall, it does not
            break the collapse.
        path: Config file.

    Returns:
        Stats: embedded, cached, batches, usd, stopped_on_budget.
    """
    from research.announcements import providers

    config = settings(path)["embedding"]
    model, size = config["model"], int(config["batch_size"])

    work = pending(session, prompt_version)
    total = session.scalar(sa.select(sa.func.count()).select_from(m.RawArticleEmbedding)) or 0
    stats = {
        "embedded": 0, "cached": total, "batches": 0, "usd": 0.0,
        "stopped_on_budget": False,
    }
    if not work:
        return stats

    providers.load_env()
    for start in range(0, len(work), size):
        batch = work[start:start + size]
        if budget is not None and not budget.begin_call():
            stats["stopped_on_budget"] = True
            break
        try:
            vectors, cost = providers.embed_openai(
                model, [text for _, text in batch], f"dedupe:batch:{start // size}"
            )
        finally:
            # Releases the in-flight slot without booking spend; `spend` below
            # books it. Split this way because `spend` also increments the call
            # count, and `expected_per_call` is what the guard extrapolates
            # from — a batch of embeddings costs a rounding error next to a
            # classification, and the guard should learn that rather than keep
            # extrapolating from the seed.
            if budget is not None:
                budget.end_call(0.0)

        if budget is not None:
            budget.spend(cost["usd"])
        _record_cost(cost)
        stats["usd"] += cost["usd"]
        stats["batches"] += 1

        for (url, text), vector in zip(batch, vectors):
            _upsert(session, url, text, model, vector)
            stats["embedded"] += 1
        session.commit()

    return stats


def _record_cost(cost: dict) -> None:
    """Append one cost record to the shared log, immediately.

    Written per batch rather than at the end of the phase: a crash partway
    through must not lose the spend already incurred, which is the same reason
    `run_batch` bills each item as it goes.

    A record missing `url` or `at` is dropped rather than written — those two
    are `raw_costs`'s key, so an unloadable record would sit in an append-only
    file until `load_costs` crashed on it. The spend is still counted against
    the budget by the caller either way.

    Args:
        cost: Record from `providers._cost`.
    """
    import score_announcements as sa_module

    if not (cost.get("url") and cost.get("at")):
        return
    log = json.loads(sa_module.COST.read_text()) if sa_module.COST.exists() else []
    log.append({**cost, "workflow": "dedupe"})
    sa_module.COST.write_text(json.dumps(log, indent=2))


def _upsert(session: Session, url: str, text: str, model: str, vector: list[float]) -> None:
    """Write or replace one article's cached vector.

    Args:
        session: Open session.
        url: Article URL, the cache key.
        text: The text that was embedded.
        model: Embedding model id.
        vector: The returned embedding.
    """
    row = session.scalar(
        sa.select(m.RawArticleEmbedding).where(m.RawArticleEmbedding.url == url)
    )
    if row is None:
        row = m.RawArticleEmbedding(url=url)
        session.add(row)
    row.content_hash = text_hash(text)
    row.model = model
    row.dim = len(vector)
    row.vector = pack(vector)


def subjects(title: str) -> set[str]:
    """Extract the normalised model identifiers an article is *about* — gate 1.

    Reuses the first-mention machinery rather than reimplementing name folding:
    `normalise` already collapses `GPT-6-Astra`, `gpt 6 astra` and `GPT_6_ASTRA`
    to one key, and rewrites digit-hyphen-digit so Anthropic's "Opus 4.8" and
    `claude-opus-4-8` — both of which appear in one document — are one name.

    **Title only, deliberately.** Reading the body was tried and over-generates:
    the GPT-6 Astra launch post is 24,000 characters with a comparison table,
    and yields `gemini-3.8`, `opus-5`, `gpt-5.6` and `gpt-5.6-sol` alongside its
    actual subject. Those are references, not what the article is about, and
    pairing on them would join the Astra launch to the GPT-5.6 launch — two
    different events from one lab inside the window, which is precisely the
    false merge this gate must not make. A title names its subject.

    Args:
        title: Article title.

    Returns:
        Set of normalised identifiers, empty if none are recognised. Empty is
        common and expected — `config/entities.yaml` records that the pattern
        "does NOT catch a genuinely novel product name carrying no version
        number", and "Path to Astra: critical capabilities and frontier
        safeguards" is a live instance: it precedes the name it is about.
        Gate 1b exists to cover exactly that.
    """
    import first_mention as fm

    cfg = fm.load_config()
    pattern = fm.model_pattern(cfg["model_families"], cfg["max_version_parts"])
    deny = {fm.normalise(d) for d in cfg.get("deny") or []}
    found = set(fm.identifiers(title, pattern, deny))
    return found | {stem(i) for i in found}


def stem(identifier: str) -> str:
    """Reduce an identifier to its family and version, dropping variant suffixes.

    Without this, one model produces two non-intersecting keys depending on how
    a headline spells it. The identifier pattern only absorbs a suffix across a
    hyphen or underscore, so "GPT-6 Astra" extracts as `gpt-6` while
    "GPT-6-Astra" extracts as `gpt-6-astra`. Those are the launch post and the
    forum restatement of it — the clearest true duplicate in the corpus — and
    on the raw identifiers they do not match.

    Emitting the stem alongside the full identifier makes them intersect on
    `gpt-6` without discarding the more specific key.

    Joining sibling variants of one version — a hypothetical `gpt-6-astra` and
    `gpt-6-luna` — is intended, not a side effect: shipped together they are one
    launch. Gates 2 and 3 still have to agree before anything merges.

    Args:
        identifier: A normalised identifier from :func:`subjects`.

    Returns:
        Everything up to and including the first part containing a digit.
    """
    parts = identifier.split("-")
    for index, part in enumerate(parts):
        if any(character.isdigit() for character in part):
            return "-".join(parts[: index + 1])
    return identifier


def candidate_pairs(rows: list[dict], window_days: int) -> list[tuple[int, int]]:
    """Pair every two items from one lab published within the window.

    This is the space gate 1b searches, and the only reason the collapse is
    cheap: 267 non-release articles make 35,511 unordered pairs in principle
    and 4,412 once lab and window are applied. Cross-lab pairs are not
    duplicates of each other by construction — two labs announcing the same
    week are two events — and the window is what stops an article pairing with
    a year-old post that happens to share vocabulary.

    Args:
        rows: Items as dicts with `lab` and `published_on`.
        window_days: Maximum days apart.

    Returns:
        Index pairs (i, j) with i < j.
    """
    order = sorted(range(len(rows)), key=lambda i: (rows[i]["lab"], rows[i]["published_on"]))
    pairs = []
    for a in range(len(order)):
        i = order[a]
        for b in range(a + 1, len(order)):
            j = order[b]
            if rows[j]["lab"] != rows[i]["lab"]:
                break
            if (rows[j]["published_on"] - rows[i]["published_on"]).days > window_days:
                break
            pairs.append((i, j) if i < j else (j, i))
    return pairs


def normalised_title(title: str) -> str:
    """Reduce a title to a comparison key for the exact pass.

    Args:
        title: Article title.

    Returns:
        Lowercased, punctuation collapsed to single spaces.
    """
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def exact_groups(rows: list[dict]) -> dict[int, str]:
    """Gate 0 — one article that reached us at two URLs.

    Same lab, same day, same title once punctuation is stripped. Free, and it
    has to run before the event-type gate rather than after: the classifier
    assigns *different* event types to two copies of one text, so the gate
    below would refuse to merge them. "Expanding Daybreak as the Cyber Defense
    Window Narrows" arrives on openai.com and community.openai.com the same day
    and is classified `incremental_model_release` and `product_launch`;
    "Introducing Intelligence Age" arrives at two openai.com URLs and is
    classified `other` and `safety_policy`.

    Both were labelled `same` by a blind labeller, which is the evidence that
    the disagreement is classifier noise on identical input rather than a real
    difference between the articles.

    Args:
        rows: Articles with `id`, `lab`, `published_on` and `title`.

    Returns:
        Article id to exact-group key, for members of a group larger than one.
    """
    buckets: dict[tuple, list[int]] = {}
    for row in rows:
        key = (row["lab"], row["published_on"], normalised_title(row["title"]))
        buckets.setdefault(key, []).append(row["id"])
    return {
        article_id: f"exact:{lab}:{day}:{title[:60]}"
        for (lab, day, title), members in buckets.items() if len(members) > 1
        for article_id in members
    }


def adjudicate(left: dict, right: dict, config: dict, budget=None) -> dict | None:
    """Ask the model whether two articles are the same event — gate 3.

    Reached only inside the threshold band, which is roughly 12 of 4,412
    candidate pairs on the current corpus. Everything outside it was already
    decided for free.

    Args:
        left: One article with `lab`, `published_on`, `event_type`, `title`,
            `summary`.
        right: The other.
        config: Parsed `config/dedupe.yaml`.
        budget: Optional budget; a refusal returns None rather than raising, so
            an exhausted budget leaves the pair unmerged instead of failing the
            run.

    Returns:
        `{"same": bool, "reason": str, "more_complete": "a"|"b"}`, or None if
        the call was refused, failed, or came back malformed. None means "not
        adjudicated", which the caller treats as not merged — the conservative
        direction, since a false merge deletes a claim.
    """
    from research.announcements import providers

    adjudication = config["adjudication"]
    version = adjudication["prompt_version"]
    template = (ROOT / "prompts" / "duplicate_adjudication" / f"{version}.md").read_text(
        encoding="utf-8"
    )

    import score_announcements as sa_module

    prompt = sa_module.strip_comments(template)
    for key, value in {
        "{lab}": left["lab"],
        "{a_date}": str(left["published_on"]), "{a_event_type}": left["event_type"],
        "{a_title}": left["title"], "{a_summary}": left["summary"],
        "{b_date}": str(right["published_on"]), "{b_event_type}": right["event_type"],
        "{b_title}": right["title"], "{b_summary}": right["summary"],
    }.items():
        prompt = prompt.replace(key, str(value))

    if budget is not None and not budget.begin_call():
        return None
    try:
        result, cost = providers.classify(
            adjudication["provider"], adjudication["model"],
            prompt, "Decide the pair above.", ADJUDICATION_SCHEMA,
            f"dedupe:{left['id']}-{right['id']}",
        )
    except Exception:
        return None
    finally:
        if budget is not None:
            budget.end_call(0.0)

    if budget is not None:
        budget.spend(cost["usd"])
    _record_cost(cost)

    if not isinstance(result, dict) or "same" not in result:
        return None
    return result


ADJUDICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "same": {"type": "boolean"},
        "reason": {"type": "string"},
        "more_complete": {"type": "string", "enum": ["a", "b"]},
    },
    "required": ["same", "reason", "more_complete"],
    "additionalProperties": False,
}


def release_trains(rows: list[dict], window_hours: int) -> list[list[dict]]:
    """Group a repo's consecutive releases into one event each.

    GitHub releases are 380 of 647 articles, and they never need a similarity
    check: a release train is identified by its repo. `claude-code` shipped
    v2.1.257 through v2.1.260 across three days and each took its own line in
    the feed; `deepseek-harness` shipped four alphas in five.

    Chained rather than bucketed. A fixed calendar bucket would split a train
    that happens to straddle a boundary and would leave `deepseek-harness`'
    five-day run as two cards for one piece of work. Chaining starts a new
    group only where the gap between consecutive releases exceeds the window,
    which is the same question a reader is asking: is this still the same burst
    of activity.

    Dates are day-granular (`articles.published_on` is a Date), so the window is
    applied in whole days. At the 48-hour default that is the intended
    behaviour; a sub-day window is not expressible and would need the timestamp
    from the raw payload.

    Args:
        rows: Release articles as dicts with `repo`, `published_on` and `id`.
        window_hours: Maximum gap between consecutive releases in one train.

    Returns:
        Groups, each a list of the input dicts, ordered by date.
    """
    window_days = max(1, window_hours // 24)
    groups: list[list[dict]] = []
    ordered = sorted(rows, key=lambda r: (r["repo"], r["published_on"], r["id"]))
    for row in ordered:
        current = groups[-1] if groups else None
        if (
            current
            and current[-1]["repo"] == row["repo"]
            and (row["published_on"] - current[-1]["published_on"]).days <= window_days
        ):
            current.append(row)
        else:
            groups.append([row])
    return groups


def anchor_of(members: list[dict], score_key: str, prefer: str = "earliest") -> dict:
    """Pick the member that represents a group: highest score, then by date.

    Score first, always. What the tie-break should be differs by path, and the
    difference is not cosmetic — within a release train every member usually
    scores identically, so the tie-break decides every time.

    `earliest` is right for an article cluster. Being early is the product's
    claim, and on the GPT-6 Astra cluster the launch post and the API
    documentation page both score 100 two days apart: anchoring on the later one
    would put a reference page at the top of the feed and fold the launch
    announcement underneath it.

    `latest` is right for a release train. A reader asking about `claude-code`
    wants the version it is on now; anchoring `earliest` labelled a four-release
    train with v2.1.257 while v2.1.260 sat folded inside it, which states the
    opposite of what the group means.

    Args:
        members: Group members with `published_on`, `id` and the score field.
        score_key: Which score to rank on — `score` for the investment axis,
            `ai_score` for the AI axis.
        prefer: `earliest` or `latest`, applied only when scores tie.

    Returns:
        The anchor member.

    Raises:
        ValueError: On an unknown `prefer`, rather than silently defaulting and
            anchoring a whole surface the wrong way round.
    """
    if prefer not in ("earliest", "latest"):
        raise ValueError(f"prefer must be 'earliest' or 'latest', got {prefer!r}")
    direction = 1 if prefer == "latest" else -1
    return max(
        members,
        key=lambda r: (
            r.get(score_key) or 0.0,
            direction * r["published_on"].toordinal(),
            direction * r["id"],
        ),
    )


def matrix(session: Session, urls: list[str]):
    """Load cached vectors for `urls` as an L2-normalised matrix.

    Normalising here means the cosine of every pair is a single matmul rather
    than a divide per pair.

    Args:
        session: Open session.
        urls: Article URLs, in the order the rows should appear.

    Returns:
        Tuple of (urls that had a vector, matrix with one row each).

    Raises:
        ValueError: If the cached vectors do not all share a width, which means
            the embedding model changed under a partially populated cache and
            comparing them would be meaningless.
    """
    import numpy as np

    cached = {
        url: blob
        for url, blob in session.execute(
            sa.select(m.RawArticleEmbedding.url, m.RawArticleEmbedding.vector)
            .where(m.RawArticleEmbedding.url.in_(urls))
        ).all()
    }
    present = [u for u in urls if u in cached]
    if not present:
        return [], np.zeros((0, 0), dtype="float32")

    rows = [unpack(cached[u]) for u in present]
    widths = {r.shape[0] for r in rows}
    if len(widths) != 1:
        raise ValueError(
            f"cached embeddings have mixed widths {sorted(widths)}; the model "
            "changed under a populated cache — clear raw_article_embeddings"
        )

    block = np.vstack(rows).astype("float32")
    norms = np.linalg.norm(block, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return present, block / norms


class _Union:
    """Disjoint sets, so merges from different gates compose.

    The paths overlap: the exact pass may join A and B while the gated pass
    joins B and C, and the answer is one group of three rather than two groups
    sharing a member. Doing this with dictionaries of lists gets the transitive
    case wrong in a way that only appears on rare three-way clusters, which is
    the kind of bug that survives review.
    """

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, item: int) -> int:
        """Return the representative of `item`'s set, adding it if unseen."""
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        """Merge the sets containing `left` and `right`."""
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


# Which evidence a group is labelled with when several gates contributed.
# Ordered by how much it is worth trusting: an exact title match is a fact, an
# adjudicated pair is a judgement, a bare cosine is an inference.
METHOD_RANK = ["exact", "release_train", "llm", "embedding", "singleton"]


def assign(session: Session, prompt_version: str, run_id: int | None = None,
           budget=None, path: Path = CONFIG) -> dict:
    """Group every article, and write the result to `article_groups`.

    Runs three paths and unions their verdicts:

    1. the exact pass, which must come first because the event-type gate would
       otherwise refuse two copies of one text that were classified differently;
    2. release trains, deterministic per repo;
    3. the gated pass — same event type, then the cosine band, with the
       adjudicator asked only inside it.

    Rebuilt wholesale each run rather than updated. The table is a pure function
    of the articles, their classifications and the config, so a re-run produces
    the same assignment and a changed threshold takes effect without a
    migration.

    Args:
        session: Open session; this function commits.
        prompt_version: Classification version to group.
        run_id: Run to attribute the rows to.
        budget: Optional budget for the adjudication calls.
        path: Config file.

    Returns:
        Stats: articles, groups, collapsed, adjudicated, by_method.
    """
    config = settings(path)
    rows = _rows(session, prompt_version)
    union = _Union()
    reasons: dict[tuple[int, int], tuple[str, str]] = {}

    _apply_exact(rows, union, reasons)
    _apply_release_trains(rows, union, reasons, config)
    adjudicated = _apply_gated(session, rows, union, reasons, config, budget)

    session.execute(sa.delete(m.ArticleGroup))
    members: dict[int, list[dict]] = {}
    for row in rows:
        members.setdefault(union.find(row["id"]), []).append(row)

    stats = {"articles": len(rows), "groups": len(members), "collapsed": 0,
             "adjudicated": adjudicated, "by_method": {}}
    for root, group in members.items():
        prefer = "latest" if all(r["repo"] for r in group) else "earliest"
        anchor = anchor_of(group, "significance", prefer=prefer)
        method, reason = _evidence(group, reasons)
        stats["by_method"][method] = stats["by_method"].get(method, 0) + 1
        stats["collapsed"] += len(group) - 1
        for row in group:
            session.add(m.ArticleGroup(
                article_id=row["id"], group_id=f"g{root}", group_size=len(group),
                is_anchor=row["id"] == anchor["id"], method=method,
                reason=reason, run_id=run_id,
            ))
    session.commit()
    return stats


def _rows(session: Session, prompt_version: str) -> list[dict]:
    """Read the articles and the fields every gate needs.

    `significance` is the greater of the two axis scores. A group is shared by
    both audiences and can only have one anchor, and both scores run 0-100 so
    they compare directly. Taking the max means the member that matters most to
    *either* reader represents the group, and it makes release trains anchor
    correctly without a special case — every release scores zero on the
    investment axis (measured: none of the 380 reach medium or high there), so
    the max is its AI score.

    Args:
        session: Open session.
        prompt_version: Classification version to read.

    Returns:
        One dict per article.
    """
    query = (
        sa.select(
            m.Article.id, m.Article.url, m.Article.title, m.Article.lab,
            m.Article.published_on, m.Article.text_source,
            m.Classification.event_type, m.Classification.summary,
            m.Classification.score, m.Classification.ai_score,
            m.RawArticle.payload,
        )
        .join(m.Classification, m.Classification.article_id == m.Article.id)
        .join(m.RawArticle, m.RawArticle.id == m.Article.raw_article_id)
        .where(m.Classification.prompt_version == prompt_version)
        .order_by(m.Article.id)
    )
    rows = []
    for record in session.execute(query):
        payload = record.payload or {}
        repo = None
        if record.text_source == "github_release" and payload.get("repo"):
            repo = f"{payload.get('org')}/{payload['repo']}"
        rows.append({
            "id": record.id, "url": record.url, "title": record.title or "",
            "lab": record.lab, "published_on": record.published_on,
            "event_type": record.event_type or "", "summary": record.summary or "",
            "score": record.score or 0.0, "ai_score": record.ai_score or 0.0,
            "significance": max(record.score or 0.0, record.ai_score or 0.0),
            "repo": repo,
        })
    return rows


def _apply_exact(rows: list[dict], union: _Union, reasons: dict) -> None:
    """Union articles that are one article arriving at two URLs."""
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        if row["repo"]:
            continue
        key = f"{row['lab']}|{row['published_on']}|{normalised_title(row['title'])}"
        buckets.setdefault(key, []).append(row)
    for group in buckets.values():
        if len(group) < 2:
            continue
        for other in group[1:]:
            union.union(group[0]["id"], other["id"])
            reasons[_key(group[0]["id"], other["id"])] = (
                "exact",
                f"one article at {len(group)} URLs — same lab, date and title: "
                f"“{group[0]['title'][:70]}”",
            )


def _apply_release_trains(rows: list[dict], union: _Union, reasons: dict,
                          config: dict) -> None:
    """Union consecutive releases from one repo."""
    releases = [r for r in rows if r["repo"]]
    window = int(config["releases"]["window_hours"])
    for train in release_trains(releases, window):
        if len(train) < 2:
            continue
        for other in train[1:]:
            union.union(train[0]["id"], other["id"])
            reasons[_key(train[0]["id"], other["id"])] = (
                "release_train",
                f"{len(train)} releases of {train[0]['repo']} between "
                f"{train[0]['published_on']} and {train[-1]['published_on']}",
            )


def _apply_gated(session: Session, rows: list[dict], union: _Union, reasons: dict,
                 config: dict, budget) -> int:
    """Run the event-type gate and the cosine band over the non-release articles.

    Gate 1's identifier match is computed and carried into the reason, but it is
    not a separate route into a merge. On this corpus it would not add one: no
    labelled duplicate sits below `cosine_low`, so a subject-only route would
    need a threshold the labelled set gives no evidence for. Recorded here
    rather than shipped as a gate that never fires.

    Args:
        session: Open session.
        rows: All articles.
        union: Accumulating groups.
        reasons: Accumulating evidence.
        config: Parsed config.
        budget: Optional budget.

    Returns:
        How many pairs reached the adjudicator.
    """
    articles = [r for r in rows if not r["repo"]]
    if not articles:
        return 0

    high = float(config["thresholds"]["cosine_high"])
    low = float(config["thresholds"]["cosine_low"])
    urls, block = matrix(session, [r["url"] for r in articles])
    position = {url: index for index, url in enumerate(urls)}
    subject = {r["id"]: subjects(r["title"]) for r in articles}

    adjudicated = 0
    for left, right in candidate_pairs(articles, int(config["window_days"])):
        a, b = articles[left], articles[right]

        # Gate 2, a hard separator. "Safety overview: GPT-6 Astra" reports a
        # crossed Preparedness threshold the launch post never mentions;
        # merging them would delete that claim from the product.
        if a["event_type"] != b["event_type"]:
            continue
        if a["url"] not in position or b["url"] not in position:
            continue

        cosine = float(block[position[a["url"]]] @ block[position[b["url"]]])
        shared = sorted(subject[a["id"]] & subject[b["id"]])
        names = f" on {', '.join(shared)}" if shared else ""

        if cosine >= high:
            union.union(a["id"], b["id"])
            reasons[_key(a["id"], b["id"])] = (
                "embedding",
                f"same {a['event_type']}{names}, similarity {cosine:.2f} "
                f"at or above {high}",
            )
        elif cosine >= low:
            adjudicated += 1
            verdict = adjudicate(a, b, config, budget)
            if verdict and verdict.get("same"):
                union.union(a["id"], b["id"])
                reasons[_key(a["id"], b["id"])] = (
                    "llm", f"{verdict['reason'][:180]} (similarity {cosine:.2f})"
                )
    return adjudicated


def _key(left: int, right: int) -> tuple[int, int]:
    """Order a pair so a reason is findable whichever way round it was made."""
    return (left, right) if left < right else (right, left)


def _evidence(group: list[dict], reasons: dict) -> tuple[str, str]:
    """Pick the method and reason a group is labelled with.

    A group built by more than one gate is described by its most trustworthy
    one: an exact title match is a fact, an adjudicated pair is a judgement, a
    bare cosine is an inference.

    Args:
        group: Members.
        reasons: Recorded (method, reason) by ordered id pair.

    Returns:
        Tuple of (method, reason).
    """
    if len(group) == 1:
        return "singleton", "no near-duplicate found in the window"
    ids = [row["id"] for row in group]
    found = [
        reasons[_key(a, b)]
        for index, a in enumerate(ids) for b in ids[index + 1:]
        if _key(a, b) in reasons
    ]
    if not found:  # pragma: no cover - a group always has at least one link
        return "singleton", "grouped transitively"
    return min(found, key=lambda pair: METHOD_RANK.index(pair[0]))
