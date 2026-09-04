"""Does the classifier still agree with itself? Measured every run.

`gold_snapshots` has existed since the database was built and never had a
writer. This is it.

**The cache would make this useless, so it is bypassed.** `classify_one` returns
a cached result keyed on the article URL, so a drift check reading through the
cache would report perfect agreement forever — the one failure mode that looks
exactly like success. Drift therefore calls the uncached path, which means it
always costs money.

Two different caches, and only one of them is bypassed. The *result* cache is
the one that would make this meaningless. The provider's *prompt* cache is not —
it discounts the shared system prompt every call re-sends and has no bearing on
whether the model's answer is fresh. So the run fans out over the classifier's
own worker count, after one call has completed serially to create the cache
entry the others read: a cold parallel start would bill every request as a 1.25x
cache write rather than a 0.10x read, costing more than the serial loop it
replaced (docs/decisions.md D42).

**Fixed, and the whole gold set.** The sample is a fixed named list rather than
a draw, so a movement between runs is the classifier changing and never the
sample changing -- a random sample would make every movement ambiguous.

It was six items for its first weeks, on the §6a argument that variance probing
is pure cost with no product output. Running it proved the sample itself was the
variance: six articles carry ten reference mechanism tags, so a single tag
missed or gained moves micro-F1 by ~0.05 and a floor set at 0.80 sits well
inside the noise. The first firing to breach it scored 0.750 -- which is either
real degradation or three tags of jitter, and six items cannot tell you which.
A check that cannot distinguish drift from its own sampling error does not
measure drift, and a false alarm a week is how a system-alert channel gets
muted. All 20 gold articles cost ~$0.75 a run against a raised $75 monthly
ceiling, which is affordable, and the wider tag base is what makes the floor
mean something (docs/decisions.md D35).

**The headline metric is mechanism micro-F1**, not a composite. Under
`config/scoring.yaml` an article with no mechanism tag scores zero by
construction, so mechanism agreement *is* score agreement. Blending several
axes into one number would be the "arbitrary weighted sum dressed up as a score"
the scoring rule already refuses to be.

Falling below the floor is a **system** alert, not a content one. A scorer
quietly becoming less consistent is the silent degradation this whole design
exists to catch — it is the pipeline breaking, not a finding about the world.
"""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m

ROOT = Path(__file__).parent.parent.parent
GOLD = ROOT / "research" / "announcements" / "test" / "articles"

for _leg in ("announcements", "papers"):
    _path = str(ROOT / "research" / _leg)
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Every adjudicated gold article, not a curated subset. The six-item sample this
# replaced was chosen to span the failure modes -- richly-tagged release, the
# other weight-5 event type, a practices-only article, two with no tags at all
# -- and it still does, because those six are in here. What it could not do was
# carry enough reference tags for the floor to separate drift from jitter.
# Still listed explicitly rather than globbed: `load_gold` raises on an id it
# cannot find, and that guard against a silently shrinking sample is worth
# more than the convenience. A new gold article is added here by hand.
DEFAULT_SAMPLE = ("01", "04", "05", "07", "10", "12", "15", "16", "19", "20",
                  "21", "22", "23", "24", "25", "26", "27", "28", "29", "30")


def load_gold(sample: tuple[str, ...] = DEFAULT_SAMPLE) -> list[dict]:
    """Read the sampled gold articles.

    Raises:
        FileNotFoundError: Naming the missing ids. A silently shrinking sample
            would change the metric without changing the number's meaning.
    """
    records, missing = [], []
    for gold_id in sample:
        path = GOLD / f"{gold_id}.json"
        if not path.exists():
            missing.append(gold_id)
        else:
            records.append(json.loads(path.read_text(encoding="utf-8")))
    if missing:
        raise FileNotFoundError(f"gold articles missing: {missing}")
    return records


