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
