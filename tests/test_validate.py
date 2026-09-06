"""Tests for config/validate.py's per-method and cross-file config checks.

The silent failure this suite exists to catch: a lab entry missing a key its
declared discovery method needs, or a github_sources.yaml org pointing at a
lab id that doesn't exist -- both currently invisible to `validate.py` and
would only surface deep into a live run (bitcap-reviewer finding #7).
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "config"))

from validate import check_github_sources, check_sources


def _write(path: Path, doc: dict) -> None:
    path.write_text(yaml.safe_dump(doc))


class TestCheckSources:
    def test_real_config_has_no_errors(self):
        # The actual committed file must always pass this check.
        assert check_sources(ROOT / "config") == []

    def test_missing_method_specific_key_is_an_error(self, tmp_path):
        _write(
            tmp_path / "sources.yaml",
            {
                "labs": [
                    {
                        "id": "eighth-lab",
                        "method": "listing_pagination",
                        "index_url": "https://x.example/blog/",
                        "url_contains": "/blog/",
                        "text_source": "full_text",
                        # page_param missing -- the real bug this check exists for
                    }
                ]
            },
        )
        errors = check_sources(tmp_path)
        assert any("page_param" in e for e in errors)

    def test_unknown_method_is_an_error(self, tmp_path):
        _write(
            tmp_path / "sources.yaml",
            {"labs": [{"id": "x", "method": "carrier_pigeon"}]},
        )
        errors = check_sources(tmp_path)
        assert any("unknown method" in e for e in errors)

    def test_wayback_cdx_does_not_require_text_source(self, tmp_path):
        # from_wayback_cdx hardcodes text_source itself -- it must not be
        # flagged as missing just because sitemap/listing_pagination need it.
        _write(
            tmp_path / "sources.yaml",
            {
                "labs": [
                    {
                        "id": "x",
                        "method": "wayback_cdx",
                        "index_url": "https://x.ai/news",
                        "url_contains": "/news/",
                    }
                ]
            },
        )
        assert check_sources(tmp_path) == []

    def test_rss_only_requires_index_url(self, tmp_path):
        _write(
            tmp_path / "sources.yaml",
            {"labs": [{"id": "x", "method": "rss", "index_url": "https://x.example/rss"}]},
        )
        assert check_sources(tmp_path) == []


class TestCheckGithubSources:
    def test_real_config_has_no_errors(self):
        srcs = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
        tracked = {lab["id"] for lab in srcs["labs"]}
        assert check_github_sources(ROOT / "config", tracked) == []

    def test_lab_not_in_sources_yaml_is_an_error(self, tmp_path):
        _write(
            tmp_path / "github_sources.yaml",
            {"orgs": {"some-org": {"lab": "not-a-real-lab", "domain_shared": False}}},
        )
        errors = check_github_sources(tmp_path, {"anthropic", "openai"})
        assert any("not-a-real-lab" in e for e in errors)

    def test_missing_lab_field_is_an_error(self, tmp_path):
        _write(tmp_path / "github_sources.yaml", {"orgs": {"some-org": {"domain_shared": False}}})
        errors = check_github_sources(tmp_path, {"anthropic"})
        assert any("no 'lab'" in e for e in errors)

    def test_non_bool_domain_shared_is_an_error(self, tmp_path):
        _write(
            tmp_path / "github_sources.yaml",
            {"orgs": {"some-org": {"lab": "anthropic", "domain_shared": "yes"}}},
        )
        errors = check_github_sources(tmp_path, {"anthropic"})
        assert any("domain_shared" in e for e in errors)

    def test_invalid_work_suffix_regex_is_an_error(self, tmp_path):
        _write(
            tmp_path / "github_sources.yaml",
            {"orgs": {"some-org": {"lab": "anthropic", "domain_shared": False, "work_suffix": "[unclosed"}}},
        )
        errors = check_github_sources(tmp_path, {"anthropic"})
        assert any("work_suffix" in e for e in errors)

    def test_missing_file_is_an_error_not_a_crash(self, tmp_path):
        errors = check_github_sources(tmp_path, {"anthropic"})
        assert any("missing" in e for e in errors)


class TestTheBackfillKeyIsValidated:
    """`backfill: wayback` is matched by equality in two places, so anything
    wrong about it fails silently and the lab keeps its feed summaries -- the
    exact shape of the two-day OpenAI regression (decisions.md D55). These
    branches shipped untested once: deleting the whole block left the suite
    green.
    """

    def _sources(self, lab_extra):
        lab = {"id": "openai", "label": "OpenAI", "method": "rss",
               "index_url": "https://openai.com/blog/rss.xml",
               "text_source": "rss_summary", **lab_extra}
        return {"version": 1, "window_months": 3, "labs": [lab]}

    def _check(self, tmp_path, doc):
        _write(tmp_path / "sources.yaml", doc)
        return check_sources(tmp_path)

    def test_a_known_backfill_passes(self, tmp_path):
        assert self._check(tmp_path, self._sources({"backfill": "wayback"})) == []

    def test_no_backfill_at_all_passes(self, tmp_path):
        assert self._check(tmp_path, self._sources({})) == []

    def test_an_unknown_backfill_value_is_an_error(self, tmp_path):
        errors = self._check(tmp_path, self._sources({"backfill": "waybck"}))
        assert any("unknown backfill" in e for e in errors)

    def test_a_dropped_letter_in_the_key_is_caught(self, tmp_path):
        """`backfil` is precisely the case a near-miss check cannot catch:
        collapsing case and underscores does not recover a dropped letter, so
        the first version of this check passed the very example its own comment
        cited. An allowlist has no such gap."""
        errors = self._check(tmp_path, self._sources({"backfil": "wayback"}))
        assert any("backfil" in e and "unknown key" in e for e in errors)

    def test_an_underscored_variant_is_caught(self, tmp_path):
        errors = self._check(tmp_path, self._sources({"back_fill": "wayback"}))
        assert any("unknown key" in e for e in errors)

    def test_the_committed_file_has_no_unknown_keys(self):
        """The allowlist must be maintained. Adding a key to sources.yaml
        without adding it here fails loudly, rather than the key being quietly
        declared dead -- which is the direction that costs nothing to get
        wrong and everything to leave wrong."""
        assert check_sources(ROOT / "config") == []


class TestUnknownKeysAreCaughtInsideSecondaryChannels:
    """The allowlist walked only the primary lab entry, so a typo inside an
    `also:` channel passed clean -- the gap the class above claims an allowlist
    does not have, one level down.

    Found in review of D66. `feed_pagees: 3` in the meta-ai newsroom channel
    validated green; `from_rss` then read page 1 only, which reaches back two
    months rather than three, dropping the June items the channel was added
    for. Every check stayed green, which is the same silent-under-coverage
    shape D66 itself was about. The required-keys loop directly above already
    iterated `[lab] + also` for exactly this reason.
    """

    def _sources(self, also_extra):
        lab = {"id": "meta-ai", "label": "Meta AI", "method": "rss",
               "index_url": "https://ai.meta.com/blog/",
               "text_source": "full_text",
               "also": [{"method": "rss",
                         "index_url": "https://about.fb.com/news/tag/ai/feed/",
                         **also_extra}]}
        return {"version": 1, "window_months": 3, "labs": [lab]}

    def _check(self, tmp_path, doc):
        _write(tmp_path / "sources.yaml", doc)
        return check_sources(tmp_path)

    def test_the_new_paging_keys_are_allowed(self, tmp_path):
        doc = self._sources({"feed_pages": 3, "feed_page_param": "paged"})
        assert self._check(tmp_path, doc) == []

    def test_a_typo_inside_a_secondary_channel_is_caught(self, tmp_path):
        errors = self._check(tmp_path, self._sources({"feed_pagees": 3}))
        assert any("feed_pagees" in e and "unknown key" in e for e in errors)

    def test_a_typo_on_the_primary_entry_is_still_caught(self, tmp_path):
        doc = self._sources({})
        doc["labs"][0]["feed_pagees"] = 3
        errors = self._check(tmp_path, doc)
        assert any("feed_pagees" in e and "unknown key" in e for e in errors)

    def test_the_paging_keys_are_allowed_on_a_primary_entry_too(self, tmp_path):
        """They were usable only inside `also:` by accident of the loop's
        scope -- on a primary entry the allowlist would have rejected them."""
        doc = self._sources({})
        doc["labs"][0]["feed_pages"] = 2
        assert self._check(tmp_path, doc) == []

    def test_a_nested_also_is_rejected(self, tmp_path):
        """`channels()` merges one level and never recurses, so a nested
        `also` is a silent no-op rather than a second tier of channels."""
        errors = self._check(tmp_path, self._sources({"also": []}))
        assert any("'also'" in e and "unknown key" in e for e in errors)


class TestTheRelevanceBlockIsValidated:
    """`repo_signals.yaml`'s `relevance` block.

    The silent failure these exist for is that every one of these mistakes is
    caught *after* something irreversible: an unpriced model name works all the
    way through the provider call and raises in `providers._cost` only once the
    call has been billed, and a `max_judged` below `releases_watch` silently
    watches fewer repositories than configured for ever. `validate.py` is not
    run by CI (`.github/workflows/tests.yml` runs pytest only), so these tests
    are the only thing standing behind the checks.
    """

    def _check(self, tmp_path, relevance):
        from validate import check_repo_signals

        doc = yaml.safe_load((ROOT / "config" / "repo_signals.yaml").read_text())
        if relevance is None:
            doc.pop("relevance", None)
        else:
            doc["relevance"] = {**doc["relevance"], **relevance}
        _write(tmp_path / "repo_signals.yaml", doc)
        return check_repo_signals(tmp_path)

    def test_the_committed_file_passes(self):
        from validate import check_repo_signals

        assert check_repo_signals(ROOT / "config") == []

    def test_an_unpriced_model_is_rejected(self, tmp_path):
        """The exact trap: `claude-haiku-4-5` is not a key in `providers.PRICES`
        — the dated `claude-haiku-4-5-20251001` is. The undated string survives
        the whole API call and raises while building the cost record, after the
        provider has billed it."""
        errors = self._check(tmp_path, {"model": "claude-haiku-4-5"})
        assert any("PRICES" in e for e in errors)

    def test_an_unknown_provider_is_rejected(self, tmp_path):
        errors = self._check(tmp_path, {"provider": "anthropik"})
        assert any("relevance.provider" in e for e in errors)

    def test_a_missing_prompt_file_is_rejected(self, tmp_path):
        errors = self._check(tmp_path, {"prompt_version": "r99"})
        assert any("r99.md is missing" in e for e in errors)

    def test_max_judged_below_releases_watch_is_rejected(self, tmp_path):
        """A cap under the watch count can never fill the list, so the leg
        quietly watches fewer repositories than it is configured to."""
        errors = self._check(tmp_path, {"max_judged": 3})
        assert any("can never fill" in e for e in errors)

    def test_a_missing_block_is_an_error_not_a_default(self, tmp_path):
        """Defaulting it to off would leave the releases leg watching what it
        watched before, with nothing saying why."""
        errors = self._check(tmp_path, None)
        assert any("relevance must be a mapping" in e for e in errors)

    def test_a_non_boolean_kill_switch_is_rejected(self, tmp_path):
        errors = self._check(tmp_path, {"enabled": "yes"})
        assert any("relevance.enabled" in e for e in errors)

    def test_validating_twice_does_not_grow_sys_path(self):
        """The insert is guarded. Unguarded, repeated validation in one process
        prepends the shim again each time and permanently shadows any
        same-named installed module — the trap already documented for the
        identical insert in `check_dedupe`."""
        from validate import check_repo_signals

        check_repo_signals(ROOT / "config")
        before = list(sys.path)
        check_repo_signals(ROOT / "config")
        assert sys.path == before
