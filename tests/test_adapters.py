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

# The stats dict `new_releases` really returns. Faking a subset of it is how
# a field the adapter newly reads becomes a KeyError in production that no
# test saw.
STATS = {"seen": 0, "kept": 0, "truncated": 0, "empty": 0,
         "drafts": 0, "reached_cursor": True}

import pytest
from sqlalchemy import select

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


class TestReleasesAdapter:
    """The releases leg's seam.

    Three things it must get right, none of which fails loudly: the ranking it
    is gated on, the cursor it carries between firings, and the isolation
    between repositories inside one org.
    """

    @pytest.fixture(autouse=True)
    def no_live_relevance_calls(self, monkeypatch):
        """Answer the relevance gate locally for every test in this class.

        `fetch_releases` loads the real `config/repo_signals.yaml`, where the
        gate is enabled, so without this the suite reaches
        `providers.classify` — measured at 20 attempted calls. They failed for
        want of a key and the gate failed open, so every test still passed
        green while making network calls, and would have spent real money on a
        machine with `ANTHROPIC_API_KEY` set.

        Keeping everything is what the tests below assume, so this preserves
        their meaning; the gate's own behaviour is tested in `TestRepoRelevanceGate`
        with an explicit stub.
        """
        from app.pipeline import repo_relevance

        monkeypatch.setattr(
            repo_relevance, "judge",
            lambda session, row, config: ({"relevant": True, "reason": "stub"}, 0.0, None))

    def release_source(self, org="xai-org", lab="xai"):
        return Source(leg="releases", id=org, label=org, stage=4, enabled=True,
                      config={"org": org, "lab": lab})

    def stored(self, session, repo, stars=10, commits=None, org="xai-org"):
        from app import models as m

        session.add(m.RawGithubRepo(
            org=org, repo=repo, pushed_at="2026-08-01T00:00:00Z",
            payload={"total": 0, "commits": commits or [], "stars": stars},
            content_hash="h"))
        session.flush()

    def in_corpus(self, session, repo, org="xai-org"):
        """Put one release for `repo` in bronze.

        A cursor asserts that documents up to it are stored, so any test whose
        premise is "we have a cursor from a previous run" needs the rows that
        run produced -- otherwise it is describing the corrupt state the
        stale-cursor guard exists to recover from, not a healthy one.
        """
        from app import models as m
        from app.pipeline.registry import CORPUS_LABELS, RELEASES

        session.add(m.RawArticle(
            url=f"https://github.com/{org}/{repo}/releases/tag/v0",
            payload={"org": org, "repo": repo}, content_hash="h",
            source_file=CORPUS_LABELS[RELEASES]))
        session.flush()

    def listing(self, monkeypatch, entries):
        """Stand in for the live REST listing the leg overlays onto bronze."""
        import harvest_github

        monkeypatch.setattr(harvest_github, "load_token", lambda: "token")
        monkeypatch.setattr(harvest_github, "repos",
                            lambda org, token, since: entries)

    def listed_repo(self, name, stars, created_at="2020-01-01T00:00:00Z"):
        return {"name": name, "pushed_at": "2026-08-01T00:00:00Z",
                "stargazers_count": stars, "description": None,
                "created_at": created_at, "language": "Python",
                "topics": [], "archived": False}

    def released(self, monkeypatch, fn):
        import fetch_releases as fr

        monkeypatch.setattr(fr, "new_releases", fn)

    def item(self, repo, published="2026-09-03T00:00:00Z", lab="xai"):
        return {"url": f"https://github.com/xai-org/{repo}/releases/tag/v1",
                "lab": lab, "date": published[:10], "tag": "v1",
                "published_at": published}

    def test_an_empty_bronze_raises_rather_than_reporting_no_releases(
            self, session, monkeypatch):
        """Nothing downstream can tell "the github leg has not run" from "this
        org ships nothing" -- both are an empty leg."""
        self.listing(monkeypatch, [])
        with pytest.raises(RuntimeError, match="raw_github_repos"):
            adapters.fetch_releases(self.release_source(), session=session)

    def test_the_watch_list_is_ranked_by_live_stars_not_bronze(self, session,
                                                               monkeypatch):
        """Bronze's star count is only as fresh as the last history walk, and
        the github leg runs at cadence 3. A repository that stops being
        committed to would rank for ever on a frozen number."""
        self.stored(session, "quiet", stars=1)
        self.stored(session, "loud", stars=999)
        self.listing(monkeypatch, [self.listed_repo("quiet", 500_000),
                                   self.listed_repo("loud", 2)])
        seen = []
        self.released(monkeypatch,
                      lambda org, repo, *a: seen.append(repo) or ([], STATS))

        adapters.fetch_releases(self.release_source(), session=session)
        assert seen == ["quiet", "loud"]

    def test_a_repo_absent_from_the_live_listing_is_not_watched(self, session,
                                                                monkeypatch):
        """Deleted, renamed, or pushed outside the window: bronze still holds
        the row, and fetching releases for it would 404 every firing."""
        self.stored(session, "gone", stars=999)
        self.stored(session, "here", stars=1)
        self.listing(monkeypatch, [self.listed_repo("here", 1)])
        seen = []
        self.released(monkeypatch,
                      lambda org, repo, *a: seen.append(repo) or ([], STATS))

        adapters.fetch_releases(self.release_source(), session=session)
        assert seen == ["here"]

    def test_an_empty_listing_against_non_empty_bronze_fails_the_source(
            self, session, monkeypatch):
        """The API failing to answer and an org going quiet produce the same
        empty result. Bronze knowing about repositories is what separates
        them, and a failed source is recorded and escalated where a quiet week
        is not."""
        self.stored(session, "grok", stars=10)
        self.listing(monkeypatch, [])
        with pytest.raises(RuntimeError, match="listing came back empty"):
            adapters.fetch_releases(self.release_source(), session=session)

    def test_the_cursor_comes_from_the_watermark(self, session, monkeypatch):
        self.stored(session, "grok", stars=10)
        self.in_corpus(session, "grok")
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        seen = {}

        def fake(org, repo, token, lab, cursor, *a):
            seen["cursor"] = cursor
            return [], STATS

        self.released(monkeypatch, fake)
        state = SimpleNamespace(watermark={"cursors": {"grok": "2026-08-01T00:00:00Z"}})
        adapters.fetch_releases(self.release_source(), state=state, session=session)
        assert seen["cursor"] == "2026-08-01T00:00:00Z"

    def test_the_advanced_cursor_comes_back_as_the_watermark(self, session,
                                                             monkeypatch):
        """The orchestrator persists this. On a container with no disk it is
        the only thing that stops every firing re-fetching the same backfill
        for ever while looking healthy."""
        self.stored(session, "grok", stars=10)
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        self.released(monkeypatch,
                      lambda *a: ([self.item("grok")], STATS))

        result = adapters.fetch_releases(self.release_source(), session=session)
        assert result.watermark["cursors"]["grok"] == "2026-09-03T00:00:00Z"

    def test_an_untouched_repo_keeps_its_existing_cursor(self, session,
                                                         monkeypatch):
        """The watermark replaces the stored one wholesale, so a repository
        with nothing new this run must still appear in it or its cursor is
        lost and it re-backfills."""
        self.stored(session, "a", stars=20)
        self.stored(session, "b", stars=10)
        self.in_corpus(session, "b")
        self.listing(monkeypatch, [self.listed_repo("a", 20),
                                   self.listed_repo("b", 10)])
        self.released(monkeypatch, lambda org, repo, token, lab, cursor, *a: (
            ([self.item("a")], STATS) if repo == "a"
            else ([], STATS)))

        state = SimpleNamespace(watermark={"cursors": {"b": "2026-01-01T00:00:00Z"}})
        result = adapters.fetch_releases(self.release_source(), state=state,
                                         session=session)
        assert result.watermark["cursors"]["b"] == "2026-01-01T00:00:00Z"

    def test_one_dead_repo_does_not_take_out_the_org(self, session, monkeypatch):
        """A repository renamed since bronze last saw it raises a 404 that
        `_call` does not retry. Letting it propagate discards every release
        already fetched, advances no cursor, and repeats every firing."""
        self.stored(session, "dead", stars=20)
        self.stored(session, "live", stars=10)
        self.listing(monkeypatch, [self.listed_repo("dead", 20),
                                   self.listed_repo("live", 10)])

        def fake(org, repo, token, lab, *a):
            if repo == "dead":
                raise RuntimeError("HTTP Error 404: Not Found")
            return [self.item("live")], STATS

        self.released(monkeypatch, fake)
        result = adapters.fetch_releases(self.release_source(), session=session)
        assert len(result.items) == 1
        assert any("404" in f for f in result.watermark["repo_failures"])

    def test_items_carry_the_lab_id_not_the_org_login(self, session, monkeypatch):
        self.stored(session, "grok", stars=10, org="anthropics")
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        self.released(monkeypatch, lambda org, repo, token, lab, *a: (
            [self.item("grok", lab=lab)], STATS))

        result = adapters.fetch_releases(
            self.release_source("anthropics", "anthropic"), session=session)
        assert result.items[0]["lab"] == "anthropic"

    def test_truncation_is_surfaced_on_the_watermark(self, session, monkeypatch):
        """A cap that drops documents quietly reads as full coverage; the
        watermark is where the ops view can see it."""
        self.stored(session, "grok", stars=10)
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        self.released(monkeypatch, lambda *a: ([], {**STATS, "truncated": 7}))

        result = adapters.fetch_releases(self.release_source(), session=session)
        assert result.watermark["truncated"] == 7

    def test_the_relevance_report_reaches_the_watermark(self, session, monkeypatch):
        """The seam nothing else crosses.

        `TestRepoRelevanceGate` calls `_relevant_slice` directly and asserts on
        what it returns; `TestRepoFilterUnavailable` builds the watermark by
        hand. Between them, deleting the `watermark["relevance"] = relevance`
        assignment left the whole suite green — and in production that means the
        filter runs, spends money, and `repo_filter_unavailable` can never fire,
        because its only input is the key that no longer exists. Both silent.
        """
        from app.pipeline import repo_relevance

        monkeypatch.setattr(repo_relevance, "judge", lambda session, row, config: (
            {"relevant": row["repo"] != "mujoco", "reason": "a physics simulator"},
            0.0007, None))
        self.stored(session, "grok", stars=20)
        self.stored(session, "mujoco", stars=30)
        self.listing(monkeypatch, [self.listed_repo("grok", 20),
                                   self.listed_repo("mujoco", 30)])
        self.released(monkeypatch, lambda *a: ([self.item("grok")], STATS))

        result = adapters.fetch_releases(self.release_source(), session=session)
        report = result.watermark["relevance"]

        assert [e["repo"] for e in report["excluded"]] == ["mujoco"]
        assert report["judged"] == 2
        assert report["errors"] == 0

    def test_the_filter_spend_is_not_reported_twice(self, session, monkeypatch):
        """`repo_relevance` records its own cost, with tokens, into the shared
        log — which `load_costs` puts in `raw_costs`. `budget.month_to_date`
        sums `raw_costs` *plus* `run_sources.cost_usd` on the stated assumption
        that they never overlap, so returning it here as well would
        double-count the filter against the monthly ceiling."""
        from app.pipeline import repo_relevance

        monkeypatch.setattr(repo_relevance, "judge", lambda session, row, config: (
            {"relevant": True, "reason": "r"}, 0.0007, None))
        self.stored(session, "grok", stars=10)
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        self.released(monkeypatch, lambda *a: ([self.item("grok")], STATS))

        result = adapters.fetch_releases(self.release_source(), session=session)

        assert result.cost_usd == 0.0

    def test_nothing_is_written_to_disk(self, session, monkeypatch, tmp_path):
        """The whole reason for the port: the deployed container has no disk,
        and state kept there resets to the image copy every firing."""
        self.stored(session, "grok", stars=10)
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        self.released(monkeypatch,
                      lambda *a: ([self.item("grok")], STATS))

        before = set(adapters.DOCS.glob("release*"))
        adapters.fetch_releases(self.release_source(), session=session)
        assert set(adapters.DOCS.glob("release*")) == before

    def test_the_documents_land_in_the_same_call_that_advances_the_cursor(
            self, session, monkeypatch):
        """The cursor gates every future fetch and `run_source` commits it as
        soon as the adapter returns. Landing the rows a phase later means a
        failure in between -- the register load, a redeploy, an OOM kill during
        a 9-30 minute firing -- rolls the rows back while the cursor stays
        advanced, and those releases are filtered out as already-seen for ever.
        """
        from app import models as m

        self.stored(session, "grok", stars=10)
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        self.released(monkeypatch,
                      lambda *a: ([self.item("grok")], STATS))

        result = adapters.fetch_releases(self.release_source(), session=session)

        landed = session.scalars(select(m.RawArticle)).all()
        assert [r.url for r in landed] == [result.items[0]["url"]]
        assert landed[0].source_file == "github_releases"

    def test_relanding_the_same_release_does_not_duplicate_it(self, session,
                                                              monkeypatch):
        """A firing that landed rows and then failed leaves the cursor alone,
        so the next one refetches. The url-keyed upsert has to absorb that."""
        from app import models as m

        self.stored(session, "grok", stars=10)
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        self.released(monkeypatch, lambda *a: ([self.item("grok")], STATS))

        adapters.fetch_releases(self.release_source(), session=session)
        adapters.fetch_releases(self.release_source(), session=session)

        assert len(session.scalars(select(m.RawArticle)).all()) == 1

    def test_every_repo_failing_fails_the_source(self, session, monkeypatch):
        """A token rotated to one without the right scope 404s on all of them.
        Reported as a success it would reset `consecutive_failures` to zero
        every firing, so `source_down` could never fire and the leg would stay
        dead behind a green row."""
        self.stored(session, "a", stars=20)
        self.stored(session, "b", stars=10)
        self.listing(monkeypatch, [self.listed_repo("a", 20),
                                   self.listed_repo("b", 10)])
        self.released(monkeypatch, lambda *a: (_ for _ in ()).throw(
            RuntimeError("HTTP Error 404: Not Found")))

        with pytest.raises(RuntimeError, match="all 2 watched repositories failed"):
            adapters.fetch_releases(self.release_source(), session=session)

    def test_some_repos_failing_does_not_fail_the_source(self, session,
                                                         monkeypatch):
        self.stored(session, "dead", stars=20)
        self.stored(session, "live", stars=10)
        self.listing(monkeypatch, [self.listed_repo("dead", 20),
                                   self.listed_repo("live", 10)])

        def fake(org, repo, token, lab, *a):
            if repo == "dead":
                raise RuntimeError("404")
            return [self.item("live")], STATS

        self.released(monkeypatch, fake)
        result = adapters.fetch_releases(self.release_source(), session=session)
        assert len(result.items) == 1

    def test_an_unreached_cursor_reaches_the_watermark(self, session,
                                                       monkeypatch):
        """A walk that never found the cursor left releases above it unfetched
        and uncounted -- `truncated` only counts what this call saw and
        dropped. Discarding the flag made that indistinguishable from a clean
        run."""
        self.stored(session, "grok", stars=10)
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        self.released(monkeypatch,
                      lambda *a: ([], {**STATS, "reached_cursor": False}))

        result = adapters.fetch_releases(self.release_source(), session=session)
        assert result.watermark["reached_cursor"] is False

    # --- stale cursors ----------------------------------------------------
    #
    # `source_state` is in `models.OPS_TABLES` and survives `drop_all`; the
    # release rows it vouches for live in `raw_articles`, which does not. A
    # rebuild therefore leaves every cursor intact against an empty corpus, and
    # this leg then reports itself caught up for ever. The outage is permanent
    # and completely silent, because "no new releases" is exactly what a quiet
    # week looks like. Measured on the live database 2026-09-06: 87 cursors
    # dated 2026-09-03/04 against 0 release rows.
    #
    # D67 fixed the sibling case by moving `raw_github_repos` into OPS_TABLES.
    # That is unavailable here: these are articles, and the articles table
    # cannot be exempt from a rebuild.

    def test_cursors_against_an_empty_corpus_trigger_a_backfill(
            self, session, monkeypatch):
        """The outage itself: a cursor claiming documents that no longer exist."""
        self.stored(session, "grok", stars=10)
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        seen = {}

        def fake(org, repo, token, lab, cursor, *a):
            seen["cursor"] = cursor
            return [], STATS

        self.released(monkeypatch, fake)
        state = SimpleNamespace(watermark={"cursors": {"grok": "2026-09-03T00:00:00Z"}})
        result = adapters.fetch_releases(self.release_source(), state=state,
                                         session=session)
        assert seen["cursor"] is None, "a cursor with no corpus behind it must not bind"
        assert result.watermark["stale_cursors"] == 1

    def test_a_healthy_run_keeps_its_cursors(self, session, monkeypatch):
        """The expensive false positive: re-backfilling every firing because
        the guard reads the wrong signal."""
        self.stored(session, "grok", stars=10)
        self.in_corpus(session, "grok")
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        seen = {}

        def fake(org, repo, token, lab, cursor, *a):
            seen["cursor"] = cursor
            return [], STATS

        self.released(monkeypatch, fake)
        state = SimpleNamespace(watermark={"cursors": {"grok": "2026-09-03T00:00:00Z"}})
        result = adapters.fetch_releases(self.release_source(), state=state,
                                         session=session)
        assert seen["cursor"] == "2026-09-03T00:00:00Z"
        assert "stale_cursors" not in result.watermark

    def test_a_partially_missing_corpus_is_not_treated_as_stale(
            self, session, monkeypatch):
        """Narrowness. Losing *some* rows is a different fault, and silently
        re-backfilling would hide it rather than surface it."""
        self.stored(session, "grok", stars=20)
        self.stored(session, "grok-prompts", stars=10)
        self.in_corpus(session, "grok")           # one present, one missing
        self.listing(monkeypatch, [self.listed_repo("grok", 20),
                                   self.listed_repo("grok-prompts", 10)])
        seen = {}

        def fake(org, repo, token, lab, cursor, *a):
            seen[repo] = cursor
            return [], STATS

        self.released(monkeypatch, fake)
        state = SimpleNamespace(watermark={
            "cursors": {"grok": "2026-09-03T00:00:00Z",
                        "grok-prompts": "2026-09-02T00:00:00Z"}})
        result = adapters.fetch_releases(self.release_source(), state=state,
                                         session=session)
        assert seen["grok-prompts"] == "2026-09-02T00:00:00Z"
        assert "stale_cursors" not in result.watermark

    def test_a_new_org_never_reaches_the_guard(self, session, monkeypatch):
        """No cursors and no corpus is a first run, not a recovery, and must
        not be reported as one."""
        self.stored(session, "grok", stars=10)
        self.listing(monkeypatch, [self.listed_repo("grok", 10)])
        self.released(monkeypatch, lambda *a: ([], STATS))

        result = adapters.fetch_releases(self.release_source(), session=session)
        assert "stale_cursors" not in result.watermark

    def test_the_recovery_is_counted_so_an_operator_can_see_it(
            self, session, monkeypatch):
        """A large backfill is indistinguishable from a busy week in the item
        count alone. The operator has to be told the leg recovered."""
        self.stored(session, "grok", stars=20)
        self.stored(session, "grok-prompts", stars=10)
        self.listing(monkeypatch, [self.listed_repo("grok", 20),
                                   self.listed_repo("grok-prompts", 10)])
        self.released(monkeypatch, lambda *a: ([], STATS))

        state = SimpleNamespace(watermark={
            "cursors": {"grok": "2026-09-03T00:00:00Z",
                        "grok-prompts": "2026-09-02T00:00:00Z"}})
        result = adapters.fetch_releases(self.release_source(), state=state,
                                         session=session)
        assert result.watermark["stale_cursors"] == 2


