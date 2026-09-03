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

Employment is read from the commit email domain, per org, configured in
config/github_sources.yaml -- not hardcoded here. How much of an org's human
commits carry a domain match, and how reliable a work-handle convention is,
both vary a great deal by lab: Anthropic's own org evidences roughly three
fifths of human commits via `@anthropic.com` directly; Mistral evidences
close to none (near-universal use of GitHub's noreply-relay addresses, a
real finding about that org, not a harvesting gap; see that config file's
own notes per org for what to expect from each one).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"
GITHUB_SOURCES = ROOT / "config" / "github_sources.yaml"

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
    # Google's internal source-of-truth sync tool. Found via the
    # google-deepmind harvest: 199 commits across 11 repos under the display
    # name "Copybara-Service", uncaught by BOT_PATTERNS since its login ends
    # in "-github", not "-bot" or "[bot]" -- it would otherwise have ranked
    # in the top 10 committers by volume, ahead of real staff.
    "copybara-github",
    # GitHub's own Copilot coding agent. Found via the facebookresearch
    # harvest: 43 commits across 3 repos, committing under the resolved
    # login "Copilot" even though one of its own commit `name` fields is
    # literally "copilot-swe-agent[bot]" and its email domain is GitHub's
    # noreply relay -- uncaught by BOT_PATTERNS because "copilot" contains
    # neither "-bot" (no hyphen before "bot") nor "[bot]" at the login level.
    "copilot",
    # Speakeasy's SDK-generation automation. Found via the mistralai
    # harvest: 20 commits across 2 repos under the resolved login
    # "speakeasybot" -- profile confirms it live ("Speakeasy Bot", bio "I'm
    # a helpful bot that automates Speakeasy operations"), but the login
    # ends in "bot" with no hyphen, so BOT_PATTERNS' `-bot$` doesn't match
    # it -- same class of miss as copybara-github and copilot above.
    "speakeasybot",
    # Mistral's own CI automation. Found via the same harvest: 50 commits
    # across 4 repos under the resolved login "maiengineering" -- a blank
    # profile (no name, bio or company), a role-based team mailbox
    # (engineering@mistral.ai, not a personal address) rather than a
    # personal one, and half its commits carry the raw commit `name`
    # "Buildkite CI" (a named CI tool) instead of a person's name. No
    # pattern in the login itself suggests a bot at all -- this one was
    # only caught by checking the commit-level `name`/`email` fields, not
    # the login string.
    "maiengineering",
    # GoReleaser's own release automation. Found via the openai harvest: 1
    # commit, profile confirms it live ("GoReleaser Bot", bio "I'm a bot,
    # do not @ me."), login ends in "bot" with no hyphen so `-bot$` doesn't
    # match -- same class of miss as speakeasybot.
    "goreleaserbot",
}

BOT_PATTERNS = re.compile(r"(\[bot\]$|^bot-|-bot$|^stainless)", re.I)

# Work-account suffixes: staff hold a second GitHub account for lab work and
# mark it in the handle. The convention differs per lab and must be supplied
# per lab, since a pattern that matches nothing silently yields zero merges
# rather than an error.
WORK_SUFFIX = re.compile(r"[-_](ant|anthropic)$", re.I)

# A regex that matches nothing: the explicit "no known work-handle
# convention" sentinel for config/github_sources.yaml entries that omit
# work_suffix, so an absent convention yields zero merges rather than
# silently reusing another lab's pattern.
NEVER_MATCHES = r"(?!)"


def load_labs() -> dict[str, tuple[str | list[str] | None, str, bool]]:
    """Load per-org GitHub config from config/github_sources.yaml.

    Returns:
        Dict keyed by GitHub org login, each value a
        (domain, work_suffix, domain_shared) tuple -- `domain` is a string,
        a list of strings (a lab with more than one active corporate
        domain), or None (no reliable domain). `domain_shared` marks a
        commit-email domain that belongs to the parent company rather than
        the lab itself (see the file's own header comment).
    """
    data = yaml.safe_load(GITHUB_SOURCES.read_text())
    return {
        org: (entry.get("domain"), entry.get("work_suffix") or NEVER_MATCHES,
              entry.get("domain_shared", False))
        for org, entry in data["orgs"].items()
    }


LABS = load_labs()

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
    org_domain: str | list[str] | None,
    work_suffix: str,
    domain_shared: bool = False,
) -> dict:
    """Build the per-person register for one organisation.

    `org_domain` and `work_suffix` are required, not defaulted to
    Anthropic's own values -- a caller that omitted them used to silently
    score whatever org it named against Anthropic's domain and handle
    convention, returning an all-`unknown` (or worse, wrongly `confirmed`)
    employment column that looks like clean data rather than a
    misconfiguration (bitcap-reviewer finding #8). Look the real values up
    per org from `config/github_sources.yaml` (`load_labs()` below), never
    guess them.

    Args:
        org: GitHub organisation login, used to locate the harvest file.
        org_domain: Email domain(s) that evidence employment at the lab. A
            single string for the common case; a list where a lab genuinely
            uses more than one (Meta: both @meta.com and @fb.com are active
            in real commits, confirmed live). `None` when no reliable domain
            exists at all (Mistral).
        work_suffix: Regex for this lab's work-account handle convention.
        domain_shared: True when `org_domain` belongs to the parent company
            rather than the lab specifically (e.g. @google.com is
            Alphabet-wide, not DeepMind-specific). A corp-email match then
            tags `confirmed_org_wide` instead of `confirmed`, so the two
            evidence strengths are never conflated downstream.

    Returns:
        Dict with 'people' (list of per-person records, most commits first),
        'aliases' (merges applied) and 'totals' (run-level counts).
    """
    suffix = re.compile(work_suffix, re.I)
    org_domains = {org_domain} if isinstance(org_domain, str) else set(org_domain or [])
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
        corp = sum(n for d, n in domains.items() if d in org_domains)
        vendor = sorted(d for d in domains if d in VENDOR_DOMAINS)

        if corp:
            # committed under a lab address; org-wide domains (Alphabet,
            # etc.) are real but weaker evidence than a lab-owned one
            employment = "confirmed_org_wide" if domain_shared else "confirmed"
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
                for k in ("confirmed", "confirmed_org_wide", "handle", "vendor", "unknown")
            },
        },
    }


if __name__ == "__main__":
    import sys

    org = sys.argv[1] if len(sys.argv) > 1 else "anthropics"
    if org not in LABS:
        sys.exit(f"no lab config for {org}; add it to config/github_sources.yaml")
    domain, suffix, domain_shared = LABS[org]
    result = aggregate(org, domain, suffix, domain_shared)
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
    # Not p["employment"][0]: "confirmed" and "confirmed_org_wide" share a
    # first letter, and collapsing them here would silently erase the one
    # distinction domain_shared exists to preserve.
    legend = {"confirmed": "c", "confirmed_org_wide": "C", "handle": "h",
              "vendor": "v", "unknown": "?"}
    print("\ntop 30 (E=employment: c confirmed, C confirmed org-wide, "
          "h handle, v vendor, ? unknown):")
    for p in result["people"][:30]:
        name = p["names"][0] if p["names"] else ""
        print(
            f"  {p['commits']:>5} {p['repo_count']:>3}r  "
            f"{legend.get(p['employment'], '?')}  "
            f"{p['login']:<24} {name[:30]}"
        )
