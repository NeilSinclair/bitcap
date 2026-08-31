"""Turn raw GitHub commit records into a per-person contribution register.

Three jobs, in order:

1. Drop machine accounts. Release automation and code generators outrank every
   human on commit count, so an unfiltered ranking is meaningless.
2. Merge accounts belonging to the same human. Commit email is the primary
   key: staff routinely hold a work and a personal account, and the shared
   address links them where a handle heuristic only guesses.
3. Record several signals per person rather than collapsing to one score. The
   papers research established that a single computed importance number does
   not survive scrutiny; the same applies here.

Employment is read from the commit email domain. Roughly three fifths of human
commits in this org carry an ``@anthropic.com`` address, which is direct
evidence rather than inference; the ``-ant`` handle convention is kept only as
a fallback for people who commit with a private address.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"

# Machine accounts. Matched exactly, in addition to the [bot] suffix that
# GitHub App accounts always carry.
BOT_LOGINS = {
    "actions-user",
    "github-actions",
    "stainless-bot",
    "stainless-app",
    "dependabot",
    "renovate",
    "claude",
    "web-flow",
}

BOT_PATTERNS = re.compile(r"(\[bot\]$|^bot-|-bot$|^stainless)", re.I)

# Work-account suffixes: staff hold a second GitHub account for lab work and
# mark it in the handle. The convention differs per lab and must be supplied
# per lab, since a pattern that matches nothing silently yields zero merges
# rather than an error.
WORK_SUFFIX = re.compile(r"[-_](ant|anthropic)$", re.I)

LABS = {
    "anthropics": ("anthropic.com", r"[-_](ant|anthropic)$"),
    "openai": ("openai.com", r"[-_](oai|openai)$"),
}

# GitHub substitutes this domain when a user hides their address, so it
# identifies nobody and must never be used to link accounts.
PRIVATE_DOMAIN = "users.noreply.github.com"

# Vendor domains: real people, but not lab staff.
VENDOR_DOMAINS = {"stainless.com"}

# Some org repos are read-only mirrors of upstream open-source projects. GitHub
# reports fork=False and mirror_url=None for these because they were created by
# push rather than by forking, so only the description gives them away. Their
# contributors are upstream maintainers, not lab staff, and one such mirror
# outweighed every real repo in the org on raw commit count.
MIRROR_MARKER = re.compile(r"\bmirror of\b", re.I)


def is_bot(login: str) -> bool:
    """Report whether a login is a machine account.

    Args:
        login: GitHub login.

    Returns:
        True if the account is automation rather than a person.
    """
    return login.lower() in BOT_LOGINS or bool(BOT_PATTERNS.search(login))


def alias_pairs(people: dict, work_suffix: re.Pattern = WORK_SUFFIX) -> dict[str, str]:
    """Map duplicate accounts onto one canonical login per human.

    Two rules, strongest first. Accounts sharing a real commit email are the
    same person outright. Failing that, a login that reduces to another login
    after stripping the work suffix is treated as the same person, which covers
    staff whose work account hides its address.

    Private no-reply addresses are excluded: GitHub issues them per account, so
    they never link two accounts and would merge strangers if trusted.

    Args:
        people: Per-login records, each carrying an 'emails' set.
        work_suffix: Compiled pattern matching this lab's work-handle suffix.

    Returns:
        Mapping of alias login -> canonical login. The account with more
        commits wins, since that is the one worth profiling.
    """
    merges: dict[str, str] = {}

    by_email: dict[str, list[str]] = defaultdict(list)
    for login, rec in people.items():
        for email in rec["emails"]:
            if email and not email.endswith(PRIVATE_DOMAIN):
                by_email[email].append(login)

    for logins in by_email.values():
        if len(logins) < 2:
            continue
        canonical = max(logins, key=lambda x: people[x]["commits"])
        for login in logins:
            if login != canonical:
                merges[login] = canonical

    lower = {login.lower(): login for login in people}
    canonicals = set(merges.values())
    for login in people:
        # Skip accounts the email rule already settled. Without the canonical
        # check the suffix rule points a merge target back at its own alias,
        # producing a two-way cycle.
        if login in merges or login in canonicals:
            continue
        stem = work_suffix.sub("", login).lower()
        if stem != login.lower() and stem in lower and lower[stem] != login:
            merges[login] = lower[stem]

    # Collapse chains so no alias points at another alias.
    for alias in list(merges):
        seen = {alias}
        target = merges[alias]
        while target in merges and target not in seen:
            seen.add(target)
            target = merges[target]
        merges[alias] = target
    return {a: c for a, c in merges.items() if a != c}


def aggregate(
    org: str,
    org_domain: str = "anthropic.com",
    work_suffix: str = r"[-_](ant|anthropic)$",
) -> dict:
    """Build the per-person register for one organisation.

    Args:
        org: GitHub organisation login, used to locate the harvest file.
        org_domain: Email domain that evidences employment at the lab.
        work_suffix: Regex for this lab's work-account handle convention.

    Returns:
        Dict with 'people' (list of per-person records, most commits first),
        'aliases' (merges applied) and 'totals' (run-level counts).
    """
    suffix = re.compile(work_suffix, re.I)
    raw = json.loads((DOCS / f"github_commits_{org}.json").read_text())

    people = defaultdict(
        lambda: {
            "commits": 0,
            "repos": defaultdict(int),
            "first": None,
            "last": None,
            "names": set(),
            "emails": set(),
            "domains": defaultdict(int),
        }
    )
    bot_commits = unattributed = 0
    mirrors = sorted(
        r for r, d in raw.items() if MIRROR_MARKER.search(d.get("description") or "")
    )

    for repo, data in raw.items():
        if repo in mirrors:
            continue
        for commit in data["commits"]:
            login = commit["login"]
            if not login:
                # Commit author never resolved to a GitHub account: a bad
                # local git config, or an account since deleted.
                unattributed += 1
                continue
            if is_bot(login):
                bot_commits += 1
                continue

            rec = people[login]
            rec["commits"] += 1
            rec["repos"][repo] += 1
            date = commit["date"]
            rec["first"] = min(rec["first"] or date, date)
            rec["last"] = max(rec["last"] or date, date)
            if commit["name"]:
                rec["names"].add(commit["name"])
            email = (commit["email"] or "").lower()
            if email:
                rec["emails"].add(email)
                rec["domains"][email.split("@")[-1]] += 1

    merges = alias_pairs(people, suffix)
    for alias, canonical in merges.items():
        src, dst = people[alias], people[canonical]
        dst["commits"] += src["commits"]
        for repo, n in src["repos"].items():
            dst["repos"][repo] += n
        for domain, n in src["domains"].items():
            dst["domains"][domain] += n
        dst["first"] = min(dst["first"], src["first"])
        dst["last"] = max(dst["last"], src["last"])
        dst["names"] |= src["names"]
        dst["emails"] |= src["emails"]
        dst.setdefault("merged_from", []).append(alias)
        del people[alias]

    out = []
    for login, rec in people.items():
        handles = [login] + rec.get("merged_from", [])
        domains = dict(sorted(rec["domains"].items(), key=lambda kv: -kv[1]))
        corp = sum(n for d, n in domains.items() if d == org_domain)
        vendor = sorted(d for d in domains if d in VENDOR_DOMAINS)

        if corp:
            employment = "confirmed"  # committed under a lab address
        elif vendor:
            employment = "vendor"
        elif any(suffix.search(h) for h in handles):
            employment = "handle"  # work-account convention only
        else:
            employment = "unknown"

        out.append(
            {
                "login": login,
                "commits": rec["commits"],
                "repo_count": len(rec["repos"]),
                "repos": dict(sorted(rec["repos"].items(), key=lambda kv: -kv[1])),
                "first_commit": rec["first"],
                "last_commit": rec["last"],
                "names": sorted(rec["names"]),
                "merged_from": rec.get("merged_from", []),
                "employment": employment,
                "corp_commits": corp,
                "vendor_domains": vendor,
                "domains": domains,
            }
        )
    out.sort(key=lambda p: -p["commits"])

    return {
        "people": out,
        "aliases": merges,
        "mirrors_excluded": mirrors,
        "totals": {
            "repos": len(raw) - len(mirrors),
            "commits": sum(
                r["total"] for k, r in raw.items() if k not in mirrors
            ),
            "people": len(out),
            "bot_commits": bot_commits,
            "unattributed_commits": unattributed,
            "employment": {
                k: sum(1 for p in out if p["employment"] == k)
                for k in ("confirmed", "handle", "vendor", "unknown")
            },
        },
    }


if __name__ == "__main__":
    import sys

    org = sys.argv[1] if len(sys.argv) > 1 else "anthropics"
    if org not in LABS:
        sys.exit(f"no lab config for {org}; add it to LABS")
    domain, suffix = LABS[org]
    result = aggregate(org, domain, suffix)
    dest = DOCS / f"github_people_{org}.json"
    dest.write_text(json.dumps(result, indent=2))

    t = result["totals"]
    print(f"repos                 {t['repos']}")
    print(f"  mirrors excluded    {result['mirrors_excluded']}")
    print(f"commits               {t['commits']}")
    print(f"  bot                 {t['bot_commits']}")
    print(f"  unattributed        {t['unattributed_commits']}")
    print(f"people                {t['people']}")
    for k, v in t["employment"].items():
        print(f"  {k:<19} {v}")
    print(f"aliases merged        {len(result['aliases'])}")
    print("\ntop 30 (E=employment: c confirmed, h handle, v vendor, ? unknown):")
    for p in result["people"][:30]:
        name = p["names"][0] if p["names"] else ""
        print(
            f"  {p['commits']:>5} {p['repo_count']:>3}r  {p['employment'][0]}  "
            f"{p['login']:<24} {name[:30]}"
        )