def measure(
    sample: tuple[str, ...] = DEFAULT_SAMPLE,
    model: str | None = None,
    budget=None,
    on_progress=None,
    workers: int | None = None,
) -> dict:
    """Re-classify the sample and compare it against the adjudicated labels.

    Args:
        sample: Gold article ids.
        on_progress: Called as `(done, total)` after each article completes, so
            a caller can report progress. Fired under the lock, so `done` never
            goes backwards even though the calls finish out of order.
        workers: Concurrent requests. Defaults to `classification.workers` in
            config/pipeline.yaml — the same knob the classifier uses, because it
            is the same provider, the same rate limit and the same shape of
            work.
        model: Model id. Defaults to `classification.model` in
            config/pipeline.yaml — the same model production classifies with.
            Hardcoding one here would let the classifier be changed in config
            while drift silently kept grading the old model, so the number
            would stop describing the thing actually running.
        budget: Optional ceiling. Checked before each call; a drift check that
            cannot afford to run reports `skipped` rather than a false-good
            agreement number.

    Returns:
        Metrics dict, suitable for `gold_snapshots.metrics` verbatim.
    """
    import anthropic
    import score_announcements as sa
    from gold_metrics import axis
    from verbatim import enforce as enforce_quotes

    if model is None or workers is None:
        from app.pipeline.classify import settings as classify_settings

        configured = classify_settings()
        model = model or configured["model"]
        workers = workers or int(configured.get("workers", 12))

    records = load_gold(sample)
    _, _, _, mech_ids, cat_ids, prac_ids = sa.vocabularies()
    dims, cap = sa.practice_dimensions(), sa.dimension_cap()
    client = anthropic.Anthropic()

    runs, errors, spent, skipped = {}, [], 0.0, 0
    lock = threading.Lock()
    done = 0

    def one(record: dict) -> None:
        """Classify a single gold article and fold its result in, thread-safely."""
        nonlocal spent, skipped, done
        if budget is not None and budget.exhausted:
            with lock:
                skipped += 1
                done += 1
            return
        article = {k: v for k, v in record.items()
                   if k not in ("gold", "review", "system")}
        try:
            # The uncached path, deliberately — see the module docstring.
            result, cost = sa.classify(client, model, article)
        except Exception as exc:  # noqa: BLE001 — one bad call is not a drift signal
            with lock:
                errors.append({"id": record["id"], "error": str(exc)})
                done += 1
            return
        result["dropped_tags"] = sa.drop_unknown_tags(
            result, mech_ids, cat_ids, prac_ids, dims, cap
        )
        result["dropped_tags"] += enforce_quotes(result, article["text"])
        with lock:
            runs[record["id"]] = result
            spent += cost["usd"]
            done += 1
            # Cost at the call site, into the same append-only log the
            # classifier writes, so `load_costs` carries it into `raw_costs` and
            # the monthly ceiling can see it. Taking the number and discarding
            # the record left ~$5/month of real spend invisible to
            # `month_to_date`. Inside the lock: `_record_cost` does a
            # read-modify-write of a JSON file, and concurrent writers lose rows.
            _record_cost(sa, cost)
            if budget is not None:
                budget.spend(cost["usd"])
            if on_progress is not None:
                on_progress(done, len(records))

    # Warm the prompt cache before fanning out, exactly as `score_announcements.run`
    # does. `sa.classify` marks the shared system prompt `cache_control:
    # ephemeral`, and Anthropic bills a cache *write* at 1.25x input against a
    # *read* at 0.10x. Firing all 20 at a cold cache makes every one of them a
    # write — the run costs more in parallel than it did serially, which is the
    # opposite of the point. One call first, serially, creates the entry the
    # other 19 then read (docs/decisions.md D42).
    if records:
        one(records[0])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one, r) for r in records[1:]]):
            future.result()

    compared = [r for r in records if r["id"] in runs]
    if not compared:
        return {
            "sample": list(sample), "compared": 0, "skipped": skipped,
            "errors": errors, "cost_usd": round(spent, 6),
            "mechanism_f1": None, "event_type_agreement": None,
        }

    same_event = sum(
        1 for r in compared if r["gold"]["event_type"] == runs[r["id"]].get("event_type")
    )
    axes = {}
    for key in ("mechanisms", "categories", "practices"):
        pairs = [
            ({t["id"] for t in r["gold"].get(key, [])},
             {t["id"] for t in runs[r["id"]].get(key, [])})
            for r in compared
        ]
        axes[key] = axis(pairs, key)

    return {
        "sample": list(sample),
        "compared": len(compared),
        "skipped": skipped,
        "errors": errors,
        "cost_usd": round(spent, 6),
        "model": model,
        # The alert metric. Mechanisms gate every non-zero investment score, so
        # mechanism agreement is score agreement.
        "mechanism_f1": round(axes["mechanisms"]["micro_f1"], 4),
        "event_type_agreement": round(same_event / len(compared), 4),
        "axes": axes,
    }


