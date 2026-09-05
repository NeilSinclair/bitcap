"""Fetch release notes from the top-starred repositories as announcements.

A release note is a dated document with a title, a body and a permanent URL,
which is exactly the shape `research/announcements/fetch_announcements.py`
produces. So releases enter the existing extraction and scoring path as another
source rather than becoming a second kind of thing with its own vocabulary.

Why this source earns its place: on the probe run, `openai/codex`
`rust-v0.153.1` announced *"support for configuring GPT-6-Astra through the API
without changing the default model or showing it in the model picker"*, and
`openai-python` v3.8.0 shipped `gpt-6-astra` the same day. The announcements
corpus contains one bare "Astra" mention in an August safety post -- no model
name, no catalog fact. A model deliberately hidden from the model picker is not
something a lab blogs about, so the announcements leg structurally cannot see
it.

Two things the probe settled about the mechanics:

Volume, not coverage, is the problem. Only 10 of the top 25 repositories cut
releases at all, and that is fine -- six documents a lab has already curated
beat forty noisy ones. But `openai/codex` cut 240 releases in 90 days, so the
unit is "releases since the last run", never a fixed window.

Body length is not a proxy for signal, and filtering on it would have thrown
away the best item found. The codex release carrying GPT-6-Astra is 359
characters; DeepSeek's 12,534-character release is mostly font sizing and
scrollbars. Only genuinely empty bodies are dropped, because there is no
document to extract from.

**This module holds no state.** The cursor lives in `source_state.watermark`
and the documents land in `raw_articles`, both in the database, because the
deployed container has no disk (D31). An earlier version kept both in JSON
files next to a lock file, which needed a lock precisely because two writers
could reach the files; with one writer and one store, that whole apparatus is
gone rather than ported.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from harvest_github import API, _call, load_token  # noqa: F401 (load_token re-exported)

ROOT = Path(__file__).parent.parent.parent
GITHUB_SOURCES = ROOT / "config" / "github_sources.yaml"


def labs(path: Path = GITHUB_SOURCES) -> dict[str, str]:
    """Map GitHub org login to the register's lab id.

    `anthropics` is a GitHub login; `anthropic` is the id the announcements
    corpus and the register use. Mixing them splits one lab into two.

    Returns:
        Dict of org login to lab id, e.g. `{"anthropics": "anthropic"}`.
    """
    orgs = yaml.safe_load(path.read_text(encoding="utf-8"))["orgs"]
    return {login: cfg["lab"] for login, cfg in orgs.items()}


def as_announcement(org: str, repo: str, lab: str, rel: dict) -> dict:
    """Reshape one GitHub release into an announcements-corpus item.

    The `url` is the release's own permalink, which GitHub keeps resolvable
    against the tag -- unlike a `/blob/main/` path, which moves under the
    citation. It is also the identity of the document in `raw_articles`.

    Args:
        org: GitHub organisation login.
        repo: Repository name.
        lab: Register lab id the org belongs to.
        rel: Raw release object from the API.

    Returns:
        An item with the keys the announcements corpus carries, plus the
        repository fields a reader needs to place it.
    """
    return {
        "lab": lab,
        "url": rel["html_url"],
        "date": (rel.get("published_at") or "")[:10],
        "title": f'{org}/{repo} {rel["tag_name"]}',
        "text": rel.get("body") or "",
        "text_source": "github_release",
        "feed_category": "Release",
        "org": org,
        "repo": repo,
        "tag": rel["tag_name"],
        "release_name": rel.get("name"),
        "prerelease": bool(rel.get("prerelease")),
        "published_at": rel.get("published_at"),
    }


def new_releases(org: str, repo: str, token: str, lab: str,
                 cursor: str | None, backfill: int, cap: int,
                 max_pages: int) -> tuple[list[dict], dict]:
    """Fetch a repository's releases published since the cursor.

    Drafts are skipped -- they are not published documents. Prereleases are
    kept: DeepSeek publishes nothing else, so dropping them would drop the
    fastest-moving repository in the corpus entirely.

    Args:
        org: GitHub organisation login.
        repo: Repository name.
        token: GitHub personal access token.
        lab: Register lab id the org belongs to.
        cursor: Last `published_at` already seen, or None on a first run.
        backfill: Cap applied when there is no cursor.
        cap: Cap applied when there is a cursor.
        max_pages: Pages of 100 to walk before giving up on reaching the cursor.

    Returns:
        Tuple of (items newest first, stats dict). `stats["truncated"]` counts
        qualifying releases this call dropped to stay under the cap; releases
        beyond an unreached cursor are not in that number, because the unwalked
        history was never counted, so a caller reports it as a floor.
    """
    fresh: list[dict] = []
    empty = drafts = 0
    reached = cursor is None  # nothing to reach on a first run

    for page in range(1, max_pages + 1):
        body, _ = _call(
            f"{API}/repos/{org}/{repo}/releases?per_page=100&page={page}", token)
        if not body:
            reached = True
            break
        newer_on_page = False
        for rel in body:
            published = rel.get("published_at")
            if rel.get("draft") or not published:
                drafts += 1
                continue
            if cursor and published <= cursor:
                continue
            newer_on_page = True
            if not (rel.get("body") or "").strip():
                empty += 1
                continue
            fresh.append(as_announcement(org, repo, lab, rel))
        if len(body) < 100:
            reached = True  # the whole history fits, so nothing was missed
        # Stop on a page with nothing newer than the cursor, NOT on the first
        # old item. GitHub's list is ordered by neither `created_at` nor
        # `published_at` once a repository interleaves stable and prerelease
        # tags: one page of openai/codex carries 24 published_at inversions,
        # confirmed against the live API. Breaking at the first old release
        # would step over the newer ones sitting below it, and the cursor
        # advance below would then put them permanently out of reach.
        elif cursor and not newer_on_page:
            reached = True
        if reached:
            break

    fresh.sort(key=lambda item: item["published_at"], reverse=True)
    limit = cap if cursor else backfill
    truncated = max(0, len(fresh) - limit)
    # Which end the cap keeps decides whether the overflow is recoverable.
    # With a cursor, keep the OLDEST `cap` so the caller can advance the cursor
    # to the end of a contiguous run and the next call resumes there. Keeping
    # the newest would strand everything below them behind the advanced cursor
    # with no path back: re-running skips them, and clearing the cursor drops
    # into the backfill branch, which does not reach them either.
    # With no cursor the newest are correct -- a backfill deliberately abandons
    # history rather than replaying a repository's whole release log.
    kept = fresh[-limit:] if cursor else fresh[:limit]
    stats = {"seen": len(fresh), "kept": len(kept), "truncated": truncated,
             "empty": empty, "drafts": drafts, "reached_cursor": reached}
    return kept, stats


def next_cursor(items: list[dict]) -> str:
    """Pick the cursor to store after taking `items`.

    The invariant: never advance past a release that was not taken. `items`
    arrives newest first and, when a cap applied, is the *oldest* contiguous
    run of what qualified -- so its newest element is the high-water mark of an
    unbroken sequence, and the next run resumes at exactly the first release
    this one skipped.

    Args:
        items: The releases actually taken, newest first.

    Returns:
        The `published_at` to store as the cursor.
    """
    return items[0]["published_at"]