class TestTheBackfillRunsInThePipeline:
    """The path that actually runs nightly, which is the one that never ran it.

    `backfill_openai.py` was a script a person ran by hand against a file. The
    deployed pipeline does not run scripts and has no file: it calls the
    discovery methods directly and writes to `raw_articles`. So recovery was
    absent from production entirely, and OpenAI reached the classifier as
    ~200-character feed blurbs every single night while the corpus file --
    briefly, once -- looked fine.

    `tests/test_announcements.py` covers `enrich_wayback` itself. These cover
    the wiring, because the wiring is what was missing.
    """

    def _source(self, **extra):
        config = {
            "id": "openai", "method": "rss", "window_months": 3,
            "backfill": "wayback", "text_source": "rss_summary",
            **extra,
        }
        return Source(leg="announcements", id="openai", label="OpenAI", stage=1,
                      enabled=True, config=config)

    def _summary(self, url="https://openai.com/index/x"):
        return {"lab": "openai", "url": url, "date": "2026-09-01",
                "title": "X", "text": "X. A one-sentence summary.",
                "text_source": "rss_summary"}

    def test_recovered_text_reaches_the_items_the_pipeline_stores(self, monkeypatch):
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "rss",
                            lambda lab, cutoff, skip=None: [self._summary()])

        def fake_enrich(lab, articles, cutoff):
            for a in articles:
                a["text"] = "the recovered article body " * 200
                a["text_source"] = "full_text_archived"
                a["archive_snapshot"] = "http://web.archive.org/web/2026id_/x"
            return []

        monkeypatch.setattr(fa, "enrich_wayback", fake_enrich)

        result = adapters.fetch_announcements(self._source())

        assert [a["text_source"] for a in result.items] == ["full_text_archived"]
        assert len(result.items[0]["text"]) > 3000

    def test_a_lab_without_the_key_is_not_walked(self, monkeypatch):
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "rss",
                            lambda lab, cutoff, skip=None: [self._summary()])
        called = []
        monkeypatch.setattr(fa, "enrich_wayback",
                            lambda *a, **k: called.append(1) or [])

        adapters.fetch_announcements(self._source(backfill=None))

        assert called == []

    def test_an_unrecovered_article_is_reported_as_unresolved(self, monkeypatch):
        """A lab stuck on summaries has to be visible somewhere. `unresolved`
        is the channel this pipeline already uses for a known gap."""
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "rss",
                            lambda lab, cutoff, skip=None: [self._summary()])
        monkeypatch.setattr(fa, "enrich_wayback", lambda lab, articles, cutoff: [
            {"kind": "backfill", "name": articles[0]["url"],
             "reason": "no archive snapshot in window; still on the RSS summary"}])

        result = adapters.fetch_announcements(self._source())

        assert len(result.unresolved) == 1
        assert result.unresolved[0]["kind"] == "backfill"

    def test_a_failing_archive_does_not_lose_the_articles(self, monkeypatch):
        """Discovery already succeeded. The archive being down means worse
        text, not a dead source -- failing here would throw away a good fetch
        and mark OpenAI down for a reason that has nothing to do with OpenAI."""
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "rss",
                            lambda lab, cutoff, skip=None: [self._summary()])

        def boom(lab, articles, cutoff):
            raise RuntimeError("wayback fetch failed: 503")

        monkeypatch.setattr(fa, "enrich_wayback", boom)

        result = adapters.fetch_announcements(self._source())

        assert len(result.items) == 1
        assert result.items[0]["text_source"] == "rss_summary"
        assert any("backfill failed" in u["reason"] for u in result.unresolved)

    def test_no_articles_means_no_archive_query(self, monkeypatch):
        import fetch_announcements as fa

        monkeypatch.setitem(fa.METHODS, "rss", lambda lab, cutoff, skip=None: [])
        called = []
        monkeypatch.setattr(fa, "enrich_wayback",
                            lambda *a, **k: called.append(1) or [])

        adapters.fetch_announcements(self._source())

        assert called == []