def _record_cost(sa, cost: dict) -> None:
    """Append one drift call's cost to the shared cost log.

    Written per call rather than summed at the end, matching the discipline the
    rest of the project applies: a run that dies halfway has still spent what it
    spent, and the log must say so.

    `url` and `at` are `raw_costs`'s key, so a record without both cannot be
    loaded and would sit in an append-only file until `load_costs` crashed on
    it. Refusing to write it keeps the log loadable; the spend is still counted
    against the budget by the caller either way.
    """
    if not (cost.get("url") and cost.get("at")):
        return
    log = json.loads(sa.COST.read_text()) if sa.COST.exists() else []
    log.append({**cost, "workflow": "drift"})
    sa.COST.write_text(json.dumps(log, indent=2))


def record(
    session: Session, metrics: dict, prompt_version: str, run_id: int | None = None
) -> m.GoldSnapshot:
    """Persist a measurement as a point on the drift timeline.

    Args:
        session: Open session; the caller commits.
        metrics: A :func:`measure` result, stored verbatim.
        prompt_version: Classifier version the measurement graded.
        run_id: Firing that produced it, if any.

    Returns:
        The persisted snapshot.
    """
    snapshot = m.GoldSnapshot(
        prompt_version=prompt_version, metrics=metrics, run_id=run_id
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def below_floor(metrics: dict, floor: float) -> bool:
    """Whether this measurement should raise a drift alert.

    A measurement that ran no comparisons is **not** a drift signal — it is a
    missing measurement, and reporting it as drift would fire the alert every
    time the budget ran out.
    """
    score = metrics.get("mechanism_f1")
    return score is not None and score < floor


def history(session: Session, prompt_version: str | None = None, limit: int = 30) -> list[dict]:
    """Recent snapshots, oldest first, for the drift chart.

    Args:
        session: Open session.
        prompt_version: Restrict to one classifier version. Comparing across
            versions is comparing two different questions, so the caller has to
            ask for that deliberately.
        limit: How many points.

    Returns:
        ``[{at, prompt_version, mechanism_f1, event_type_agreement, compared,
        cost_usd}]``.
    """
    query = select(m.GoldSnapshot).order_by(m.GoldSnapshot.id.desc()).limit(limit)
    if prompt_version:
        query = query.where(m.GoldSnapshot.prompt_version == prompt_version)
    rows = list(session.scalars(query))[::-1]
    return [
        {
            "at": r.created_at.isoformat() if r.created_at else None,
            "prompt_version": r.prompt_version,
            "run_id": r.run_id,
            "mechanism_f1": (r.metrics or {}).get("mechanism_f1"),
            "event_type_agreement": (r.metrics or {}).get("event_type_agreement"),
            "compared": (r.metrics or {}).get("compared"),
            "cost_usd": (r.metrics or {}).get("cost_usd"),
        }
        for r in rows
    ]
