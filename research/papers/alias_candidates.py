"""Propose person aliases from the co-authorship graph.

Two renderings of one name ("Sam Marks" / "Samuel Marks") split that person's
record in half and corrupt any ranking built on it. Merging two genuinely
different people is the worse error, so this module only ever *proposes*: it
writes `config/aliases.yaml` with every candidate marked `proposed`, and nothing
is applied until a human sets `confirmed: true`.

Evidence used:
  * Name shape - same surname, compatible given name.
  * Co-authorship overlap - the two names' co-author sets. Distinct people who
    happen to share a surname do not share collaborators.
  * Co-appearance - two names on the SAME article are different people. This is
    a hard disqualifier, not a score.

Shared *organisation* is deliberately not used as evidence: people move between
labs, so a matching affiliation is weak, and a differing one proves nothing.

Usage:
    python research/alias_candidates.py
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
REGISTER = ROOT / "research" / "docs" / "anthropic_contributors.json"
PROPOSALS = ROOT / "config" / "aliases_proposed.yaml"
CONFIRMED = ROOT / "config" / "aliases.yaml"

# A short given name must share this many leading characters with the long form.
# "nick"/"nicholas" share three; a two-character rule would pair unrelated names.
PREFIX_CHARS = 3
SHORT_NAME_MAX = 6


def strip_accents(text: str) -> str:
    """Remove combining marks so accented and plain spellings compare equal."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def name_parts(name: str) -> tuple[str, str]:
    """Split a name into comparable given and family parts.

    Middle names and initials are dropped: they are the most inconsistently
    rendered part of a byline and carry no disambiguating power here.

    Args:
        name: Name as published.

    Returns:
        Tuple of (given name, family name), lowercased and unpunctuated.
    """
    clean = re.sub(r"[^\w\s]", " ", strip_accents(name).lower())
    tokens = [t for t in clean.split() if t]
    if not tokens:
        return ("", "")
    if len(tokens) == 1:
        return ("", tokens[0])
    return (tokens[0], tokens[-1])


def given_names_compatible(a: str, b: str) -> bool:
    """Whether two given names could be long and short forms of one name.

    Args:
        a: One given name.
        b: The other given name.

    Returns:
        True if identical, or if the shorter is a plausible diminutive of the
        longer ("sam"/"samuel", "nick"/"nicholas").
    """
    if not a or not b:
        return True  # an initial-only or single-token name cannot rule it out
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    if len(short) > SHORT_NAME_MAX or short == long:
        return False
    shared = 0
    for x, y in zip(short, long):
        if x != y:
            break
        shared += 1
    return shared >= PREFIX_CHARS


def normalised_full(name: str) -> str:
    """Full name reduced to letters and spaces, for exact-variant detection."""
    clean = re.sub(r"[^\w\s]", " ", strip_accents(name).lower())
    return " ".join(clean.split())


def coauthor_graph(articles: list[dict]) -> tuple[dict[str, set], dict[str, set]]:
    """Build per-name co-author sets and the articles each name appears on.

    Args:
        articles: Article records with parsed bylines.

    Returns:
        Tuple of (name -> set of co-author names, name -> set of article urls).
    """
    coauthors: dict[str, set] = defaultdict(set)
    appears: dict[str, set] = defaultdict(set)
    for art in articles:
        names = [a["name"] for a in art["authors"]]
        for n in names:
            appears[n].add(art["url"])
            coauthors[n].update(x for x in names if x != n)
    return coauthors, appears


def propose(register: dict) -> list[dict]:
    """Generate alias candidates with their evidence.

    Args:
        register: Parsed contributor register.

    Returns:
        Candidate records, strongest evidence first.
    """
    people = {p["name"]: p for p in register["people"]}
    coauthors, appears = coauthor_graph(register["articles"])

    buckets: dict[str, list[str]] = defaultdict(list)
    for name in people:
        buckets[name_parts(name)[1]].append(name)

    out: list[dict] = []
    for family, names in buckets.items():
        if len(names) < 2:
            continue
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if not given_names_compatible(name_parts(a)[0], name_parts(b)[0]):
                    continue
                together = appears[a] & appears[b]
                shared = coauthors[a] & coauthors[b]
                union = coauthors[a] | coauthors[b]
                jaccard = len(shared) / len(union) if union else 0.0
                if normalised_full(a) == normalised_full(b) and not together:
                    verdict, reason = "same_string", (
                        "identical once punctuation and accents are removed; "
                        "co-author evidence not required"
                    )
                elif together:
                    verdict, reason = "reject", (
                        f"both names appear on the same article ({len(together)}), "
                        "so they are different people"
                    )
                elif len(shared) >= 3 or jaccard >= 0.30:
                    verdict, reason = "likely_same", (
                        f"{len(shared)} shared co-authors, jaccard {jaccard:.2f}"
                    )
                elif shared:
                    verdict, reason = "review", (
                        f"only {len(shared)} shared co-author(s), jaccard {jaccard:.2f}"
                    )
                else:
                    verdict, reason = "weak", "no shared co-authors"
                canonical, variant = sorted((a, b), key=lambda n: (-len(n), n))
                out.append(
                    {
                        "canonical": canonical,
                        "variant": variant,
                        "verdict": verdict,
                        "reason": reason,
                        "shared_coauthors": len(shared),
                        "jaccard": round(jaccard, 3),
                        "appearances": [people[canonical]["appearances"],
                                        people[variant]["appearances"]],
                        "family": family,
                    }
                )
    order = {"same_string": 0, "likely_same": 1, "review": 2, "weak": 3, "reject": 4}
    out.sort(key=lambda r: (order[r["verdict"]], -r["shared_coauthors"]))
    return out


