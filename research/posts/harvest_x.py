"""Read the last N days of original posts from the people in the register.

Three stages, run in order, each writing a committed artifact so the next one
is reproducible without repaying for the last.

  0 verify   One request resolving every handle at once. Yields the numeric ids
             the timeline endpoint needs, and -- the reason it is worth doing on
             its own -- the first direct read of these accounts this project has
             ever had. config/people.yaml has carried an `x_evidence` tier per
             handle precisely because x.com returned 402 to the old fetcher and
             no profile could be read; nine handles sit on `search_index`, which
             that file calls "a lead, not a fact". A resolved account with a
             matching name and bio promotes them.

  1 probe    Five posts per handle -- the API's own floor for `max_results` --
             read only to date them. The age of the oldest of the five gives the
             posting rate, and the rate sets the per-handle caps for stage 2.
             This exists because hand estimates of who posts a lot were wrong
             three times in four, and a wrong estimate here is spent money.

  2 pull     One page per handle at the computed cap. Never paginates.

WHY MEASURE BEFORE PULLING. X bills per post returned. The budget is a fixed
number of posts, and the distribution across these handles is extremely skewed
-- a few accounts post more than all the others combined. Splitting the budget
evenly wastes most of it on accounts that will never fill their share, while
starving the handful that carry the volume. Splitting it by measured rate costs
$0.68 to learn and is the difference between reading everyone and reading three
people.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

import x_client as xc

ROOT = Path(__file__).parent.parent.parent
CONFIG = ROOT / "config"
DOCS = ROOT / "research" / "docs"

HANDLES_OUT = DOCS / "x_handles.json"
PROBE_OUT = DOCS / "x_rate_probe.json"
# Pre-prefilter. The corpus that reaches the spine is written by prefilter.py.
RAW_OUT = DOCS / "x_posts_raw.json"
# The corpus the spine loads, after the deterministic prefilter.
CORPUS_OUT = DOCS / "posts_corpus.json"

# An hour, in days. Floor on the elapsed time used to compute a posting rate:
# five posts inside one minute would otherwise project to a rate no account has.
_MIN_ELAPSED_DAYS = 1.0 / 24.0


def register(config_dir: Path = CONFIG) -> tuple[list[dict], list[dict]]:
    """Every active person in config/people.yaml who has an X handle.

    Reads the register rather than restating the handles in this leg's own
    config, so there is one list of who works where. `departed` people are
    skipped: config/people.yaml calls rendering someone as a voice of a lab they
    left "the guard against this file's most likely failure", and reading their
    timeline into a lab's corpus is exactly that failure.

    Args:
        config_dir: Directory holding people.yaml and posts_sources.yaml.

    Returns:
        Tuple of (people to read, people deliberately excluded). Each entry
        carries the fields the corpus needs to attribute a post: lab, name,
        role, handle and the evidence tier that handle rests on.
    """
    people_cfg = yaml.safe_load((config_dir / "people.yaml").read_text())
    excluded_cfg = yaml.safe_load(
        (config_dir / "posts_sources.yaml").read_text()).get("exclude_handles", {})
    excluded = {h.lower(): why for h, why in (excluded_cfg or {}).items()}

    wanted, skipped = [], []
    for lab, block in people_cfg["labs"].items():
        for person in (block.get("people") or []):
            handle = person.get("x_handle")
            if not handle:
                continue
            entry = {
                "lab": lab,
                "name": person["name"],
                "role": person.get("role"),
                "handle": handle,
                "x_evidence": person.get("x_evidence"),
                # config/people.yaml marks a role CONTESTED in `notes` when its
                # sources disagree. Carried through so the UI can say so rather
                # than rendering a disputed title as fact.
                "role_contested": bool(person.get("role_contested")),
            }
            if handle.lower() in excluded:
                skipped.append({**entry, "reason": excluded[handle.lower()].strip()})
            else:
                wanted.append(entry)
    return wanted, skipped


def verify(people: list[dict], token: str, spend: xc.Spend,
           base_url: str = "https://api.x.com/2") -> dict:
    """Stage 0. Resolve every handle in one request and record what came back.

    Args:
        people: Entries from :func:`register`.
        token: Bearer token.
        spend: Ceiling enforcer.
        base_url: API root.

    Returns:
        A recordable dict: each person with the resolved account beside them,
        plus the handles that did not resolve. A handle that fails here is a
        register error -- a typo, a rename, a deleted account -- and is reported
        rather than quietly producing an empty timeline.
    """
    found, missing = xc.users_by([p["handle"] for p in people], token, spend,
                                 base_url=base_url)
    resolved, unresolved = [], []
    for person in people:
        account = found.get(person["handle"].lower())
        if account is None:
            unresolved.append({**person, "reason": "handle did not resolve at X"})
            continue
        resolved.append({
            **person,
            "user_id": account["id"],
            "account_name": account.get("name"),
            "account_username": account.get("username"),
            "description": account.get("description"),
            "verified": account.get("verified"),
            "verified_type": account.get("verified_type"),
            "followers": (account.get("public_metrics") or {}).get("followers_count"),
            "posts_all_time": (account.get("public_metrics") or {}).get("tweet_count"),
        })
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "resolved": resolved,
        "unresolved": unresolved,
        "missing_at_api": missing,
        "spend": spend.snapshot(),
    }


def projected_count(created: list[datetime], probe_size: int, window_days: int,
                    now: datetime) -> int | None:
    """How many posts this handle will have in the window, from a probe sample.

    THE COUNT RETURNED IS NOT THE SIGNAL -- the age of the oldest post is. X
    applies `exclude=replies,retweets` after assembling a page rather than
    before, so a page of five can come back holding one original and four
    filtered-out replies. Reading "fewer than we asked for" as "that is all
    there is" therefore understates every prolific replier: @sama returned four
    posts whose oldest was dated yesterday, which is a very active account, not
    an account with four posts in ninety days.

    So a sample is only complete when it actually reached back across the
    window. Otherwise it is a rate, measured over the span it does cover, and
    extrapolated.

    Args:
        created: Post timestamps from the probe, any order.
        probe_size: How many were requested. Retained because a full page is
            still weak evidence of a deep timeline.
        window_days: The window being planned for.
        now: Reference time.

    Returns:
        Projected posts in the window; exact when the sample reached back across
        it. **None when the probe returned nothing at all** -- which is genuinely
        unknown rather than zero, because a first page consisting entirely of
        replies is indistinguishable from silence at this sample size.
    """
    if not created:
        return None
    span = (now - min(created)).total_seconds() / 86400.0
    # Reaching back across nearly the whole window means we saw all of it.
    if span >= 0.9 * window_days:
        return len(created)
    return int(round(len(created) / max(span, _MIN_ELAPSED_DAYS) * window_days))


def allocate(projections: dict[str, int | None], remaining_posts: int,
             cfg: dict) -> dict[str, int]:
    """Turn measured rates into a per-handle page size.

    THE ASYMMETRY THIS EXPLOITS: X bills for posts *returned*, not for
    `max_results` requested. Asking a silent account for a hundred posts costs
    nothing, because nothing comes back. So the expensive mistake is asking too
    little of a prolific handle, never too much of a quiet one -- and a handle
    whose volume is unknown should be asked generously, not cautiously.

    Hence three groups. Handles with an unknown rate (the probe returned
    nothing) get a full page: free if they really are silent, and it resolves
    the ambiguity the probe could not. Handles below `take_whole_below` are read
    whole. The rest are capped so no single account can consume the budget.

    Args:
        projections: handle -> projected posts, or None where unknown.
        remaining_posts: Budget left after stages 0 and 1.
        cfg: Parsed posts_sources.yaml.

    Returns:
        handle -> `max_results` for stage 2, bounded to the API's 5..100.
    """
    rules = cfg["allocation"]
    whole, floor = rules["take_whole_below"], rules["per_handle_floor"]
    ceiling = min(rules["per_handle_ceiling"], 100)

    def page(n: int) -> int:
        return max(5, min(n, ceiling))

    caps: dict[str, int] = {}
    expected: dict[str, int] = {}
    prolific = []
    for handle, n in sorted(projections.items()):
        if n is None:
            # Unknown: a full page costs only what it returns, and the probe
            # already returned nothing, so the expected charge is zero. Budget
            # against that expectation rather than against the page size --
            # reserving 100 apiece for eight silent accounts would consume the
            # whole balance on posts that will never arrive, and starve the
            # handles that actually have volume.
            caps[handle], expected[handle] = ceiling, 0
        elif n == 0:
            continue
        elif n <= whole:
            caps[handle] = expected[handle] = page(n)
        else:
            prolific.append(handle)

    budget = remaining_posts - sum(expected.values())
    if prolific and budget > 0:
        each = max(floor, min(ceiling, budget // len(prolific)))
        if each * len(prolific) > budget:
            each = max(5, budget // len(prolific))
        for handle in prolific:
            caps[handle] = max(5, min(each, projections[handle]))
    return caps


def probe(resolved: list[dict], token: str, spend: xc.Spend, cfg: dict,
          now: datetime | None = None,
          base_url: str = "https://api.x.com/2") -> dict:
    """Stage 1. Date five posts per handle to measure its posting rate.

    Args:
        resolved: The `resolved` list from :func:`verify`.
        token: Bearer token.
        spend: Ceiling enforcer.
        cfg: Parsed posts_sources.yaml.
        now: Reference time, for tests.
        base_url: API root.

    Returns:
        A recordable dict carrying each handle's sample, its projected volume,
        and the caps :func:`allocate` derived from them.
    """
    now = now or datetime.now(timezone.utc)
    size = cfg["probe"]["size"]
    window = cfg["window_days"]
    start = xc.window_start(window, now)

    rows, projections = [], {}
    for person in resolved:
        try:
            posts = xc.user_posts(person["user_id"], token, spend,
                                  max_results=size, start_time=start,
                                  exclude=cfg["exclude"], base_url=base_url)
        except xc.SpendCeiling:
            raise
        except Exception as exc:  # one dead handle does not abort the probe
            rows.append({"handle": person["handle"], "lab": person["lab"],
                         "error": f"{type(exc).__name__}: {exc}"})
            continue
        created = [datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
                   for p in posts if p.get("created_at")]
        n = projected_count(created, size, window, now)
        projections[person["handle"]] = n
        rows.append({
            "handle": person["handle"], "lab": person["lab"],
            "name": person["name"],
            "returned": len(posts),
            "oldest": min(created).isoformat() if created else None,
            "newest": max(created).isoformat() if created else None,
            "projected_in_window": n,
            "complete": len(posts) < size,
        })

    remaining = spend.max_posts - spend.posts
    return {
        "measured_at": now.isoformat(),
        "window_days": window,
        "probe_size": size,
        "handles": sorted(rows, key=lambda r: -(r.get("projected_in_window") or 0)),
        "projections": projections,
        "remaining_post_budget": remaining,
        "caps": allocate(projections, remaining, cfg),
        "spend": spend.snapshot(),
    }


def to_record(post: dict, person: dict) -> dict:
    """Shape one X post into the article record the spine expects.

    `app/transform.py` hard-requires `lab`, `title`, `date`, `text` and
    `text_source`; a missing or unparseable date is the poison pill documented
    on `research/papers/paper_text.py`, so a post without `created_at` must
    never reach here.

    The URL is the post's own permalink, which is both the document we scored
    and the citation a reader clicks -- the same rule the papers leg follows.

    Args:
        post: One item from the X timeline response.
        person: The register entry for its author.

    Returns:
        An article-shaped record.
    """
    text = post["text"]
    urls = [u.get("expanded_url") for u in
            (post.get("entities") or {}).get("urls", []) if u.get("expanded_url")]
    quoted = [r for r in (post.get("referenced_tweets") or [])
              if r.get("type") == "quoted"]
    return {
        "lab": person["lab"],
        "url": f"https://x.com/{person['handle']}/status/{post['id']}",
        # Posts have no title. The opening clause is the closest honest thing,
        # and it is marked as derived so nobody mistakes it for one the author
        # wrote.
        "title": (text[:80] + "…") if len(text) > 80 else text,
        "date": post["created_at"][:10],
        "text": text,
        "text_source": "x_post",
        "author_handle": person["handle"],
        "author_name": person["name"],
        "author_role": person.get("role"),
        # Carried per post, not looked up later, so the corpus records what the
        # evidence for this attribution was at the time it was collected.
        "x_evidence": person.get("x_evidence"),
        "role_contested": bool(person.get("role_contested")),
        "is_quote": bool(quoted),
        "quoted_id": quoted[0]["id"] if quoted else None,
        "links": urls,
    }


def pull(resolved: list[dict], caps: dict[str, int], token: str, spend: xc.Spend,
         cfg: dict, now: datetime | None = None,
         base_url: str = "https://api.x.com/2") -> tuple[list[dict], list[dict]]:
    """Stage 2. One page per handle, at the cap the probe computed.

    Every request is clamped against what has actually been billed so far, so
    the ceiling binds on real spend rather than on the optimistic reservation.
    When the remaining budget falls below the API's five-post floor the run
    stops and says which handles it did not reach -- a truncated pull that
    reports itself is recoverable; one that looks complete is not.

    Args:
        resolved: The `resolved` list from :func:`verify`.
        caps: handle -> page size, from :func:`allocate`.
        token: Bearer token.
        spend: Ceiling enforcer.
        cfg: Parsed posts_sources.yaml.
        now: Reference time, for tests.
        base_url: API root.

    Returns:
        Tuple of (article-shaped records, unresolved). A handle that errors or
        is skipped for budget lands in `unresolved` with a reason rather than
        being silently absent.
    """
    now = now or datetime.now(timezone.utc)
    start = xc.window_start(cfg["window_days"], now)
    records, unresolved = [], []

    # Largest caps first: if the budget runs out, it should run out on the
    # accounts we already know are marginal, not on the ones carrying volume.
    ordered = sorted((p for p in resolved if caps.get(p["handle"])),
                     key=lambda p: -caps[p["handle"]])

    for person in ordered:
        room = spend.max_posts - spend.posts
        if room < 5:
            unresolved.append({"url": f"https://x.com/{person['handle']}",
                               "lab": person["lab"], "kind": "post",
                               "reason": "post budget exhausted before this handle"})
            continue
        size = max(5, min(caps[person["handle"]], room))
        try:
            posts = xc.user_posts(person["user_id"], token, spend,
                                  max_results=size, start_time=start,
                                  exclude=cfg["exclude"], base_url=base_url)
        except Exception as exc:  # one dead handle does not abort the leg
            unresolved.append({"url": f"https://x.com/{person['handle']}",
                               "lab": person["lab"], "kind": "post",
                               "reason": f"fetch failed: {type(exc).__name__}"})
            continue
        for post in posts:
            if not post.get("created_at") or not post.get("text"):
                unresolved.append({"url": f"https://x.com/{person['handle']}/status/"
                                          f"{post.get('id')}",
                                   "lab": person["lab"], "kind": "post",
                                   "reason": "post missing created_at or text"})
                continue
            records.append(to_record(post, person))
    return records, unresolved


def _write(path: Path, payload: dict) -> None:
    """Write a committed artifact as pretty JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run one stage. Stages are separate commands because each one costs money."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["verify", "probe", "pull", "filter"])
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be read and the projected cost, "
                             "and call nothing")
    args = parser.parse_args(argv)

    cfg = xc.settings()

    if args.stage == "filter":
        # Costs nothing and touches no network: it reads the pulled corpus and
        # the register's existing URLs, and is safe to re-run.
        import prefilter
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from app import models as m
        from app.db import get_engine, load_env

        load_env()
        raw = json.loads(RAW_OUT.read_text())
        with Session(get_engine()) as session:
            known = {u.rstrip("/") for (u,) in session.execute(select(m.RawArticle.url))}
        kept, dropped = prefilter.apply(raw["records"], cfg["prefilter"], known)
        _write(CORPUS_OUT, {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "window_days": raw["window_days"],
            "kept": len(kept), "dropped": len(dropped),
            "duplicates_announcement": sum(1 for k in kept
                                           if k["duplicates_announcement"]),
            "records": kept,
            "filtered_out": dropped,
        })
        print(f"{len(raw['records'])} pulled → {len(kept)} kept, {len(dropped)} dropped")
        print(f"  {sum(1 for k in kept if k['duplicates_announcement'])} of the kept "
              f"posts link to a document the register already holds")
        return 0

    people, skipped = register()
    print(f"{len(people)} handles to read, {len(skipped)} excluded by config")
    for entry in skipped:
        print(f"  excluded @{entry['handle']} ({entry['lab']})")

    budget = cfg["budget"]
    spend = xc.Spend(budget["max_posts_total"], budget["max_user_lookups"],
                     cfg["provider"]["price_per_post_usd"],
                     cfg["provider"]["price_per_user_usd"])

    if args.stage == "verify":
        cost = len(people) * cfg["provider"]["price_per_user_usd"]
        print(f"stage 0 verify: 1 request, {len(people)} users, ${cost:.2f}")
        if args.dry_run:
            return 0
        report = verify(people, xc.load_token(cfg["provider"]["token_env"]), spend,
                        cfg["provider"]["base_url"])
        _write(HANDLES_OUT, report)
        print(f"  resolved {len(report['resolved'])}, "
              f"unresolved {len(report['unresolved'])}, "
              f"spent ${spend.usd:.2f}")
        for row in report["unresolved"]:
            print(f"  UNRESOLVED @{row['handle']} ({row['lab']}) — {row['reason']}")
        return 0

    if not HANDLES_OUT.exists():
        sys.exit(f"run `verify` first: {HANDLES_OUT} is missing")
    resolved = json.loads(HANDLES_OUT.read_text())["resolved"]

    if args.stage == "pull":
        if not PROBE_OUT.exists():
            sys.exit(f"run `probe` first: {PROBE_OUT} is missing")
        measured = json.loads(PROBE_OUT.read_text())
        caps = measured["caps"]
        # The probe's reads were billed against the same balance, so stage 2's
        # ceiling is what it left behind, not the full configured total.
        spend = xc.Spend(measured["remaining_post_budget"],
                         budget["max_user_lookups"],
                         cfg["provider"]["price_per_post_usd"],
                         cfg["provider"]["price_per_user_usd"])
        expected = sum(min(measured["projections"].get(h) or 0, c)
                       for h, c in caps.items())
        print(f"stage 2 pull: {len(caps)} handles, ceiling "
              f"{measured['remaining_post_budget']} posts, expected ~{expected} "
              f"(${expected * cfg['provider']['price_per_post_usd']:.2f})")
        if args.dry_run:
            return 0
        records, unresolved = pull(
            resolved, caps, xc.load_token(cfg["provider"]["token_env"]), spend, cfg,
            base_url=cfg["provider"]["base_url"])
        _write(RAW_OUT, {"fetched_at": datetime.now(timezone.utc).isoformat(),
                         "window_days": cfg["window_days"],
                         "records": records, "unresolved": unresolved,
                         "spend": spend.snapshot()})
        print(f"  {len(records)} posts from "
              f"{len({r['author_handle'] for r in records})} handles, "
              f"{len(unresolved)} unresolved, spent ${spend.usd:.2f}")
        return 0

    cost = len(resolved) * cfg["probe"]["size"] * cfg["provider"]["price_per_post_usd"]
    print(f"stage 1 probe: {len(resolved)} requests, "
          f"{len(resolved) * cfg['probe']['size']} posts, ${cost:.2f}")
    if args.dry_run:
        return 0
    # The probe's own reads are billed against the same ceiling as the pull.
    report = probe(resolved, xc.load_token(cfg["provider"]["token_env"]), spend, cfg,
                   base_url=cfg["provider"]["base_url"])
    _write(PROBE_OUT, report)
    print(f"  spent ${spend.usd:.2f}; "
          f"{report['remaining_post_budget']} posts of budget left")
    print(f"  {'handle':20} {'ret':>4} {'proj':>5}  {'cap':>4}  oldest")
    for row in report["handles"]:
        if "error" in row:
            print(f"  {row['handle']:20} ERROR {row['error']}")
            continue
        cap = report["caps"].get(row["handle"], 0)
        print(f"  {row['handle']:20} {row['returned']:>4} "
              f"{row['projected_in_window']:>5}  {cap:>4}  {row['oldest'] or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
