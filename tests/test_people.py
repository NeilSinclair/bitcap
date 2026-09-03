"""Tests for config/people.yaml -- the per-lab register of senior people.

The failure this file is exposed to is not a crash. It is a person quietly
outliving their entry: a title that moved, a lab that was left, a handle typed
from memory. None of that breaks a parse, and all of it produces a confidently
wrong attribution downstream.

So these tests check the two things that keep the file honest rather than
merely well-formed:

  1. Every claim carries a citation, and a citation that was actually read says
     so. A URL recorded but not fetched must admit it.
  2. Nobody is listed as both present and departed, and no lab id exists here
     that the announcements register does not know about.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
PEOPLE = ROOT / "config" / "people.yaml"
SOURCES = ROOT / "config" / "sources.yaml"

X_EVIDENCE_TIERS = {"own_site", "self_post", "lab_post", "search_index"}
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


@pytest.fixture(scope="module")
def people():
    return yaml.safe_load(PEOPLE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def register_ids():
    return {lab["id"] for lab in yaml.safe_load(SOURCES.read_text())["labs"]}


def _all_people(people):
    for lab_id, lab in people["labs"].items():
        for person in lab["people"]:
            yield lab_id, person


class TestRegisterJoin:
    """people.yaml is keyed to the announcements register, not to itself."""

    def test_every_lab_id_exists_in_sources_yaml(self, people, register_ids):
        unknown = set(people["labs"]) - register_ids
        assert not unknown, f"people.yaml names labs the register does not: {unknown}"

    def test_every_registered_lab_has_an_entry(self, people, register_ids):
        """A lab with no people entry is indistinguishable from one nobody did."""
        missing = register_ids - set(people["labs"])
        assert not missing, f"labs in the register with no people entry: {missing}"

    def test_labels_match_the_register(self, people):
        labels = {
            lab["id"]: lab["label"] for lab in yaml.safe_load(SOURCES.read_text())["labs"]
        }
        wrong = {
            lab_id: (lab["label"], labels[lab_id])
            for lab_id, lab in people["labs"].items()
            if lab["label"] != labels[lab_id]
        }
        assert not wrong, f"label drift against sources.yaml: {wrong}"


class TestEveryClaimIsCited:
    """No role and no channel without a source. This is the project's bar."""

    def test_every_person_has_a_name_and_a_role(self, people):
        bad = [
            (lab, p.get("name"))
            for lab, p in _all_people(people)
            if not p.get("name") or not p.get("role")
        ]
        assert not bad, f"person missing name or role: {bad}"

    def test_every_person_has_at_least_one_source(self, people):
        bare = [
            (lab, p["name"]) for lab, p in _all_people(people) if not p.get("sources")
        ]
        assert not bare, f"person with no citation: {bare}"

    def test_every_source_url_is_resolvable_in_form(self, people):
        bad = [
            (lab, p["name"], s.get("url"))
            for lab, p in _all_people(people)
            for s in p["sources"]
            if not str(s.get("url", "")).startswith("https://")
        ]
        assert not bad, f"source url is not an https URL: {bad}"

    def test_a_source_that_was_not_fetched_says_so(self, people):
        """`fetched: null` is allowed, but only with a note admitting it.

        Recording a URL nobody read, with no flag, is how an unverified claim
        gets promoted to a verified one by nothing more than sitting in a list.
        """
        silent = [
            (lab, p["name"], s["url"])
            for lab, p in _all_people(people)
            for s in p["sources"]
            if s.get("fetched") is None and "NOT FETCHED" not in (s.get("note") or "")
        ]
        assert not silent, f"unfetched source with no admission in its note: {silent}"

    def test_no_source_is_dated_after_the_research_date(self, people):
        researched = people["researched"]
        assert isinstance(researched, date)
        future = [
            (lab, p["name"], s["url"], d)
            for lab, p in _all_people(people)
            for s in p["sources"]
            for d in (s.get("published"), s.get("fetched"))
            if isinstance(d, date) and d > researched
        ]
        assert not future, f"source dated after the research date: {future}"


