"""Attach profile and personal-repository data to the contributor register.

Two purposes. First, the profile ``company`` field is a second employment
signal: it catches staff who commit under a personal address, whom the email
domain alone would miss. Second, the profile is where a person's other
channels actually live — blog, X handle, and their own public repositories —
which is what the ingestion pipeline needs to follow them.

Profiles are cached per login so re-runs cost no rate limit.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from harvest_github import API, _call, load_token  # noqa: E402

ROOT = Path(__file__).parent.parent.parent
DOCS = ROOT / "research" / "docs"
CACHE = DOCS / "github_profiles"

# Matched against the free-text profile company field, which people write by
# hand: "Anthropic", "@anthropic", "Anthropic PBC" all occur.
LAB_COMPANY = re.compile(r"anthropic", re.I)

LAB_PATTERNS = {"anthropics": r"anthropic", "openai": r"open\s*ai"}


def profile(login: str, token: str) -> dict:
    """Fetch and cache one user profile plus their own public repositories.

    Args:
        login: GitHub login.
        token: GitHub personal access token.

    Returns:
        Dict of profile fields and a 'personal_repos' list. A deleted account
        yields a record with 'missing' set rather than raising.
    """
    cached = CACHE / f"{login}.json"
    if cached.exists():
        return json.loads(cached.read_text())

    try:
        user, _ = _call(f"{API}/users/{login}", token)
    except Exception as exc:  # account renamed or deleted since the commit
        rec = {"login": login, "missing": str(exc)}
        cached.write_text(json.dumps(rec))
        return rec

    repos, _ = _call(
        f"{API}/users/{login}/repos?per_page=100&sort=pushed&type=owner", token
    )
    rec = {
        "login": user["login"],
        "name": user.get("name"),
        "company": user.get("company"),
        "bio": user.get("bio"),
        "blog": user.get("blog") or None,
        "twitter": user.get("twitter_username"),
        "location": user.get("location"),
        "followers": user.get("followers", 0),
        "created_at": user.get("created_at"),
        "personal_repos": [
            {
                "name": r["name"],
                "stars": r["stargazers_count"],
                "pushed_at": r["pushed_at"],
                "description": r["description"],
                "language": r["language"],
            }
            for r in repos
            if not r["fork"] and not r["archived"]
        ],
    }
    cached.write_text(json.dumps(rec))
    return rec


def worth_fetching(person: dict, min_commits: int) -> bool:
    """Decide whether a contributor justifies two API calls.

    Anyone already evidenced as staff is always fetched. Beyond that, a single
    commit in twelve months is almost always an external drive-by pull request,
    and fetching the whole tail costs far more time than the tail is worth.

    Args:
        person: Aggregated person record.
        min_commits: Threshold applied to non-staff contributors.

    Returns:
        True if the profile should be fetched.
    """
    return (
        person["employment"] in ("confirmed", "profile", "handle", "vendor")
        or person["commits"] >= min_commits
    )


def enrich(
    org: str, company_pattern: str | None = None, min_commits: int = 2
) -> dict:
    """Merge profile data into the aggregated register.

    Employment is re-resolved with the profile in hand. A corporate commit
    address remains the strongest evidence; a profile naming the lab is
    accepted next; the handle convention is only used when neither is present.

    Args:
        org: GitHub organisation login.
        company_pattern: Regex matched against the free-text profile company
            field. Defaults to the Anthropic pattern when not supplied.
        min_commits: Non-staff contributors below this commit count are left
            unenriched rather than costing two API calls each.

    Returns:
        The register dict with each person carrying a 'profile' block and a
        revised 'employment' value.
    """
    global LAB_COMPANY
    if company_pattern:
        LAB_COMPANY = re.compile(company_pattern, re.I)
    token = load_token()
    CACHE.mkdir(parents=True, exist_ok=True)
    reg = json.loads((DOCS / f"github_people_{org}.json").read_text())

    targets = [p for p in reg["people"] if worth_fetching(p, min_commits)]
    print(f"enriching {len(targets)} of {len(reg['people'])} contributors")

    for p in reg["people"]:
        p["profile"] = {}
        p["profile_fetched"] = False

    for i, person in enumerate(targets, 1):
        prof = profile(person["login"], token)
        person["profile"] = prof
        person["profile_fetched"] = True

        company = prof.get("company") or ""
        person["company_matches_lab"] = bool(LAB_COMPANY.search(company))

        if person["employment"] == "confirmed":
            pass  # committed under a lab address; nothing beats that
        elif person["company_matches_lab"]:
            person["employment"] = "profile"
        elif person["employment"] == "handle":
            pass
        elif prof.get("missing"):
            person["employment"] = "deleted"

        if i % 50 == 0:
            print(f"  {i}/{len(targets)}", flush=True)

    reg["totals"]["profiles_fetched"] = len(targets)
    reg["totals"]["profile_min_commits"] = min_commits
    reg["totals"]["employment"] = {
        k: sum(1 for p in reg["people"] if p["employment"] == k)
        for k in ("confirmed", "profile", "handle", "vendor", "deleted", "unknown")
    }
    return reg


if __name__ == "__main__":
    org = sys.argv[1] if len(sys.argv) > 1 else "anthropics"
    result = enrich(org, LAB_PATTERNS.get(org))
    print(f"\nprofiles fetched: {result['totals']['profiles_fetched']}")
    dest = DOCS / f"github_enriched_{org}.json"
    dest.write_text(json.dumps(result, indent=2))

    print("\nemployment evidence:")
    for k, v in result["totals"]["employment"].items():
        print(f"  {k:<12} {v}")

    staff = [
        p
        for p in result["people"]
        if p["employment"] in ("confirmed", "profile", "handle")
    ]
    print(f"\nstaff identified: {len(staff)}")
    with_channel = [
        p
        for p in staff
        if p["profile"].get("blog") or p["profile"].get("twitter")
    ]
    print(f"  with a blog or X handle: {len(with_channel)}")
    own_repos = [p for p in staff if p["profile"].get("personal_repos")]
    print(f"  with public personal repos: {len(own_repos)}")
