"""The adapters that absorb three legs' worth of differing call conventions.

These are the seam between the orchestrator and code it does not own, so the
silent failures live here: an argument quietly dropped, a return shape unpacked
wrongly, or the wrong URL recorded as the citation. None of those raise — they
produce a smaller or subtly wrong register that looks like real data.

Everything here is mocked. The adapters' behaviour against live sources is
proven separately by running them; what these pin down is the translation.
"""

import pytest

from app.pipeline import adapters
from app.pipeline.registry import Source


def paper_source(**overrides):
    config = {
        "lab": "meta-ai", "module": "fake_harvest", "entry": "collect",
        "args": ["months", "model", "limit"], "returns": "papers_and_unresolved",
        "url_field": "meta_url", "enabled": True,
    }
    config.update(overrides)
    return Source(leg="papers", id=config["lab"], label=config["lab"], stage=2,
                  enabled=config["enabled"], config=config)


class TestPapersArgumentHandling:
    def test_only_declared_arguments_are_passed(self, monkeypatch):
        """OpenAI's harvester takes no window argument; passing one is a TypeError."""
        seen = {}

        def collect(**kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr(adapters.importlib, "import_module",
                            lambda name: type("M", (), {"collect": staticmethod(collect)}))
        adapters.fetch_papers(paper_source(args=[], returns="papers", url_field="url"))
        assert seen == {}

    def test_declared_arguments_are_passed(self, monkeypatch):
        seen = {}

        def collect(**kwargs):
            seen.update(kwargs)
            return ([], [])

        monkeypatch.setattr(adapters.importlib, "import_module",
                            lambda name: type("M", (), {"collect": staticmethod(collect)}))
        adapters.fetch_papers(paper_source())
        assert set(seen) == {"months", "model", "limit"}

    def test_the_pair_return_shape_is_unpacked(self, monkeypatch):
        """Three harvesters return (papers, unresolved); three return a bare list."""
        monkeypatch.setattr(adapters.importlib, "import_module", lambda name: type("M", (), {
            "collect": staticmethod(lambda **kw: (
                [{"meta_url": "https://ai.meta.com/p", "title": "T", "date": "2026-08-01"}],
                [{"title": "unfindable", "reason": "no arxiv match"}],
            ))
        }))
        result = adapters.fetch_papers(paper_source())
        assert len(result.items) == 1
        assert result.unresolved == [{"title": "unfindable", "reason": "no arxiv match",
                                      "lab": "meta-ai"}]

    def test_the_bare_list_return_shape_is_handled(self, monkeypatch):
        monkeypatch.setattr(adapters.importlib, "import_module", lambda name: type("M", (), {
            "collect": staticmethod(lambda **kw: [{"url": "https://x/p", "title": "T",
                                                   "date": "2026-08-01"}])
        }))
        result = adapters.fetch_papers(paper_source(args=[], returns="papers", url_field="url"))
        assert len(result.items) == 1
        assert result.unresolved == []

    def test_unresolved_candidates_are_never_dropped(self, monkeypatch):
        """No citation, no insight — but a gap is recorded, not silently omitted."""
        monkeypatch.setattr(adapters.importlib, "import_module", lambda name: type("M", (), {
            "collect": staticmethod(lambda **kw: ([], [{"title": "x", "reason": "ambiguous"}]))
        }))
        result = adapters.fetch_papers(paper_source())
        assert result.items == []
        assert result.unresolved[0]["reason"] == "ambiguous"


class TestPapersCitation:
    @pytest.mark.parametrize("url_field,raw,expected", [
        ("meta_url",
         {"meta_url": "https://ai.meta.com/research/p", "source_url": "https://arxiv.org/abs/1"},
         "https://ai.meta.com/research/p"),
        ("announcement_url",
         {"announcement_url": "https://mistral.ai/news/p", "source_url": "https://arxiv.org/abs/2"},
         "https://mistral.ai/news/p"),
        ("url",
         {"url": "https://deepmind.google/p", "source_url": "https://arxiv.org/abs/3"},
         "https://deepmind.google/p"),
    ])
    def test_the_labs_own_page_is_the_citation_not_the_scraped_page(
        self, monkeypatch, url_field, raw, expected
    ):
        """`source_url` is where the byline was scraped from — our plumbing.

        Citing it instead of the lab's own page would point a reader at an arXiv
        record chosen by a title-similarity heuristic rather than at the thing
        the lab actually published.
        """
        monkeypatch.setattr(adapters.importlib, "import_module", lambda name: type("M", (), {
            "collect": staticmethod(lambda **kw: [{**raw, "title": "T", "date": "2026-08-01"}])
        }))
        result = adapters.fetch_papers(
            paper_source(returns="papers", url_field=url_field, args=[])
        )
        assert result.items[0]["url"] == expected
        assert result.items[0]["raw"]["source_url"].startswith("https://arxiv.org/")

    def test_lab_specific_fields_survive_in_raw(self, monkeypatch):
        """Per-lab idiosyncrasy goes in one blob, not a column per lab."""
        monkeypatch.setattr(adapters.importlib, "import_module", lambda name: type("M", (), {
            "collect": staticmethod(lambda **kw: [{"url": "u", "title": "T", "date": "d",
                                                   "resolution": "relaxed_title",
                                                   "order_meaningful": False}])
        }))
        result = adapters.fetch_papers(paper_source(returns="papers", url_field="url", args=[]))
        assert result.items[0]["raw"]["resolution"] == "relaxed_title"
        assert result.items[0]["raw"]["order_meaningful"] is False

    def test_a_disabled_lab_raises_rather_than_returning_empty(self):
        """An empty result would look like 'this lab published nothing'."""
        with pytest.raises(RuntimeError, match="no papers harvester"):
            adapters.fetch_papers(paper_source(lab="xai", enabled=False, module=None))


class TestGithubAdapter:
    def test_aggregation_arguments_come_from_load_labs(self, monkeypatch):
        """Regression: a null `work_suffix` must become a never-matching regex.

        Passing the raw YAML value straight through gave `None` to `re`, which
        raised for every org without a handle convention. `load_labs()` exists
        precisely to make that substitution, and the adapter must go through it
        rather than rebuilding the mapping itself.
        """
        import aggregate_github
        import harvest_github

        captured = {}
        monkeypatch.setattr(harvest_github, "collect", lambda org, months=12: {})
        monkeypatch.setattr(aggregate_github, "aggregate",
                            lambda *a: captured.update(args=a) or {"people": [], "totals": {}})

        source = Source(leg="github", id="xai-org", label="xai", stage=3, enabled=True,
                        config={"org": "xai-org", "lab": "xai"})
        adapters.fetch_github(source)

        org, domain, work_suffix, domain_shared = captured["args"]
        assert org == "xai-org"
        assert work_suffix is not None
        import re
        assert re.compile(work_suffix) is not None  # a usable pattern, not None

    def test_people_are_tagged_with_their_org_and_lab(self, monkeypatch):
        """A lab can own two orgs, so the org must survive onto each person."""
        import aggregate_github
        import harvest_github

        monkeypatch.setattr(harvest_github, "collect", lambda org, months=12: {})
        monkeypatch.setattr(aggregate_github, "aggregate", lambda *a: {
            "people": [{"login": "someone", "commits": 4}],
            "totals": {"repos": 8, "people": 1},
        })

        source = Source(leg="github", id="meta-llama", label="meta", stage=3, enabled=True,
                        config={"org": "meta-llama", "lab": "meta-ai"})
        result = adapters.fetch_github(source)

        assert result.items[0]["org"] == "meta-llama"
        assert result.items[0]["lab"] == "meta-ai"
        assert result.watermark["totals"]["repos"] == 8


class TestAnnouncementsAdapter:
    def test_an_unknown_method_raises_instead_of_exiting(self, monkeypatch):
        """`collect()` calls sys.exit here, which would kill the whole run."""
        source = Source(leg="announcements", id="newlab", label="New", stage=1, enabled=True,
                        config={"method": "carrier_pigeon", "window_months": 3})
        with pytest.raises(ValueError, match="unknown discovery method"):
            adapters.fetch_announcements(source)

    def test_the_watermark_is_the_newest_publication_date(self, monkeypatch):
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "sitemap", lambda lab, cutoff: [
            {"url": "a", "date": "2026-08-01"}, {"url": "b", "date": "2026-08-27"},
        ])
        source = Source(leg="announcements", id="anthropic", label="A", stage=1, enabled=True,
                        config={"method": "sitemap", "window_months": 3})
        result = adapters.fetch_announcements(source)
        assert result.watermark == {"max_published": "2026-08-27"}

    def test_no_articles_yields_no_watermark_rather_than_a_null_one(self, monkeypatch):
        """An empty watermark leaves the stored one intact; a null would erase it."""
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "sitemap", lambda lab, cutoff: [])
        source = Source(leg="announcements", id="anthropic", label="A", stage=1, enabled=True,
                        config={"method": "sitemap", "window_months": 3})
        assert adapters.fetch_announcements(source).watermark == {}
