"""Emit an unlabelled paper sample for gold tagging.

Mirrors `research/announcements/build_gold_set.py`, including its two rules:
the system's own classification never appears in a file, and the sample is
stratified rather than random.

STRATIFIED BY DOCUMENT TYPE, NOT BY LAB. That is the difference from the
announcements sampler, and it is the finding this whole leg rests on: a paper's
value tracks what kind of document it is, not which lab wrote it. Read across
the corpus the types are

  technical_report   a model launch written up as a paper. The strongest
                     investment evidence in the register -- DeepSeek-V4's
                     abstract states inference FLOPs and KV cache directly --
                     and the class the `p1` prompt exists to classify correctly.
  system_card        a model card or system card. Investment-relevant but
                     largely duplicative: the announcements leg usually has the
                     launch already.
  interpretability   alignment and interpretability work. High for the AI team,
                     near zero for an investor.
  safety_social      safety position papers, red-teaming methodology,
                     social-science studies. The largest group in the corpus and
                     the noise the filter has to remove.
  component          a technique or result with no model attached.

Deliberately over-samples `safety_social`. "Correctly scored zero" is the case
most worth pinning, because it is the one that fails silently: a scorer that
starts finding transmission in a study of how people perceive AI consciousness
buries the technical reports, and nothing about that shows up as an error.

LABELLING PROVENANCE. `gold.labelled_by` is required and is written by whoever
fills the file in. The announcements gold set is human-adjudicated; if this one
is labelled by a model it is a cross-model proxy and must be named as one
wherever its numbers are reported. The brief permits a defensible proxy where
honest ground truth is out of reach. It does not permit calling one gold.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
OUT = Path(__file__).parent / "test" / "papers"

SEED = 20260905  # fixed so the sample is reproducible

# Quotas per document type. Ten items, weighted to where the failures are.
QUOTA = {
    "technical_report": 2,
    "system_card": 1,
    "interpretability": 2,
    "safety_social": 3,
    "component": 2,
}

# Which type a paper is, decided from the lab and the title rather than from the
# system's own event_type -- that would let the sample inherit the answer it is
# meant to check.
_REPORT = ("technical report", "-v2", "-v3", "-v4", "deepseek llm", "deepseek-coder")
_CARD = ("system card", "model card")


def document_type(lab: str, title: str) -> str:
    """Classify a paper into one of the five sampling strata.

    Args:
        lab: Lab id.
        title: Paper title.

    Returns:
        One of the keys of :data:`QUOTA`.
    """
    low = title.lower()
    if any(k in low for k in _CARD):
        return "system_card"
    if lab == "deepseek" or any(k in low for k in _REPORT):
        return "technical_report"
    if lab == "anthropic":
        return "interpretability"
    if lab == "google-deepmind":
        return "safety_social"
    return "component"


def sample(records: list[dict], quota: dict[str, int] | None = None) -> list[dict]:
    """Draw a stratified sample, shuffled so filename order hints at nothing.

    Args:
        records: Article-shaped paper records (`lab`, `title`, `text`, ...).
        quota: Items wanted per document type; defaults to :data:`QUOTA`.

    Returns:
        The drawn records, each with a `document_type` key added.
    """
    quota = quota or QUOTA
    buckets: dict[str, list[dict]] = {}
    for record in records:
        kind = document_type(record["lab"], record["title"])
        buckets.setdefault(kind, []).append({**record, "document_type": kind})

    rng = random.Random(SEED)
    drawn: list[dict] = []
    for kind, wanted in quota.items():
        pool = sorted(buckets.get(kind, []), key=lambda r: r["url"])
        rng.shuffle(pool)
        drawn.extend(pool[:wanted])
        if len(pool) < wanted:
            print(f"  short on {kind}: wanted {wanted}, corpus has {len(pool)}",
                  file=sys.stderr)
    rng.shuffle(drawn)
    return drawn


def write(drawn: list[dict], out: Path = OUT) -> list[Path]:
    """Write one unlabelled file per paper.

    The `gold` block is empty and `labelled_by` is null: a file that reaches the
    metrics without someone having filled that in is a file whose provenance
    nobody can state.

    Args:
        drawn: Records from :func:`sample`.
        out: Directory to write into.

    Returns:
        The paths written.
    """
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for index, record in enumerate(drawn, start=1):
        path = out / f"{index:02d}.json"
        path.write_text(json.dumps({
            "id": f"{index:02d}",
            "lab": record["lab"],
            "date": record["date"],
            "title": record["title"],
            "url": record["url"],
            "text_source": record["text_source"],
            "document_type": record["document_type"],
            "text": record["text"],
            "gold": {
                "labelled_by": None,   # "human" | a model id. Required.
                "event_type": None,
                "mechanisms": [],
                "categories": [],
                "practices": [],
            },
            "review": {},
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    """Build the sample from the papers already in bronze."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    from sqlalchemy.orm import Session

    from app.db import get_engine, load_env
    from app.pipeline import adapters

    load_env()
    with Session(get_engine()) as session:
        records, _ = adapters.fetch_paper_abstracts(session)

    drawn = sample(records)
    paths = write(drawn, args.out)
    print(f"wrote {len(paths)} unlabelled papers to {args.out}")
    for path in paths:
        record = json.loads(path.read_text())
        print(f"  {path.name}  {record['document_type']:17}{record['lab']:16}"
              f"{record['title'][:52]}")
    print("\nEvery file needs `gold.labelled_by` filled in. If a model labels "
          "them,\nthat set is a cross-model proxy and must be reported as one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
