"""Find names appearing for the first time anywhere in the corpus.

The LLM legs read one document and report what it says. That is the right split
for almost everything, and it has one blind spot it cannot be prompted out of:
a document does not know whether the name it uses is new. `openai/codex`
rust-v0.153.1 announced GPT-6-Astra as a configuration option and was scored
low-impact, correctly, because that is what the release note says. What made it
the most interesting item in the corpus is that the string had never appeared
anywhere before -- a property of the corpus, not the document.

So this module does no reading. It extracts identifiers by shape, finds the
earliest document mentioning each, and reports the ones whose earliest mention
is recent. It costs nothing, it is deterministic, and it improves as sources are
added, because "anywhere" means every row in `raw_articles` at once -- every leg
that produces articles, in one query.

The corpus is the database, not a set of files. On the deployed container there
is no disk (D31), and `raw_articles` is where every leg's documents already
land, so it is both the only durable store and the complete one.

Two questions, deliberately kept as two functions rather than forced into one
abstraction, because they are not the same operation:

`first_mentions` -- names appearing in prose for the first time. Extraction is
a regex over known model families, so it catches a new version or variant of a
family we know and misses a genuinely novel product name. That limit is real
and recorded in docs/decisions.md.

`unannounced_repos` -- repositories the labs ship and never write about. No
extraction at all: the repository name is already a field, so this is a plain
set difference and cannot be wrong about what it holds.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
CONFIG = ROOT / "config" / "entities.yaml"


def load_config() -> dict:
    """Read the entity config.

    Returns:
        The parsed `config/entities.yaml`.
    """
    return yaml.safe_load(CONFIG.read_text())


def model_pattern(families: list[str], max_parts: int) -> re.Pattern:
    """Build the model-identifier regex from the configured families.

    The shape is a family word, an optional separator, an optional `v`/`r`
    generation letter, a version number of at most `max_parts` dot-separated
    components, an optional fused letter, and up to two suffixes.

    Each of those optional parts is here because a real, current model name
    needs it, and the first version of this pattern -- which required the
    version to start with a digit and end at a word boundary -- silently
    matched none of them:

    - `[vr]?`  DeepSeek names every model this way. `deepseek-v4-pro`,
      `deepseek-v3.2`, `DeepSeek-R1` and 90-odd other occurrences in the live
      corpus extracted to nothing at all, so a DeepSeek release would have
      reported "no new names" rather than a miss.
    - `[a-z]?` fused to the digits, for `gpt-4o` and `gemma-3n`.
    - a numeric suffix alternative, for dated snapshots like
      `deepseek-r1-0528`.

    The negative lookahead is what keeps `haiku-0.0.7` -- google-deepmind's
    neural-network package, not Claude Haiku -- from matching as `haiku-0.0`.
    Without it the version simply truncates at `max_parts` and a package
    version becomes a model name. Rejecting outright is the honest behaviour:
    too many components means this is not a model identifier.

    Args:
        families: Model family words, e.g. `["gpt", "claude"]`.
        max_parts: Maximum dot-separated components in the version number.

    Returns:
        A compiled case-insensitive pattern with the identifier in group 1.
    """
    words = "|".join(re.escape(f) for f in sorted(families, key=len, reverse=True))
    version = rf"[vr]?\d+(?:\.\d+){{0,{max_parts - 1}}}[a-z]?"
    # A suffix is either lowercase-initial alphanumeric (`-astra`, `-luna`,
    # `-pro`) or all digits (`-0528`). A Hugging Face collection id such as
    # `llama-32-66f448ffc8c32f949b04c8cf` is neither -- it starts with a digit
    # and contains letters -- so its hash stays out of the identifier and every
    # link does not become a distinct new model.
    suffix = r"(?:[-_](?:[a-z][a-z0-9]*|\d+)){0,2}"
    return re.compile(
        rf"\b((?:{words})[-_ ]?{version}{suffix})(?!\.\d)\b", re.I)


def normalise(raw: str) -> str:
    """Reduce an identifier to a comparable form.

    `GPT-6-Astra`, `gpt 6 astra` and `GPT_6_ASTRA` are one name. Without this
    the same model reads as three distinct first mentions.

    A separator *between two digits* is also a decimal point, because labs
    spell one model two ways: Anthropic's post says "Opus 4.8" in prose and
    `claude-opus-4-8` for the API identifier, and both appear in the same
    document. Left alone they produce two rows for one model, which is exactly
    the kind of duplicate that makes a report untrustworthy. The rule is narrow
    on purpose -- `gpt-6-astra` and `claude-3-opus` have a word after the
    separator, not a digit, and are untouched.

    Args:
        raw: The identifier as it appeared in the text.

    Returns:
        Lowercased, separators collapsed to hyphens, digit-digit joins to dots.
    """
    name = re.sub(r"[\s_]+", "-", raw.strip().lower())
    return re.sub(r"(?<=\d)-(?=\d)", ".", name)


def identifiers(text: str, pattern: re.Pattern, deny: set[str]) -> dict[str, str]:
    """Extract model identifiers from one document.

    Args:
        text: Document text.
        pattern: Compiled pattern from `model_pattern`.
        deny: Normalised identifiers never reported.

    Returns:
        Dict of normalised identifier to the first raw spelling seen, so the
        report can quote the document rather than the normalised form.
    """
    found: dict[str, str] = {}
    for match in pattern.finditer(text or ""):
        key = normalise(match.group(1))
        if key not in deny:
            found.setdefault(key, match.group(1))
    return found


def load_corpus(session) -> list[dict]:
    """Read every article in bronze into a common document shape.

    One query over `raw_articles`, so the answer covers every leg that produces
    documents without this module knowing which legs exist. `source_file` is
    carried through because *which corpus got to a name first* is the signal --
    a name first seen in a release shipped in code before anyone wrote about it,
    and a name first seen in an announcement is an ordinary launch.

    A url is the identity of a document, and bronze already enforces that, so
    there is no deduplication to do here -- unlike the file corpora this
    replaced, where 175 of 236 announcements also sat in the six-month archive
    and every mention count was doubled.

    Args:
        session: Open session.

    Returns:
        List of `{url, date, lab, source, text}` documents.
    """
    from app import models as m
    from sqlalchemy import select

    docs = []
    for row in session.scalars(select(m.RawArticle)):
        payload = row.payload or {}
        fields = [str(payload.get(f) or "") for f in text_fields(payload)]
        docs.append({
            "url": row.url,
            "date": payload.get("date") or "",
            "lab": payload.get("lab"),
            "source": payload.get("text_source") or "",
            "corpus": row.source_file,
            # Kept apart as well as joined. The joined form is what identifiers
            # are matched against; `fields` is what a quote may be cut from, so
            # a window around a name in the title cannot run past the join and
            # emit a string that appears in neither the title nor the body.
            # This repository already has `verbatim.py` because a spliced quote
            # was seen once from the model; the deterministic path must not
            # reproduce it.
            "fields": fields,
            "text": " ".join(fields),
        })
    return docs


def text_fields(payload: dict) -> tuple[str, ...]:
    """Which payload fields are primary-source text for this document.

    A release item's `title` is built by `fetch_releases.as_announcement` as
    "{org}/{repo} {tag}" -- our string, not GitHub's. Reading it as source text
    did two invisible kinds of damage: the emitted quote began with words that
    appear nowhere in the release note, and every repository that cut a release
    injected its own name into the prose the unannounced-repository pass reads,
    so `anthropics/claude-code` scored as "written about" on the strength of a
    title this pipeline wrote.

    Args:
        payload: A `raw_articles.payload`.

    Returns:
        The field names to concatenate.
    """
    if payload.get("text_source") == "github_release":
        return ("text",)
    return ("title", "text")



def quote_for(text: str, raw: str, width: int = 140) -> str:
    """Pull the sentence-sized window around an identifier, for the citation.

    Args:
        text: Document text.
        raw: The identifier as spelled in this document.
        width: Characters of context either side.

    Returns:
        A single-line excerpt containing the identifier, or "" if absent.
    """
    idx = text.lower().find(raw.lower())
    if idx < 0:
        return ""
    start, end = max(0, idx - width), min(len(text), idx + len(raw) + width)
    return " ".join(text[start:end].split())



def quote_from_fields(fields: list[str], raw: str, width: int = 140) -> str:
    """Quote from the single field the identifier appears in.

    Quoting from the joined text lets a window around a name near the end of
    the title run past the join and into the body, producing a string that
    appears in neither -- exactly the splice `research/announcements/
    verbatim.py` exists to catch in the model's output.

    Args:
        fields: The document's primary-source fields, in order.
        raw: The identifier as spelled in this document.
        width: Characters of context either side.

    Returns:
        A single-line excerpt from one field, or "" if the identifier is in
        none of them.
    """
    for field in fields:
        found = quote_for(field, raw, width)
        if found:
            return found
    return ""


def first_mentions(docs: list[dict], cfg: dict,
                   today: date | None = None) -> list[dict]:
    """Report identifiers whose earliest appearance in the corpus is recent.

    Earliest-across-everything is the definition, not "in releases but not in
    announcements". A name is new because nothing we hold mentions it earlier,
    whichever source that would have been -- which is why the corpora are
    scanned together and why adding a source makes the result stricter rather
    than noisier.

    Args:
        docs: Documents from `load_corpora`.
        cfg: The parsed entity config.
        today: Reference date; defaults to today, injectable for tests.

    Returns:
        Newest first, each `{name, first_seen, url, lab, source, quote,
        mentions, corpora}`. `mentions` counts every document containing the
        name, so a one-off differs visibly from something now everywhere.
    """
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=cfg["new_within_days"])
    pattern = model_pattern(cfg["model_families"], cfg["max_version_parts"])
    deny = {normalise(d) for d in cfg.get("deny") or []}

    earliest: dict[str, dict] = {}
    counts: dict[str, int] = {}
    corpora: dict[str, set[str]] = {}

    # Dates are day-granular, so two documents can tie -- `rust-v0.153.1` and
    # `rust-v0.153.2` both carried GPT-6-Astra on 2026-09-03. Sorting makes the
    # winner the same on every run rather than an artefact of corpus order.
    for doc in sorted(docs, key=lambda d: (d["date"], d["url"])):
        if not doc["date"]:
            continue  # an undated document cannot establish firstness
        for key, raw in identifiers(doc["text"], pattern, deny).items():
            counts[key] = counts.get(key, 0) + 1
            corpora.setdefault(key, set()).add(doc["corpus"])
            best = earliest.get(key)
            if best is None or doc["date"] < best["date"]:
                earliest[key] = {**doc, "raw": raw}

    out = []
    for key, doc in earliest.items():
        if date.fromisoformat(doc["date"]) < cutoff:
            continue
        out.append({
            "name": key,
            "as_written": doc["raw"],
            "first_seen": doc["date"],
            "url": doc["url"],
            "lab": doc["lab"],
            "source": doc["source"],
            "quote": quote_from_fields(doc.get("fields") or [doc["text"]],
                                       doc["raw"]),
            "mentions": counts[key],
            "corpora": sorted(corpora[key]),
        })
    return sorted(out, key=lambda r: (r["first_seen"], r["name"]), reverse=True)


def unannounced_repos(docs: list[dict], rows: list[dict],
                      top: int) -> list[dict]:
    """Report top-starred repositories never named in any prose document.

    No extraction: the repository name is a field we already hold, so this is a
    set difference over exact strings and cannot be wrong about what it
    contains. It answers a different question from `first_mentions` -- not
    "this name is new" but "the labs ship this and never write about it".

    Args:
        docs: Documents from `load_corpora`.
        rows: Ranked repository rows, stars-descending.
        top: How many of the ranking to check.

    Returns:
        The unmentioned rows, stars-descending.
    """
    blob = " ".join(d["text"] for d in docs).lower()
    return [r for r in rows[:top] if r["repo"].lower() not in blob]


def main() -> None:
    """Run both passes over bronze and print the report."""
    import sys as _sys

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=None,
                    help="override config's unannounced_top")
    args = ap.parse_args()

    _sys.path.insert(0, str(ROOT))
    _sys.path.insert(0, str(ROOT / "research" / "github"))
    from app.db import get_engine, get_session, load_env

    import rank_repos

    load_env()
    cfg = load_config()
    top = args.top or cfg["unannounced_top"]
    session = get_session(get_engine())

    docs = load_corpus(session)
    by_source = {}
    for d in docs:
        by_source[d["corpus"]] = by_source.get(d["corpus"], 0) + 1
    print(f"{len(docs)} documents in bronze: "
          + ", ".join(f"{k} {v}" for k, v in sorted(by_source.items())))

    fresh = first_mentions(docs, cfg)
    print(f"\n=== {len(fresh)} first mentions in the last "
          f"{cfg['new_within_days']} days")
    # Which source got there first is the actual signal. A name whose earliest
    # document is an announcement is a lab launching something normally; a name
    # whose earliest document is a release shipped in code before anyone wrote
    # about it, which is the case no other leg of this pipeline can see.
    unheralded = [r for r in fresh if r["source"] == "github_release"]
    if unheralded:
        print(f"  {len(unheralded)} appeared in shipped code before any "
              f"announcement: {', '.join(r['name'] for r in unheralded)}")
    for row in fresh:
        print(f"  {row['first_seen']}  {row['name']:<22} "
              f"{row['lab'] or '?':<16} {row['source']:<16} "
              f"{row['mentions']} mention(s)")
        print(f"      {row['url']}")

    from app import models as m
    from sqlalchemy import select

    repos = [(r.org, r.repo, r.payload)
             for r in session.scalars(select(m.RawGithubRepo))]
    if repos:
        signals = yaml.safe_load(
            (ROOT / "config" / "repo_signals.yaml").read_text(encoding="utf-8"))
        ranked = rank_repos.rank(repos, signals)
        quiet = unannounced_repos(docs, ranked, top)
        print(f"\n=== {len(quiet)}/{top} top-starred repos are never mentioned "
              "in any prose document")
        for r in quiet[:15]:
            print(f"  {r['stars']:>9,}  {r['org']}/{r['repo']}")
    session.close()


if __name__ == "__main__":
    main()