class TestChannels:
    """A handle, its URL and its evidence tier move together or not at all."""

    def test_handle_and_url_and_evidence_are_all_present_or_all_absent(self, people):
        broken = [
            (lab, p["name"])
            for lab, p in _all_people(people)
            if len({p.get("x_handle") is None, p.get("x_url") is None,
                    p.get("x_evidence") is None}) != 1
        ]
        assert not broken, f"partial X record -- handle, url and evidence disagree: {broken}"

    def test_x_url_matches_the_handle(self, people):
        mismatched = [
            (lab, p["name"], p["x_handle"], p["x_url"])
            for lab, p in _all_people(people)
            if p.get("x_handle") and p["x_url"] != f"https://x.com/{p['x_handle']}"
        ]
        assert not mismatched, f"x_url does not match x_handle: {mismatched}"

    def test_handles_are_well_formed(self, people):
        bad = [
            (lab, p["name"], p["x_handle"])
            for lab, p in _all_people(people)
            if p.get("x_handle") and not HANDLE_RE.match(p["x_handle"])
        ]
        assert not bad, f"not a valid X handle: {bad}"

    def test_evidence_tier_is_one_of_the_declared_tiers(self, people):
        bad = [
            (lab, p["name"], p["x_evidence"])
            for lab, p in _all_people(people)
            if p.get("x_evidence") and p["x_evidence"] not in X_EVIDENCE_TIERS
        ]
        assert not bad, f"unknown x_evidence tier: {bad}"

    def test_blog_urls_are_https(self, people):
        bad = [
            (lab, p["name"], p["blog"])
            for lab, p in _all_people(people)
            if p.get("blog") and not p["blog"].startswith("https://")
        ]
        assert not bad, f"blog is not an https URL: {bad}"


class TestDepartures:
    """The guard against attributing a lab to someone who left it."""

    def test_nobody_is_both_current_and_departed(self, people):
        clashes = []
        for lab_id, lab in people["labs"].items():
            current = {p["name"] for p in lab["people"]}
            gone = {d["name"] for d in (lab.get("departed") or [])}
            clashes += [(lab_id, n) for n in current & gone]
        assert not clashes, f"listed as both current and departed: {clashes}"

    def test_every_departure_carries_a_source_and_a_date(self, people):
        bad = [
            (lab_id, d.get("name"))
            for lab_id, lab in people["labs"].items()
            for d in (lab.get("departed") or [])
            if not d.get("source", "").startswith("https://") or not d.get("left")
        ]
        assert not bad, f"departure without a dated source: {bad}"

    def test_no_duplicate_names_within_a_lab(self, people):
        dupes = []
        for lab_id, lab in people["labs"].items():
            names = [p["name"] for p in lab["people"]]
            dupes += [(lab_id, n) for n in set(names) if names.count(n) > 1]
        assert not dupes, f"duplicate person within a lab: {dupes}"


class TestTheChecksCanFail:
    """A validator that cannot fail reports success over a file nobody checked."""

    def test_unfetched_source_check_catches_a_silent_one(self):
        doc = {
            "researched": date(2026, 9, 3),
            "labs": {
                "x": {
                    "label": "X",
                    "people": [
                        {
                            "name": "A",
                            "role": "r",
                            "sources": [{"url": "https://e.com", "fetched": None}],
                        }
                    ],
                }
            },
        }
        silent = [
            s["url"]
            for _, p in _all_people(doc)
            for s in p["sources"]
            if s.get("fetched") is None and "NOT FETCHED" not in (s.get("note") or "")
        ]
        assert silent == ["https://e.com"]

    def test_partial_x_record_check_catches_a_handle_without_evidence(self):
        doc = {
            "labs": {
                "x": {
                    "label": "X",
                    "people": [
                        {
                            "name": "A",
                            "role": "r",
                            "x_handle": "a",
                            "x_url": "https://x.com/a",
                            "x_evidence": None,
                            "sources": [],
                        }
                    ],
                }
            }
        }
        broken = [
            p["name"]
            for _, p in _all_people(doc)
            if len({p.get("x_handle") is None, p.get("x_url") is None,
                    p.get("x_evidence") is None}) != 1
        ]
        assert broken == ["A"]
