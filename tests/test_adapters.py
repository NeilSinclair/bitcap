"""The adapters that absorb three legs' worth of differing call conventions.

These are the seam between the orchestrator and code it does not own, so the
silent failures live here: an argument quietly dropped, a return shape unpacked
wrongly, or the wrong URL recorded as the citation. None of those raise — they
produce a smaller or subtly wrong register that looks like real data.

Everything here is mocked. The adapters' behaviour against live sources is
proven separately by running them; what these pin down is the translation.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.pipeline import adapters
from app.pipeline.registry import Source

NOW = datetime(2026, 9, 3, 3, 0, tzinfo=timezone.utc)


def state_at(last_success):
    """A stand-in for the SourceState row the orchestrator passes in."""
    return SimpleNamespace(last_success_at=last_success)




def github_source(**overrides):
    config = {"org": "xai-org", "lab": "xai"}
    config.update(overrides)
    return Source(leg="github", id=config["org"], label=config["lab"], stage=3,
                  enabled=True, config=config)


def listed(name, pushed_at):
    """One entry as the REST repository listing returns it."""
    return {"name": name, "pushed_at": pushed_at,
            "stargazers_count": 1, "description": None}


def commit(login, at=None):
    at = at or datetime.now(timezone.utc) - timedelta(days=1)
    return {"date": at.isoformat().replace("+00:00", "Z"), "login": login,
            "name": login.title(), "email": f"{login}@x.ai"}


def exploding_history(*args, **kwargs):
    raise AssertionError("a repository was walked that should have come from bronze")


def store_repo(session, repo, pushed_at, commits, org="xai-org"):
    from app import models as m

    session.add(m.RawGithubRepo(
        org=org, repo=repo, pushed_at=pushed_at,
        payload={"total": len(commits), "commits": commits}, content_hash="h",
    ))
    session.flush()


def github_fakes(monkeypatch, listing, history=None):
    """Stand in for every network call the github adapter makes."""
    import harvest_github

    monkeypatch.setattr(harvest_github, "load_token", lambda: "token")
    monkeypatch.setattr(harvest_github, "repos", lambda org, token, since: listing)
    if history is not None:
        monkeypatch.setattr(harvest_github, "history", history)


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SASession

    from app.db import create_all

    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with SASession(engine) as s:
        yield s




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

        captured = {}
        github_fakes(monkeypatch, listing=[])
        monkeypatch.setattr(aggregate_github, "aggregate",
                            lambda *a, **kw: captured.update(args=a) or
                            {"people": [], "totals": {}})

        adapters.fetch_github(github_source())

        org, domain, work_suffix, domain_shared = captured["args"]
        assert org == "xai-org"
        assert work_suffix is not None
        import re
        assert re.compile(work_suffix) is not None  # a usable pattern, not None

    def test_people_are_tagged_with_their_org_and_lab(self, monkeypatch):
        """A lab can own two orgs, so the org must survive onto each person."""
        import aggregate_github

        github_fakes(monkeypatch, listing=[])
        monkeypatch.setattr(aggregate_github, "aggregate", lambda *a, **kw: {
            "people": [{"login": "someone", "commits": 4}],
            "totals": {"repos": 8, "people": 1},
        })

        result = adapters.fetch_github(github_source())
        assert result.items == [{"login": "someone", "commits": 4,
                                 "org": "xai-org", "lab": "xai"}]
        assert result.watermark == {"totals": {"repos": 8, "people": 1}}


class TestGithubIsIncremental:
    """The leg that used to re-walk twelve months of commits every firing.

    Its cache was on disk, which the deployed container does not have, so these
    are the tests that stand between a nightly run of minutes and one of tens of
    minutes — and the only ones that would catch it silently going back.
    """

    def test_an_unchanged_repo_is_not_walked_again(self, session, monkeypatch):
        """`pushed_at` has not moved, so there cannot be new commits."""
        store_repo(session, repo="grok", pushed_at="2026-08-01T00:00:00Z",
                   commits=[commit("ada")])

        github_fakes(monkeypatch, listing=[listed("grok", "2026-08-01T00:00:00Z")],
                     history=exploding_history)

        result = adapters.fetch_github(github_source(), session=session)

        assert result.raw_repos == [], "nothing new to persist"
        assert [p["login"] for p in result.items] == ["ada"], \
            "the stored history still has to reach the aggregate"

    def test_a_repo_that_was_pushed_to_is_walked_again(self, session, monkeypatch):
        store_repo(session, repo="grok", pushed_at="2026-08-01T00:00:00Z",
                   commits=[commit("ada")])

        walked = []
        github_fakes(
            monkeypatch,
            listing=[listed("grok", "2026-08-20T00:00:00Z")],
            history=lambda owner, name, token, since: walked.append(name) or
            {"total": 1, "commits": [commit("ada"), commit("bob")]},
        )

        result = adapters.fetch_github(github_source(), session=session)

        assert walked == ["grok"]
        assert sorted(p["login"] for p in result.items) == ["ada", "bob"]

    def test_a_walked_repo_comes_back_for_bronze(self, session, monkeypatch):
        """Without this the next run has nothing stored and re-walks everything."""
        github_fakes(monkeypatch, listing=[listed("grok", "2026-08-20T00:00:00Z")],
                     history=lambda *a: {"total": 1, "commits": [commit("ada")]})

        result = adapters.fetch_github(github_source(), session=session)

        assert len(result.raw_repos) == 1
        row = result.raw_repos[0]
        assert row["org"] == "xai-org" and row["repo"] == "grok"
        assert row["pushed_at"] == "2026-08-20T00:00:00Z"
        assert row["payload"]["commits"][0]["login"] == "ada"

    def test_stored_commits_that_have_aged_out_are_dropped(self, session, monkeypatch):
        """The drift this leg would otherwise accumulate one night at a time.

        A quiet repo is never re-walked, so its stored commits were fetched
        against an older, wider window. Counting them turns a 12-month register
        into an all-time one without anything reporting it.
        """
        old = commit("ada", at=datetime.now(timezone.utc) - timedelta(days=800))
        recent = commit("ada", at=datetime.now(timezone.utc) - timedelta(days=10))
        store_repo(session, repo="grok", pushed_at="2026-08-01T00:00:00Z",
                   commits=[old, recent])

        github_fakes(monkeypatch, listing=[listed("grok", "2026-08-01T00:00:00Z")],
                     history=exploding_history)

        result = adapters.fetch_github(github_source(), session=session)

        assert [p["commits"] for p in result.items] == [1], "the 800-day-old commit counted"

    def test_without_a_session_every_repo_is_walked(self, monkeypatch):
        """The CLI path, and the shape the tests above depend on being the default."""
        walked = []
        github_fakes(monkeypatch, listing=[listed("grok", "2026-08-20T00:00:00Z")],
                     history=lambda owner, name, token, since: walked.append(name) or
                     {"total": 0, "commits": []})

        adapters.fetch_github(github_source())
        assert walked == ["grok"]


class TestAnnouncementsAdapter:
    def test_an_unknown_method_raises_instead_of_exiting(self, monkeypatch):
        """`collect()` calls sys.exit here, which would kill the whole run."""
        source = Source(leg="announcements", id="newlab", label="New", stage=1, enabled=True,
                        config={"method": "carrier_pigeon", "window_months": 3})
        with pytest.raises(ValueError, match="unknown discovery method"):
            adapters.fetch_announcements(source)

    def test_the_watermark_is_the_newest_publication_date(self, monkeypatch):
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "sitemap", lambda lab, cutoff, skip=None: [
            {"url": "a", "date": "2026-08-01"}, {"url": "b", "date": "2026-08-27"},
        ])
        source = Source(leg="announcements", id="anthropic", label="A", stage=1, enabled=True,
                        config={"method": "sitemap", "window_months": 3})
        result = adapters.fetch_announcements(source)
        assert result.watermark == {"max_published": "2026-08-27"}

    def test_no_articles_yields_no_watermark_rather_than_a_null_one(self, monkeypatch):
        """An empty watermark leaves the stored one intact; a null would erase it."""
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "sitemap", lambda lab, cutoff, skip=None: [])
        source = Source(leg="announcements", id="anthropic", label="A", stage=1, enabled=True,
                        config={"method": "sitemap", "window_months": 3})
        assert adapters.fetch_announcements(source).watermark == {}


def _raise(message):
    """A discovery method that fails, for the channel-isolation tests."""
    def method(lab, cutoff, skip=None):
        raise RuntimeError(message)
    return method


class TestMultipleDiscoveryChannels:
    """The production path for a lab with more than one discovery channel.

    The silent degradation this exists to catch: someone simplifies the adapter
    back to a single `METHODS[lab["method"]]` call, OpenAI drops to one
    channel, the GPT-6 Astra blind spot returns, and every run still reports
    success. `channels()` being tested in isolation does not prove the pipeline
    runs them.
    """

    def _source(self, **extra):
        config = {
            "id": "openai", "method": "rss", "window_months": 3,
            "also": [{"method": "model_index"}],
            **extra,
        }
        return Source(leg="announcements", id="openai", label="OpenAI", stage=1,
                      enabled=True, config=config)

    def test_every_channel_runs_and_their_items_are_combined(self, monkeypatch):
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "rss", lambda lab, cutoff, skip=None: [
            {"url": "rss-1", "date": "2026-09-01"}])
        monkeypatch.setitem(fa.METHODS, "model_index", lambda lab, cutoff, skip=None: [
            {"url": "model-1", "date": "2026-09-04"}])

        result = adapters.fetch_announcements(self._source())

        assert sorted(a["url"] for a in result.items) == ["model-1", "rss-1"]

    def test_each_channel_is_given_the_same_settled_urls(self, monkeypatch):
        # A channel that does not receive `skip` re-downloads and re-classifies
        # everything it already has, which costs money rather than correctness
        # and so would not show up as a failure.
        import fetch_announcements as fa
        seen = {}

        def record(name):
            def method(lab, cutoff, skip=None):
                seen[name] = skip
                return []
            return method

        monkeypatch.setitem(fa.METHODS, "rss", record("rss"))
        monkeypatch.setitem(fa.METHODS, "model_index", record("model_index"))
        adapters.fetch_announcements(self._source())

        assert seen["rss"] == seen["model_index"]
        assert seen["rss"] is not None

    def test_one_broken_channel_does_not_discard_the_others_items(self, monkeypatch):
        # The regression that matters most: channels are redundancy, and an
        # unguarded loop made the lab strictly LESS available than before they
        # existed — a raising second channel threw away the first channel's
        # already-fetched articles and failed the whole source.
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "rss", lambda lab, cutoff, skip=None: [
            {"url": "rss-1", "date": "2026-09-01"}])

        def broken(lab, cutoff, skip=None):
            raise RuntimeError("model index parsed 0 models")

        monkeypatch.setitem(fa.METHODS, "model_index", broken)
        result = adapters.fetch_announcements(self._source())

        assert [a["url"] for a in result.items] == ["rss-1"]
        assert result.watermark == {"max_published": "2026-09-01"}

    def test_a_dead_channel_is_recorded_rather_than_swallowed(self, monkeypatch):
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "rss", lambda lab, cutoff, skip=None: [
            {"url": "rss-1", "date": "2026-09-01"}])
        monkeypatch.setitem(fa.METHODS, "model_index", _raise("page shape changed"))
        result = adapters.fetch_announcements(self._source())

        assert len(result.unresolved) == 1
        assert result.unresolved[0]["name"] == "openai:model_index"
        assert "page shape changed" in result.unresolved[0]["reason"]

    def test_all_channels_failing_still_fails_the_source(self, monkeypatch):
        # Isolation must not turn a genuinely dead lab into a silent success.
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "rss", _raise("rss down"))
        monkeypatch.setitem(fa.METHODS, "model_index", _raise("index down"))

        with pytest.raises(RuntimeError):
            adapters.fetch_announcements(self._source())

    def test_a_disabled_channel_is_not_run(self, monkeypatch):
        import fetch_announcements as fa
        ran = []

        monkeypatch.setitem(fa.METHODS, "rss", lambda lab, cutoff, skip=None: [])
        monkeypatch.setitem(
            fa.METHODS, "model_index",
            lambda lab, cutoff, skip=None: ran.append("model_index") or [])

        source = self._source(also=[{"method": "model_index", "enabled": False}])
        adapters.fetch_announcements(source)

        assert ran == []


class TestIncrementalWindow:
    """What a run fetches, and — the point of the whole thing — what it doesn't.

    The deployed cron has no disk, so every page in the window is a live fetch.
    A leg that re-derives three months nightly to find two new articles is the
    difference between a run of seconds and a run of half an hour.
    """

    def test_a_source_that_never_succeeded_fetches_the_full_window(self):
        """First firing on an empty database, and any newly-added lab."""
        assert adapters._window_start(state_at(None), 3, NOW) == adapters._cutoff(3, NOW)

    def test_no_state_at_all_fetches_the_full_window(self):
        """The adapters are callable without a state; that must not mean 48h."""
        assert adapters._window_start(None, 3, NOW) == adapters._cutoff(3, NOW)

    def test_a_recent_success_fetches_the_48_hour_floor_not_the_gap(self):
        """A run an hour after the last one still looks back two days.

        Labs backdate posts and correct dates after publishing. A window pulled
        tight to the last run would never see them.
        """
        start = adapters._window_start(state_at(NOW - timedelta(hours=1)), 3, NOW)
        assert start == NOW - timedelta(hours=adapters.INCREMENTAL_FLOOR_HOURS)

    def test_missed_nights_are_self_healing(self):
        """Five days of outage means the next run reaches back five days.

        A fixed 48h window would skip everything published in between and
        nothing would report it — the articles would simply never exist.
        """
        last = NOW - timedelta(days=5)
        assert adapters._window_start(state_at(last), 3, NOW) == last

    def test_the_window_is_never_wider_than_the_configured_one(self):
        """A source down for a year does not trigger a five-year backfill."""
        last = NOW - timedelta(days=400)
        assert adapters._window_start(state_at(last), 3, NOW) == adapters._cutoff(3, NOW)

    def test_a_naive_timestamp_is_read_as_utc(self):
        """sqlite returns naive datetimes, Postgres aware ones. Same answer."""
        naive = (NOW - timedelta(days=5)).replace(tzinfo=None)
        aware = NOW - timedelta(days=5)
        assert adapters._window_start(state_at(naive), 3, NOW) == \
            adapters._window_start(state_at(aware), 3, NOW)

    def test_the_announcements_adapter_narrows_what_it_asks_for(self, monkeypatch):
        """The regression that matters: a second run must not re-derive the window."""
        import fetch_announcements as fa

        seen = {}

        def method(lab, cutoff, skip=None):
            seen["cutoff"] = cutoff
            return []

        monkeypatch.setitem(fa.METHODS, "sitemap", method)
        source = Source(leg="announcements", id="anthropic", label="A", stage=1,
                        enabled=True, config={"method": "sitemap", "window_months": 3})

        adapters.fetch_announcements(source, state_at(None))
        first = seen["cutoff"]

        adapters.fetch_announcements(source, state_at(datetime.now(timezone.utc)))
        second = seen["cutoff"]

        assert second - first > timedelta(days=80), "second run still fetching the full window"


class TestPapersWindow:
    def test_a_first_run_gets_the_full_window(self):
        assert adapters._window_months(state_at(None), NOW) == adapters.PAPERS_WINDOW_MONTHS

    def test_a_recent_success_gets_the_one_month_floor(self):
        assert adapters._window_months(state_at(NOW - timedelta(hours=1)), NOW) == 1

    def test_a_long_gap_is_rounded_up_but_capped(self):
        assert adapters._window_months(state_at(NOW - timedelta(days=40)), NOW) == 2
        assert adapters._window_months(state_at(NOW - timedelta(days=400)), NOW) == 3

    def test_the_window_is_a_whole_number(self):
        """`harvest_contributors` does `today.month - months` and formats `{y:04d}`.

        A float reaches that as a float month and raises inside the harvester,
        which the orchestrator would record as the source being down.
        """
        for last in (None, NOW - timedelta(hours=1), NOW - timedelta(days=40)):
            months = adapters._window_months(state_at(last), NOW)
            assert isinstance(months, int) and not isinstance(months, bool)

    def test_the_papers_adapter_passes_the_narrowed_window(self, monkeypatch):
        seen = {}

        def collect(**kwargs):
            seen.update(kwargs)
            return ([], [])

        monkeypatch.setattr(adapters.importlib, "import_module",
                            lambda name: type("M", (), {"collect": staticmethod(collect)}))
        adapters.fetch_papers(paper_source(), state_at(datetime.now(timezone.utc)))
        assert seen["months"] == 1


class TestArticlesAreNotDownloadedTwice:
    """The check that replaces the on-disk page cache.

    A page already stored has nothing to give by being downloaded again — its
    text is in `raw_articles` and everything downstream reads it from there. Not
    asking for it is strictly better than caching it: no request, no bytes, no
    rate limit, and nothing to lose when the container is thrown away.
    """

    def store(self, session, url, classified_as=None):
        from app import models as m

        session.add(m.RawArticle(url=url, payload={"url": url}, content_hash="h",
                                 source_file="announcements.json"))
        if classified_as:
            session.add(m.RawLlmResponse(url=url, prompt_version=classified_as,
                                            payload={}))
        session.flush()

    def test_a_stored_and_classified_article_is_skipped(self, session):
        self.store(session, "https://x.test/a", classified_as=adapters.PROMPT_VERSION)
        assert adapters._settled_urls(session) == {"https://x.test/a"}

    def test_a_stored_but_unclassified_article_is_still_fetched(self, session):
        """The subtle one, and the reason this is not just "is it in the table".

        `classify_new` reads article *text* from the corpus file, not from the
        database. On a container with no disk that file is the image copy every
        firing, so an article skipped here before it was classified would stay
        pending for ever with nothing able to classify it (planning.md §13.3).
        """
        self.store(session, "https://x.test/pending")
        assert adapters._settled_urls(session) == set()

    def test_a_classification_from_another_prompt_version_does_not_count(self, session):
        """A new prompt version has to see the text again to score it."""
        self.store(session, "https://x.test/a", classified_as="v6")
        assert adapters._settled_urls(session, "v7") == set()

    def test_without_a_session_nothing_is_skipped(self):
        """A manual sweep still refetches everything."""
        assert adapters._settled_urls(None) == set()

    def test_the_adapter_hands_the_skip_set_to_the_discovery_method(self, session, monkeypatch):
        import fetch_announcements as fa

        self.store(session, "https://x.test/a", classified_as=adapters.PROMPT_VERSION)
        seen = {}
        monkeypatch.setitem(fa.METHODS, "sitemap",
                            lambda lab, cutoff, skip=None: seen.update(skip=skip) or [])

        source = Source(leg="announcements", id="anthropic", label="A", stage=1,
                        enabled=True, config={"method": "sitemap", "window_months": 3})
        adapters.fetch_announcements(source, session=session)

        assert seen["skip"] == {"https://x.test/a"}

    def test_no_request_is_made_for_a_skipped_url(self, monkeypatch):
        """End to end through the real sitemap method: the page is never asked for."""
        import fetch_announcements as fa

        lab = {"id": "deepseek", "index_url": "https://x.test/sitemap.xml",
               "url_contains": "/news", "date_from": "slug",
               "text_source": "full_text", "window_months": 3}
        sitemap = (
            "<url><loc>https://x.test/news260830</loc></url>"
            "<url><loc>https://x.test/news260831</loc></url>"
        )
        requested = []

        def fake_fetch(url, *a, **kw):
            requested.append(url)
            return sitemap if url == lab["index_url"] else "<html>body text</html>"

        monkeypatch.setattr(fa, "fetch", fake_fetch)

        fa.from_sitemap(lab, datetime(2026, 8, 1, tzinfo=timezone.utc),
                        skip={"https://x.test/news260830"})

        assert "https://x.test/news260830" not in requested, "skipped page was downloaded"
        assert "https://x.test/news260831" in requested, "new page was not downloaded"