class TestRepoRelevanceGate:
    """`_relevant_slice` — the gate between the ranking and the releases fetch.

    Its failures are all quiet. A gate that shrinks the watch list instead of
    refilling it watches fewer repositories and reports nothing. A gate that
    fails closed empties the watch list during a provider outage and looks
    exactly like a week in which eight labs shipped nothing. And spend counted
    twice moves the monthly ceiling in the direction that stops the pipeline
    early, which surfaces as an unrelated budget alert weeks later.
    """

    def ranked(self, *names):
        return [{"org": "google-deepmind", "repo": n, "stars": 100 - i,
                 "description": None, "age": "established", "created_at": None}
                for i, n in enumerate(names)]

    def cfg(self, **relevance):
        block = {"enabled": True, "provider": "anthropic", "model": "m",
                 "prompt_version": "r1", "max_judged": 30}
        block.update(relevance)
        return {"releases_watch": 2, "relevance": block}

    def stub(self, monkeypatch, answer):
        """Install a local `judge`; `answer(repo)` returns (verdict, usd)."""
        from app.pipeline import repo_relevance

        monkeypatch.setattr(repo_relevance, "judge",
                            lambda session, row, config: answer(row["repo"]))

    def test_a_rejected_repo_frees_its_slot_rather_than_shrinking_the_list(
            self, monkeypatch):
        self.stub(monkeypatch, lambda repo: (
            {"relevant": repo != "mujoco", "reason": "physics"}, 0.001, None))
        picked, report = adapters._relevant_slice(
            self.ranked("mujoco", "gemma", "gpt-oss"), {}, self.cfg(), None)

        assert [r["repo"] for r in picked] == ["gemma", "gpt-oss"]
        assert report["excluded"] == [{"repo": "mujoco", "why": "physics"}]

    def test_the_kill_switch_restores_the_plain_top_slice(self, monkeypatch):
        """`enabled: false` has to mean *exactly* what the leg did before, and
        it has to judge nothing — the switch exists so an operator can stop the
        spend without a deploy."""
        self.stub(monkeypatch, lambda repo: pytest.fail("judged while disabled"))
        rows = self.ranked("mujoco", "gemma", "gpt-oss")
        picked, report = adapters._relevant_slice(
            rows, {}, self.cfg(enabled=False), None)

        assert [r["repo"] for r in picked] == ["mujoco", "gemma"]
        assert report == {}

    def test_a_failed_judgement_keeps_the_repo_and_is_counted(self, monkeypatch):
        """Fail open. A dropped repository is invisible downstream; a kept one
        costs a classification and can be seen. The count is what
        `alerts.repo_filter_unavailable` reads, so a provider outage cannot make
        the gate a silent no-op."""
        self.stub(monkeypatch, lambda repo: (None, 0.0, "RuntimeError: rate limited"))
        picked, report = adapters._relevant_slice(
            self.ranked("mujoco", "gemma"), {}, self.cfg(), None)

        assert [r["repo"] for r in picked] == ["mujoco", "gemma"]
        assert report["errors"] == 2
        assert report["excluded"] == []

    def test_the_spend_is_summed_for_the_caller_to_bill_once(self, monkeypatch):
        self.stub(monkeypatch, lambda repo: ({"relevant": True, "reason": "r"}, 0.002, None))
        _, report = adapters._relevant_slice(
            self.ranked("a", "b"), {}, self.cfg(), None)

        assert report["usd"] == 0.004
        assert report["judged"] == 2

    def test_topics_and_language_reach_the_judge(self, monkeypatch):
        """They are the strongest free discriminator the filter gets, and they
        live on the listing rather than on the ranked row. Dropping them on the
        floor would still produce a verdict, just a worse one."""
        seen = {}

        from app.pipeline import repo_relevance

        def capture(session, row, config):
            seen.update(row)
            return {"relevant": True, "reason": "r"}, 0.0, None

        monkeypatch.setattr(repo_relevance, "judge", capture)
        listing = {"mujoco": {"language": "C", "topics": ["physics", "robotics"]}}
        adapters._relevant_slice(self.ranked("mujoco"), listing, self.cfg(), None)

        assert seen["language"] == "C"
        assert seen["topics"] == ["physics", "robotics"]

    def test_repos_already_in_the_corpus_are_judged_even_below_the_walk(
            self, monkeypatch, session):
        """The walk and the derivation gate need different populations, and
        conflating them was the feature's worst bug.

        `shortlist` stops at the tenth passing repository, so anything below
        that is never judged — and `transform`'s gate only *reads* verdicts, so
        an unjudged repository's releases are re-derived on every firing for
        ever. Measured against the live ranking, relying on the walk alone left
        31 of the 87 corpus repositories unjudged, `torax` and `habitat-lab`
        among them — both named in D65 as removed."""
        from app import models as m

        # `deep` sits far below the watch cut but has releases in the corpus.
        session.add(m.RawArticle(
            url="https://github.com/google-deepmind/deep/releases/tag/v1",
            content_hash="h", source_file="github_releases",
            payload={"lab": "google-deepmind", "title": "t", "date": "2026-09-01",
                     "text": "b", "text_source": "github_release",
                     "org": "google-deepmind", "repo": "deep"}))
        session.flush()

        self.stub(monkeypatch, lambda repo: (
            {"relevant": repo != "deep", "reason": "a physics simulator"}, 0.0007, None))
        rows = self.ranked("a", "b", "c", "deep", "e")
        picked, report = adapters._relevant_slice(rows, {}, self.cfg(), session)

        assert [r["repo"] for r in picked] == ["a", "b"], "the walk still stops early"
        assert report["swept"] == 1
        assert [e["repo"] for e in report["excluded"]] == ["deep"], (
            "a repository in the corpus was never judged, so its releases would "
            "be re-derived for ever")

    def test_a_corpus_repo_the_ranking_dropped_is_reported_as_unjudged(
            self, monkeypatch, session):
        """`ranked` excludes anything absent from the live 12-month listing,
        below `min_stars`, or matching `is_mirror`. Such a repository is still
        in the corpus, so its releases are still being derived — and the sweep
        cannot judge it. Unreachable today, reachable the first time a watched
        repository goes a year without a push. The point is that it is *named*
        rather than silently skipped, because `swept` alone reads as coverage."""
        from app import models as m

        session.add(m.RawArticle(
            url="https://github.com/google-deepmind/gone/releases/tag/v1",
            content_hash="h", source_file="github_releases",
            payload={"lab": "google-deepmind", "title": "t", "date": "2026-09-01",
                     "text": "b", "text_source": "github_release",
                     "org": "google-deepmind", "repo": "gone"}))
        session.flush()

        self.stub(monkeypatch, lambda repo: ({"relevant": True, "reason": "r"}, 0.0007, None))
        # "gone" is in the corpus but not in the ranking.
        _, report = adapters._relevant_slice(
            self.ranked("a", "b"), {}, self.cfg(), session)

        assert report["unjudged"] == ["gone"]
        assert report["swept"] == 0, "swept must count what was judged, not the target"

    def test_the_sweep_does_not_re_judge_what_the_walk_already_saw(
            self, monkeypatch, session):
        from app import models as m

        session.add(m.RawArticle(
            url="https://github.com/google-deepmind/a/releases/tag/v1",
            content_hash="h", source_file="github_releases",
            payload={"lab": "google-deepmind", "title": "t", "date": "2026-09-01",
                     "text": "b", "text_source": "github_release",
                     "org": "google-deepmind", "repo": "a"}))
        session.flush()

        self.stub(monkeypatch, lambda repo: ({"relevant": True, "reason": "r"}, 0.0007, None))
        _, report = adapters._relevant_slice(
            self.ranked("a", "b", "c"), {}, self.cfg(), session)

        assert report["judged"] == 2, "judged a repository twice in one firing"

    def test_running_out_of_ranking_is_recorded_not_raised(self, monkeypatch):
        """An org may genuinely not have `releases_watch` relevant repositories.
        That is a finding about the org, not a failure, so it is reported for a
        reader rather than alerted on."""
        self.stub(monkeypatch, lambda repo: ({"relevant": False, "reason": "no"}, 0.0, None))
        picked, report = adapters._relevant_slice(
            self.ranked("a", "b", "c"), {}, self.cfg(max_judged=10), None)

        assert picked == []
        assert report["capped"] is False, "ran out of repos, not out of budget"

    def test_hitting_the_cap_is_reported_as_capped(self, monkeypatch):
        """The cap counts *paid* judgements, so the stub has to charge for
        them."""
        self.stub(monkeypatch,
                  lambda repo: ({"relevant": False, "reason": "no"}, 0.0007, None))
        rows = self.ranked(*[f"r{i}" for i in range(20)])
        picked, report = adapters._relevant_slice(rows, {}, self.cfg(max_judged=5), None)

        assert picked == []
        assert report["paid"] == 5
        assert report["capped"] is True

    def test_a_cached_walk_goes_deeper_than_the_cap_for_free(self, monkeypatch):
        """The cap exists to bound spend and wall-clock, and a cache hit costs
        neither. Counting cache hits against it froze the walk at a fixed depth
        for ever — measured, that left `facebookresearch` watching three
        repositories instead of ten on every firing, with no way to recover even
        though going deeper was free."""
        # Every answer is free, as a warm cache is: usd 0.0 and no error.
        self.stub(monkeypatch, lambda repo: (
            {"relevant": repo in {"r18", "r19"}, "reason": "cached"}, 0.0, None))
        rows = self.ranked(*[f"r{i}" for i in range(20)])
        picked, report = adapters._relevant_slice(rows, {}, self.cfg(max_judged=5), None)

        assert [r["repo"] for r in picked] == ["r18", "r19"]
        assert report["paid"] == 0
        assert report["capped"] is False
        assert report["judged"] == 20, "stopped early on judgements that cost nothing"