def to_yaml(candidates: list[dict], confirmed: dict[str, str] | None = None) -> str:
    """Render candidates as a reviewable config file.

    Existing confirmations are carried forward. Regenerating this file used to
    reset every `confirmed` flag to false, silently discarding human review -
    which it did, once, wiping ten confirmed merges.

    Args:
        candidates: Candidate records from :func:`propose`.
        confirmed: Existing variant -> canonical map to preserve.

    Returns:
        YAML text.
    """
    lines = [
        "# Person aliases for the contributor register.",
        "#",
        "# Generated by research/alias_candidates.py from the co-authorship graph.",
        "# NOTHING HERE IS APPLIED until `confirmed` is set to true by a human:",
        "# merging two different people misattributes work, which is worse than",
        "# leaving one person split across two records.",
        "#",
        "# Aliases apply at AGGREGATION time only. The per-article bylines keep",
        "# exactly what each page published, so a wrong merge is reversible.",
        "#",
        "# verdict: same_string | likely_same | review | weak | reject",
        "#   (evidence, not a decision - a human still sets `confirmed`)",
        "",
        "version: 1",
        "aliases:",
    ]
    confirmed = confirmed or {}
    for c in candidates:
        kept = confirmed.get(c["variant"]) == c["canonical"]
        lines += [
            f"  - canonical: {c['canonical']}",
            f"    variant: {c['variant']}",
            f"    confirmed: {'true' if kept else 'false'}",
            f"    verdict: {c['verdict']}",
            f"    evidence: {c['reason']}",
            f"    appearances: [{c['appearances'][0]}, {c['appearances'][1]}]",
            "",
        ]
    return "\n".join(lines)


def load_confirmed(path: Path = CONFIRMED) -> dict[str, str]:
    """Read confirmed aliases as a variant -> canonical map.

    Parsed without a YAML dependency: the file's shape is fixed by
    :func:`to_yaml`, and the register must not gain a dependency for it.

    Args:
        path: Alias file path.

    Returns:
        Mapping of variant name to canonical name for confirmed entries only.
    """
    if not path.exists():
        return {}
    out, current = {}, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- canonical:"):
            current = {"canonical": stripped.split(":", 1)[1].strip()}
        elif stripped.startswith("variant:"):
            current["variant"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("confirmed:"):
            if stripped.split(":", 1)[1].strip().lower() == "true":
                if current.get("variant") and current.get("canonical"):
                    out[current["variant"]] = current["canonical"]
    return out


def main() -> None:
    """Generate the alias proposal and print a summary."""
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    candidates = propose(register)
    existing = load_confirmed()
    # Proposals go to their own file. config/aliases.yaml is human-owned and is
    # never written here - overwriting it once already destroyed ten confirmed
    # merges, because a working alias removes the duplicate that proposed it.
    new = [c for c in candidates if existing.get(c["variant"]) != c["canonical"]]
    PROPOSALS.parent.mkdir(parents=True, exist_ok=True)
    PROPOSALS.write_text(to_yaml(new, existing), encoding="utf-8")

    counts: dict[str, int] = defaultdict(int)
    for c in candidates:
        counts[c["verdict"]] += 1
    print(f"{len(candidates)} candidates, {len(new)} not already confirmed -> {PROPOSALS}")
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"\n{'canonical':26} {'variant':24} {'verdict':12} evidence")
    for c in new:
        print(
            f"{c['canonical'][:26]:26} {c['variant'][:24]:24} "
            f"{c['verdict']:12} {c['reason']}"
        )
    confirmed = load_confirmed()
    print(f"\nconfirmed and in effect: {len(confirmed)}")


if __name__ == "__main__":
    main()
