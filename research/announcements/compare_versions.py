"""Compare two scoring-rule versions item by item.

Changing the rule changes every number, so the only honest way to judge the
change is to look at what actually moved and decide whether the new ranking is
better. This reports the largest movers in both directions and, more usefully,
whether the rule now agrees with the model's independent `notable` flag more
often than it did.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"


def load(version: str) -> dict:
    """Load a scored run, keyed by URL.

    Args:
        version: Prompt version, or "" for the original unversioned file.

    Returns:
        Mapping of url -> scored record.
    """
    name = "scored_announcements.json" if not version else f"scored_announcements_{version}.json"
    data = json.loads((DOCS / name).read_text())["scored"]
    return {a["url"]: a for a in data}


def agreement(records: list[dict]) -> dict:
    """Measure how far the computed score agrees with the model's own flag.

    The `notable` flag is produced independently of the score, so disagreement
    localises a fault in one of the two. Fewer disagreements is evidence the
    rule improved — not proof, since both could be wrong together.

    Args:
        records: Scored records.

    Returns:
        Counts of the two disagreement kinds and the totals behind them.
    """
    notable_low = [a for a in records if a["notable"] and a["score"] < 30]
    scored_not_notable = [a for a in records if a["score"] >= 30 and not a["notable"]]
    return {
        "notable": sum(1 for a in records if a["notable"]),
        "notable_but_low": len(notable_low),
        "high_but_not_notable": len(scored_not_notable),
        "disagreements": len(notable_low) + len(scored_not_notable),
    }


def main() -> None:
    """Print the comparison."""
    old, new = load(""), load("v2")
    shared = sorted(set(old) & set(new))
    print(f"{len(shared)} items in both runs\n")

    moves = []
    for url in shared:
        a, b = old[url], new[url]
        moves.append((b["score"] - a["score"], a, b))

    print("=== BAND TOTALS ===")
    print(f"{'band':<8}{'v1':>6}{'v2':>6}")
    for band in ("high", "medium", "low", "none"):
        o = sum(1 for u in shared if old[u]["band"] == band)
        n = sum(1 for u in shared if new[u]["band"] == band)
        print(f"{band:<8}{o:>6}{n:>6}")

    print("\n=== AGREEMENT WITH THE MODEL'S OWN `notable` FLAG ===")
    ao = agreement([old[u] for u in shared])
    an = agreement([new[u] for u in shared])
    print(f"{'':<26}{'v1':>6}{'v2':>6}")
    for k in ("notable", "notable_but_low", "high_but_not_notable", "disagreements"):
        print(f"{k:<26}{ao[k]:>6}{an[k]:>6}")

    print("\n=== BIGGEST RISES ===")
    for delta, a, b in sorted(moves, key=lambda m: -m[0])[:12]:
        et = f"{a['event_type']} -> {b['event_type']}" if a["event_type"] != b["event_type"] else a["event_type"]
        print(f"  {a['score']:>5} -> {b['score']:<5} (+{delta:>4.1f})  [{b['lab'][:4]}] {b['title'][:44]}")
        print(f"         {et}")

    print("\n=== BIGGEST FALLS ===")
    for delta, a, b in sorted(moves, key=lambda m: m[0])[:8]:
        et = f"{a['event_type']} -> {b['event_type']}" if a["event_type"] != b["event_type"] else a["event_type"]
        print(f"  {a['score']:>5} -> {b['score']:<5} ({delta:>5.1f})  [{b['lab'][:4]}] {b['title'][:44]}")
        print(f"         {et}")

    print("\n=== THE FOUR KNOWN FAILURES ===")
    for needle in ("S-1 to the SEC", "Confidential submission", "Redeploying Claude Fable",
                   "Broadcom unveil", "Opus 4.8"):
        hit = next((m for m in moves if needle in m[1]["title"]), None)
        if hit:
            delta, a, b = hit
            print(f"  {a['score']:>5} -> {b['score']:<5}  {b['title'][:52]}")

    print("\n=== NEW MECHANISMS IN USE ===")
    for mid in ("custom_silicon_substitution", "lab_capital_access"):
        hits = [b for _, _, b in moves if any(m["id"] == mid for m in b["mechanisms"])]
        print(f"  {mid}: {len(hits)}")
        for b in hits[:4]:
            print(f"      {b['score']:>5}  {b['title'][:56]}")

    other = [b for _, _, b in moves if b["event_type"] == "other"]
    print(f"\n=== RESIDUAL HEALTH ===")
    print(f"  'other' bucket: {len(other)} items "
          f"(v1 corporate_other: {sum(1 for u in shared if old[u]['event_type'] == 'corporate_other')})")
    print(f"  of those, notable: {sum(1 for b in other if b['notable'])}")
    print(f"  of those, scoring > 0: {sum(1 for b in other if b['score'] > 0)}")


if __name__ == "__main__":
    main()