class TestThePostsWatermarkIsReadNotJustWritten:
    """`max_published` was written every firing and never read back.

    The leg has a weekly cadence and a 90-day window, so the unread mark meant
    re-buying ~89 days of posts every week. Nothing failed and nothing alerted:
    the corpus was correct, it was just paid for again.
    """

    def test_a_stored_date_becomes_the_start_of_that_day(self):
        """Midnight, not the instant: the mark is a date, so re-read its day."""
        from datetime import datetime, timezone
        assert adapters._post_since("2026-09-04") == datetime(2026, 9, 4, tzinfo=timezone.utc)

    def test_a_full_timestamp_is_accepted_too(self):
        """`max_published` is a date today; a later change must not break this."""
        from datetime import datetime, timezone
        assert adapters._post_since("2026-09-04T11:34:32Z") == datetime(
            2026, 9, 4, tzinfo=timezone.utc)

    def test_a_missing_or_corrupt_watermark_reads_the_whole_window(self):
        """Fail wide, not closed: a bad mark costs money, a silent hole costs coverage."""
        for bad in (None, "", "never", 20260904, {"max_published": "2026-09-04"}):
            assert adapters._post_since(bad) is None


class TestAPartialPostsPullDoesNotAdvanceTheMark:
    """One mark for the whole leg, so a partial run must not move it.

    Harmless while the mark was write-only; a silent, permanent coverage hole
    the moment it started gating the fetch. If @sama's timeline errors and the
    mark advances on the other 26 handles, every post @sama published before the
    new floor is skipped for ever -- and nothing downstream can tell those from
    posts that were never written.
    """

    def _leg(self, monkeypatch, records, unresolved):
        from pathlib import Path

        import harvest_x
        import yaml

        monkeypatch.setattr(harvest_x, "pull",
                            lambda *a, **k: (records, list(unresolved)))
        monkeypatch.setattr("x_client.load_token", lambda env: "token")
        config = yaml.safe_load(
            (Path(__file__).parent.parent / "config" / "posts_sources.yaml")
            .read_text(encoding="utf-8"))
        return Source(leg="posts", id="posts", label="posts", stage=5,
                      enabled=True, config=config)

    def _post(self, handle="sama", date="2026-09-04"):
        return {"url": f"https://x.com/{handle}/status/1", "lab": "openai",
                "date": date, "title": "t", "text": "x" * 300,
                "text_source": "x_post", "author_handle": handle,
                "author_name": "n", "author_role": "r", "x_evidence": "profile",
                "role_contested": False, "is_quote": False, "quoted_id": None,
                "links": []}

    def test_a_clean_run_advances_the_mark(self, monkeypatch):
        source = self._leg(monkeypatch, [self._post()], [])
        result = adapters.fetch_posts(source)
        assert result.watermark == {"max_published": "2026-09-04"}

    def test_a_handle_that_errored_holds_the_mark_back(self, monkeypatch):
        source = self._leg(monkeypatch, [self._post()], [
            {"url": "https://x.com/demishassabis", "lab": "google-deepmind",
             "kind": "post", "reason": "fetch failed: HTTPError"}])
        result = adapters.fetch_posts(source)
        assert result.watermark == {}, (
            "an empty watermark leaves the stored mark in place, so the next "
            "firing retries the window this one only partly read")

    def test_a_handle_skipped_for_budget_holds_the_mark_back(self, monkeypatch):
        source = self._leg(monkeypatch, [self._post()], [
            {"url": "https://x.com/sama", "lab": "openai", "kind": "post",
             "reason": "post budget exhausted before this handle"}])
        assert adapters.fetch_posts(source).watermark == {}

    def test_a_prefiltered_post_is_not_a_partial_run(self, monkeypatch):
        """The prefilter drops posts we *did* read. Treating that as incomplete
        would freeze the mark for ever, because it fires on most runs."""
        source = self._leg(monkeypatch,
                           [self._post(), self._post("sama") | {
                               "url": "https://x.com/sama/status/2",
                               "text": "congrats!"}],
                           [])
        result = adapters.fetch_posts(source)
        assert result.watermark == {"max_published": "2026-09-04"}
        assert any("prefiltered" in u["reason"] for u in result.unresolved)
