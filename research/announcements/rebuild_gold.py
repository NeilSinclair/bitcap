"""Rebuild the gold set so it samples the corpus it is used to test.

Three things went wrong at once, none of them visible from inside the set:

1. The window was cut from six months to three, so ten of the twenty articles
   are no longer in the corpus at all.
2. The set was drawn using scores computed from 205-character RSS summaries.
   OpenAI is 79.6% of the corpus, so for four fifths of it the selection was
   effectively blind -- any article whose signal was not visible in two
   sentences could never have been picked.
3. The labels predate the practice axis and were pre-filled at prompt v4.

The out-of-corpus ten are moved to `hard_cases/` rather than deleted. They are
genuinely hard items -- an open-weights frontier release, a government recall, a
5GW capacity deal -- and remain useful for error analysis. They are simply not
part of any accuracy figure, because they are not part of what is scored.

The ten replacements are drawn **at random within lab strata**. Random,
deliberately: the existing ten were score-selected, which the folder README
already flagged as unable to reveal a kind of signal the system misses
entirely, and a draw blind to the system's own opinion is the only part of the
set that can. Most blind draws will be noise, and an empty tag list is the
correct answer for them -- which is itself the behaviour most worth testing.

Stratified by lab because an unstratified draw does not fix the mix. The set was
45% OpenAI against a corpus that is 79.6% OpenAI, and one unstratified draw
came back five-five, leaving it at 45%. Lab is a known fact about the article,
not a judgement the pipeline makes, so stratifying on it costs none of the
blindness that matters. Slots go where the set is furthest below the corpus.

The draw is seeded so the set is reproducible from the register.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
GOLD = Path(__file__).parent / "test"
ARTICLES = GOLD / "articles"
HARD = GOLD / "hard_cases"
REGISTER = ROOT / "research" / "docs" / "announcements.json"

SEED = 20260901
DRAW = 10


def stratified_draw(
    pool: list[dict], kept: collections.Counter, register: list[dict], n: int
) -> list[dict]:
    """Draw n articles at random, allocating slots by how far each lab is short.

    Args:
        pool: Corpus articles not already in the gold set.
        kept: Lab counts of the gold articles being retained.
        register: The whole corpus, for its lab proportions.
        n: Number to draw.

    Returns:
        The drawn articles. Within a lab the choice is uniformly random, so the
        draw stays blind to anything the pipeline thinks about the article.
    """
    size = len(kept) + n
    share = collections.Counter(a["lab"] for a in register)
    shortfall = {
        lab: max(0, round(size * count / len(register)) - kept[lab])
        for lab, count in share.items()
    }
    total = sum(shortfall.values()) or 1

    rng = random.Random(SEED)
    quota = {lab: int(n * short / total) for lab, short in shortfall.items()}
    # Largest remainder, so the slots lost to integer division go to the lab
    # furthest below its corpus share rather than to whichever sorts first.
    while sum(quota.values()) < n:
        lab = max(shortfall, key=lambda l: (shortfall[l] - quota[l], share[l]))
        quota[lab] += 1

    drawn = []
    for lab, want in quota.items():
        available = [a for a in pool if a["lab"] == lab]
        drawn += rng.sample(available, min(want, len(available)))
    print("  slots by shortfall against the corpus: "
          + ", ".join(f"{lab} {q}" for lab, q in quota.items() if q))
    return drawn


def main() -> None:
    """Move out-of-corpus articles aside and draw their replacements."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    register = json.loads(REGISTER.read_text())
    in_corpus = {a["url"] for a in register}
    existing = {json.loads(p.read_text())["url"]: p for p in ARTICLES.glob("*.json")}

    stale = {url: path for url, path in existing.items() if url not in in_corpus}
    print(f"out of corpus: {len(stale)} -> hard_cases/")

    pool = [a for a in register if a["url"] not in existing]
    kept = collections.Counter(
        json.loads(p.read_text())["lab"]
        for p in ARTICLES.glob("*.json")
        if json.loads(p.read_text())["url"] in in_corpus
    )
    drawn = stratified_draw(pool, kept, register, DRAW)
    print(f"pool: {len(pool)} unused corpus articles; drawing {DRAW} at random "
          f"within lab strata (seed {SEED})\n")

    used = {int(json.loads(p.read_text())["id"]) for p in ARTICLES.glob("*.json")}
    next_id = max(used) + 1
    for article in drawn:
        print(f"  {next_id:02d}  {article['lab']:10} {article['date']}  "
              f"{len(article['text']):6,}c  {article['title'][:44]}")
        if not args.dry_run:
            record = {
                "id": f"{next_id:02d}",
                **{k: article[k] for k in
                   ("lab", "date", "title", "url", "text_source", "text")},
                # No pre-fill. These were drawn blind, and pre-filling them from
                # the system's own tags would re-introduce the anchoring the
                # blind draw exists to avoid. prefill_gold.py fills them next.
                "gold": {"event_type": None, "mechanisms": [], "categories": [],
                         "practices": [], "notes": ""},
                "review": {"votes": 0, "needs_review": None,
                           "why": "drawn at random, not yet classified",
                           "tag_votes": {}},
                "system": {},
            }
            if article.get("archive_snapshot"):
                record["archive_snapshot"] = article["archive_snapshot"]
            (ARTICLES / f"{next_id:02d}.json").write_text(json.dumps(record, indent=2))
        next_id += 1

    if not args.dry_run:
        HARD.mkdir(exist_ok=True)
        for path in stale.values():
            shutil.move(str(path), str(HARD / path.name))

    # In a dry run nothing has moved or been written, so the final set has to
    # be simulated rather than read back -- reading the folder would report the
    # old set and quietly claim the draw changed nothing.
    if args.dry_run:
        final = [json.loads(p.read_text()) for p in ARTICLES.glob("*.json")
                 if json.loads(p.read_text())["url"] in in_corpus] + drawn
    else:
        final = [json.loads(p.read_text()) for p in sorted(ARTICLES.glob("*.json"))]
    mix = collections.Counter(g["lab"] for g in final)
    corpus_mix = collections.Counter(a["lab"] for a in register)
    print(f"\n{'would be' if args.dry_run else 'gold set is now'} {len(final)} articles")
    for lab in sorted(corpus_mix, key=corpus_mix.get, reverse=True):
        print(f"  {lab:10} gold {mix[lab]:2} ({100*mix[lab]/max(1,len(final)):4.1f}%)"
              f"   corpus {100*corpus_mix[lab]/len(register):4.1f}%")


if __name__ == "__main__":
    main()
