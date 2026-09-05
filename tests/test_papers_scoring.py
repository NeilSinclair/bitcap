"""The papers leg as a scored corpus: extraction, routing and the two guards.

The silent failures this file exists to catch, in the order they would bite:

* An abstract extractor that starts returning a cookie banner. Papers would
  keep flowing, score zero, and look like a corpus we judged unimportant.
* A paper landing on top of an announcement that shares its URL, replacing full
  text with a lead section.
* `connect` run once per prompt version, which would silently delete the other
  corpus's connections -- it rebuilds the whole table.
* A backfill firing content alerts for documents years old.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models as m
from app.connect import connect
from app.db import create_all
from app.pipeline import alerts as alerts_mod
from app.pipeline.classify import pending_urls
from app.pipeline.registry import CORPUS_LABELS, PAPERS, PAPERS_CORPUS
from app.scoring import score_of
from app.transform import transform
sys.path.insert(0, str(Path(__file__).parent.parent / "research" / "papers"))

import paper_text  # noqa: E402

ROOT_CONFIG = yaml.safe_load(open("config/scoring.yaml"))


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

ARXIV = """<html><body><blockquote class="abstract descriptor">
<span class="descriptor">Abstract:</span> We present DeepSeek-V4, a mixture of
experts model. In the one-million-token context setting it requires only 27% of
single-token inference FLOPs and 10% of the KV cache compared with V3.2, which
is the sort of sentence this whole corpus exists to reach and quote back.
</blockquote></body></html>"""

HEADING = """<html><body><main><h1>Paper</h1><h2>Abstract</h2>
<p>We study whether people can tell an AI moral argument from a human one, over
a sample of participants, and report that source labelling changes agreement
more than argument quality does across every condition we measured here.</p>
<h2>Authors</h2><p>Someone</p></main></body></html>"""

LEAD = """<html><body><nav>Skip to content</nav><article>
<h1>Modular Pretraining Enables Access Control</h1>
<p>We introduce Gradient-Routed Auxiliary Modules, a training technique that
isolates dangerous capabilities into deletable modules, so a single model can be
reconfigured to match any of five differently filtered models at inference.</p>
<h2>Method</h2><p>Details we do not want.</p></article></body></html>"""


class TestExtraction:
    def test_arxiv_abstract_is_taken_without_its_label(self):
        text, how = paper_text.extract(ARXIV, "blockquote_abstract")
        assert how == "blockquote_abstract"
        assert text.startswith("We present DeepSeek-V4")
        assert "10% of the KV cache" in text

    def test_a_heading_section_stops_at_the_next_heading(self):
        text, how = paper_text.extract(HEADING, "heading_section")
        assert how == "heading_section"
        assert "source labelling changes agreement" in text
        assert "Someone" not in text

    def test_a_lead_section_drops_navigation(self):
        text, how = paper_text.extract(LEAD, "lead_section", max_chars=6000)
        assert how == "lead_section"
        assert "Gradient-Routed" in text
        assert "Skip to content" not in text

    def test_a_lead_section_stops_at_a_heading_once_it_has_enough(self):
        """The cut is deliberately not "the first heading": a subheading inside
        the opening paragraph would truncate the lead to nothing. It fires at
        the first heading past a quarter of the budget, so the boundary moves
        with `max_chars` rather than with the page's markup."""
        text, _ = paper_text.extract(LEAD, "lead_section", max_chars=400)
        assert "Gradient-Routed" in text
        assert "Details we do not want" not in text

    def test_a_configured_strategy_that_misses_falls_back(self):
        """Meta's pages carry no Abstract heading; extraction must degrade to a
        lead section rather than dropping the paper out of the corpus."""
        text, how = paper_text.extract(LEAD, "heading_section", max_chars=6000)
        assert how == "lead_section" and "Gradient-Routed" in text

    def test_a_page_with_no_prose_is_a_failure_not_a_short_abstract(self):
        """The failure that must stay loud. A tag-free row in the register is
        indistinguishable from a paper we read and judged unimportant."""
        assert paper_text.extract("<html><body><nav>Menu</nav></body></html>",
                                  "lead_section") is None

    def test_an_unknown_strategy_is_refused(self):
        with pytest.raises(ValueError):
            paper_text.extract(ARXIV, "vibes")

    def test_every_enabled_lab_names_a_strategy_that_exists(self):
        """Config is the register of what is covered; a typo here would send a
        whole lab down the fallback chain silently."""
        labs = yaml.safe_load(open("config/papers_sources.yaml"))["labs"]
        for lab in labs:
            assert lab["abstract"]["strategy"] in paper_text.STRATEGIES, lab["lab"]
            assert lab["abstract"]["max_chars"] > 0

    def test_the_papers_corpus_is_registered_as_article_producing(self):
        assert CORPUS_LABELS[PAPERS] == PAPERS_CORPUS


# --------------------------------------------------------------------------
# Scoring: the event-type disambiguation is the whole point of the p1 prompt
# --------------------------------------------------------------------------

def _result(event, magnitude="high", confidence="high"):
    return {"event_type": event, "summary": "s", "mechanisms": [
        {"id": "inference_cost_down", "sign": "positive", "magnitude": magnitude,
         "confidence": confidence, "reason": "r", "quote": "q"}]}


class TestATechnicalReportIsAModelRelease:
    """Why p1 exists. v9 defines `research_result` as "a paper or a novel
    method", so a DeepSeek-V4-style technical report classifies there by
    default -- capping the strongest evidence in the corpus at 60% of what the
    same launch scores as a blog post. p1 rewords the type, and changes nothing
    in config/scoring.yaml, so these two numbers are the whole consequence."""

    def test_a_technical_report_can_reach_the_top_of_the_scale(self):
        assert score_of(_result("frontier_model_release"), ROOT_CONFIG) == (100.0, "high")

    def test_a_plain_research_result_ceilings_at_the_high_band_minimum(self):
        score, band = score_of(_result("research_result"), ROOT_CONFIG)
        assert (score, band) == (60.0, "high")

    def test_a_research_result_needs_perfect_evidence_to_be_surfaced_at_all(self):
        """The asymmetry, stated as a test: a finding with anything less than a
        high/high quote-backed tag drops out of the digest's `always_band`
        route, where a model release at the same evidence would not."""
        assert score_of(_result("research_result", "medium"), ROOT_CONFIG)[1] == "medium"
        assert score_of(_result("frontier_model_release", "medium"), ROOT_CONFIG)[1] == "high"

    def test_the_paper_prompt_changes_no_scoring_weight(self):
        """p1 is a wording fix. If someone later adds a paper-only event type,
        `max_event_weight` must still equal the largest weight or every existing
        announcement score silently rescales."""
        assert ROOT_CONFIG["max_event_weight"] == max(ROOT_CONFIG["event_weight"].values())

    def test_the_paper_prompt_offers_only_events_that_have_a_weight(self):
        """An event type the prompt offers but `scoring.yaml` does not weight
        scores zero, silently -- `score_of` looks it up with `.get(..., 0)`."""
        prompt = open("prompts/paper_scoring/p1.md").read()
        section = prompt.split("## Event type", 1)[1].split("## Output", 1)[0]
        offered = {line.split("`")[1] for line in section.splitlines()
                   if line.startswith("`") and "` — " in line}
        assert len(offered) > 10, offered
        assert offered <= set(ROOT_CONFIG["event_weight"]), offered - set(ROOT_CONFIG["event_weight"])

    def test_the_paper_prompt_keeps_the_announcement_event_vocabulary(self):
        """p1 rewords `research_result`; it must not invent types. A new type
        above weight 5 would rescale every existing announcement score."""
        section = open("prompts/paper_scoring/p1.md").read().split("## Event type", 1)[1]
        v9 = open("prompts/announcement_scoring/v9.md").read().split("## Event type", 1)[1]
        ids = lambda t: {line.split("`")[1] for line in t.split("## Output")[0].splitlines()
                         if line.startswith("`") and "` — " in line}
        assert ids(section) == ids(v9)


# --------------------------------------------------------------------------
# Two corpora, one spine
# --------------------------------------------------------------------------

TAG = {"id": "inference_cost_down", "sign": "positive", "magnitude": "high",
       "confidence": "high", "reason": "r", "quote": "q"}


def _payload(event):
    return {"event_type": event, "summary": "s", "is_signal": True, "notable": False,
            "notable_reason": "", "dropped_tags": [], "mechanisms": [TAG],
            "categories": [], "practices": []}


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    with Session(engine) as s:
        s.add(m.RefLab(id="deepseek", label="DeepSeek", config_version=1))
        s.add(m.RefMechanism(id="inference_cost_down", label="M", description="d",
                             polarity_note="p", config_version=2))
        s.add(m.Holding(isin="X", name="NVIDIA Corporation",
                        custodian_name="NVIDIA Corporation", aliases=[],
                        ticker="NVDA", weight_pct=1.0, ai_role="primary",
                        holdings_version=1, companies_version=1))
        s.flush()
        s.add(m.HoldingMechanism(isin="X", mechanism_id="inference_cost_down",
                                 sign="positive", magnitude="high",
                                 confidence="high", why="w", source="https://s"))
        s.add(m.RawArticle(
            url="https://lab/blog", content_hash="a",
            source_file="research/docs/announcements.json",
            payload={"lab": "deepseek", "url": "https://lab/blog", "date": "2026-08-01",
                     "title": "Announcement", "text": "t", "text_source": "full_text"}))
        s.add(m.RawArticle(
            url="https://arxiv.org/abs/1", content_hash="b", source_file=PAPERS_CORPUS,
            payload={"lab": "deepseek", "url": "https://arxiv.org/abs/1",
                     "date": "2026-08-02", "title": "Paper", "text": "t",
                     "text_source": "paper_abstract"}))
        s.add(m.RawLlmResponse(url="https://lab/blog", prompt_version="v9",
                               payload=_payload("pricing_change")))
        s.add(m.RawLlmResponse(url="https://arxiv.org/abs/1", prompt_version="p1",
                               payload=_payload("frontier_model_release")))
        s.commit()
        yield s


class TestOneSpineTwoVersions:
    def test_the_work_list_can_be_restricted_to_the_papers_corpus(self):
        """`include_source_files` is the guard that stops the paper prompt from
        widening onto announcements: at p1 every announcement is also unscored,
        so an unrestricted work list would score the whole register twice."""
        engine = create_engine("sqlite:///:memory:")
        create_all(engine)
        with Session(engine) as s:
            s.add(m.RawArticle(url="https://a", content_hash="h",
                               source_file="research/docs/announcements.json", payload={}))
            s.add(m.RawArticle(url="https://p", content_hash="h",
                               source_file=PAPERS_CORPUS, payload={}))
            s.commit()
            assert pending_urls(s, "p1", include_source_files=(PAPERS_CORPUS,)) == ["https://p"]
            assert pending_urls(s, "v9",
                                exclude_source_files=(PAPERS_CORPUS,)) == ["https://a"]

    def test_connect_run_once_keeps_both_corpora(self, session):
        """`connect` deletes the whole table before rebuilding, so calling it
        once per version leaves only the last one's rows. This is the test that
        would catch someone 'tidying' it back into a per-version loop."""
        transform(session, "v9")
        transform(session, "p1")
        connect(session, ("v9", "p1"))
        session.flush()
        urls = {
            session.get(m.Article, c.article_id).url
            for c in session.scalars(select(m.Connection))
        }
        assert urls == {"https://lab/blog", "https://arxiv.org/abs/1"}

    def test_running_connect_per_version_is_what_loses_rows(self, session):
        """The failure mode itself, pinned so the docstring above is not a
        claim on trust."""
        transform(session, "v9")
        transform(session, "p1")
        connect(session, "v9")
        connect(session, "p1")
        session.flush()
        urls = {
            session.get(m.Article, c.article_id).url
            for c in session.scalars(select(m.Connection))
        }
        assert urls == {"https://arxiv.org/abs/1"}

    def test_each_version_derives_its_own_classification_additively(self, session):
        transform(session, "v9")
        transform(session, "p1")
        session.flush()
        versions = {c.prompt_version for c in session.scalars(select(m.Classification))}
        assert versions == {"v9", "p1"}


# --------------------------------------------------------------------------
# The backfill guard
# --------------------------------------------------------------------------

class TestABackfillDoesNotAlert:
    """A content alert claims a lab published something worth seeing now. The
    papers leg carries documents back to 2023, several correctly scoring 100 --
    without an age bound the first firing pages someone about GPT-4's technical
    report."""

    @pytest.fixture()
    def alerting(self):
        engine = create_engine("sqlite:///:memory:")
        create_all(engine)
        with Session(engine) as s:
            s.add(m.RefLab(id="deepseek", label="DeepSeek", config_version=1))
            s.flush()
            for url, published in (("https://new", date.today() - timedelta(days=3)),
                                   ("https://old", date.today() - timedelta(days=900))):
                raw = m.RawArticle(url=url, content_hash=url,
                                   source_file=PAPERS_CORPUS, payload={})
                s.add(raw)
                s.flush()
                art = m.Article(url=url, raw_article_id=raw.id, lab="deepseek", title=url,
                                published_on=published, text="t", text_source="paper_abstract")
                s.add(art)
                s.flush()
                s.add(m.Classification(article_id=art.id, prompt_version="p1",
                                       scoring_version=4, event_type="frontier_model_release",
                                       summary="s", notable=False, notable_reason="",
                                       dropped_tags=[], score=100.0, band="high",
                                       ai_score=0.0, ai_band="none"))
            s.commit()
            yield s

    def test_a_recent_high_band_paper_alerts(self, alerting):
        got = alerts_mod.high_band_items(alerting, {"content_max_age_days": 120}, {})
        assert [c.payload["url"] for c in got] == ["https://new"]

    def test_a_three_year_old_paper_does_not(self, alerting):
        got = alerts_mod.high_band_items(alerting, {"content_max_age_days": 120}, {})
        assert "https://old" not in [c.payload["url"] for c in got]

    def test_without_the_bound_the_backfill_pages_someone(self, alerting):
        """Pins that the guard is what does the work, not the fixture."""
        got = alerts_mod.high_band_items(alerting, {"content_max_age_days": 100000}, {})
        assert len(got) == 2

    def test_the_configured_default_covers_every_announcement_in_the_window(self):
        """120 days is measured, not chosen: the announcements window is ~3
        months, so the bound must not clip it."""
        cfg = yaml.safe_load(open("config/pipeline.yaml"))["alerts"]
        assert cfg["content_max_age_days"] >= 92


# --------------------------------------------------------------------------
# The gold sample
# --------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent.parent / "research" / "papers"))
import build_paper_gold_set as gold  # noqa: E402


class TestTheGoldSampleIsStratifiedByDocumentType:
    """Stratified by document type, not by lab -- the finding the whole leg
    rests on. A lab-stratified sample would put DeepSeek's technical reports and
    Mistral's product write-ups in one bucket and learn nothing."""

    def test_a_technical_report_is_not_filed_as_research(self):
        assert gold.document_type("deepseek", "DeepSeek-V4: Towards ...") == "technical_report"
        assert gold.document_type("openai", "GPT-4 Technical Report") == "technical_report"

    def test_a_system_card_is_its_own_stratum(self):
        assert gold.document_type("openai", "OpenAI GPT-5 System Card") == "system_card"
        assert gold.document_type("openai", "gpt-oss-120b Model Card") == "system_card"

    def test_a_card_beats_the_lab_rule(self):
        """DeepSeek's rule is lab-wide; a card must still be a card."""
        assert gold.document_type("deepseek", "DeepSeek-V9 System Card") == "system_card"

    def test_the_noise_stratum_is_the_one_that_fails_silently(self):
        assert gold.document_type("google-deepmind", "A moral Turing test") == "safety_social"
        assert gold.document_type("anthropic", "Circuits Updates") == "interpretability"
        assert gold.document_type("meta-ai", "WaiT for the Signal") == "component"

    def test_the_sample_is_ten_papers_over_all_five_strata(self):
        assert sum(gold.QUOTA.values()) == 10
        assert set(gold.QUOTA) == {"technical_report", "system_card",
                                   "interpretability", "safety_social", "component"}

    def test_noise_is_over_sampled_deliberately(self):
        """"Correctly scored zero" is the case worth pinning: a scorer that
        starts finding transmission in social-science papers buries the
        technical reports and raises no error doing it."""
        assert gold.QUOTA["safety_social"] >= gold.QUOTA["technical_report"]

    def test_the_draw_is_reproducible(self):
        records = [{"lab": "google-deepmind", "title": f"Safety {i}", "url": f"u{i}",
                    "date": "2026-01-01", "text": "t", "text_source": "paper_abstract"}
                   for i in range(9)]
        quota = {"safety_social": 3}
        assert [r["url"] for r in gold.sample(records, quota)] == \
               [r["url"] for r in gold.sample(records, quota)]

    def test_every_emitted_file_demands_its_labeller(self, tmp_path):
        """A gold file that reaches the metrics without `labelled_by` is a file
        whose provenance nobody can state. Neither this set nor the announcements
        one is human ground truth -- both are cross-model proxies -- so which
        model produced which labels is the only thing that makes them
        comparable."""
        import json
        records = [{"lab": "anthropic", "title": "T", "url": "u", "date": "2026-01-01",
                    "text": "t", "text_source": "paper_lead", "document_type": "interpretability"}]
        (path,) = gold.write(records, tmp_path)
        written = json.loads(path.read_text())
        assert "labelled_by" in written["gold"]
        assert written["gold"]["labelled_by"] is None
        assert written["gold"]["event_type"] is None

    def test_the_system_answer_never_reaches_a_gold_file(self, tmp_path):
        """The sample must not inherit the answer it exists to check."""
        import json
        records = [{"lab": "deepseek", "title": "T", "url": "u", "date": "2026-01-01",
                    "text": "t", "text_source": "paper_abstract",
                    "document_type": "technical_report",
                    "score": 100.0, "band": "high", "event_type": "frontier_model_release"}]
        (path,) = gold.write(records, tmp_path)
        written = json.loads(path.read_text())
        assert "score" not in written and "band" not in written
        assert written["gold"]["event_type"] is None


# --------------------------------------------------------------------------
# collect(): everything below extract() that a live firing depends on
# --------------------------------------------------------------------------

import fetch_cache  # noqa: E402

LABS = {"deepseek": {"abstract": {"strategy": "blockquote_abstract", "max_chars": 6000}},
        "mistral": {"abstract": {"strategy": "lead_section", "max_chars": 6000}}}


def _papers_session(rows):
    engine = create_engine("sqlite:///:memory:")
    create_all(engine)
    s = Session(engine)
    for lab, url, payload, source_file in rows:
        if source_file is not None:
            s.add(m.RawArticle(url=url, content_hash=url, source_file=source_file,
                               payload={"text": "the full announcement"}))
        s.add(m.RawPaper(url=url, lab=lab, payload=payload, content_hash=url))
    s.commit()
    return s


class TestCollect:
    @pytest.fixture(autouse=True)
    def _no_network(self, monkeypatch):
        monkeypatch.setattr(fetch_cache, "fetch",
                            lambda url, **kw: ARXIV if "arxiv" in url else LEAD)

    def test_a_paper_becomes_an_article_shaped_record(self):
        s = _papers_session([("deepseek", "https://arxiv.org/abs/1",
                              {"title": "T", "date": "2026-04-26"}, None)])
        records, unresolved = paper_text.collect(s, LABS)
        assert unresolved == []
        (record,) = records
        assert record["url"] == "https://arxiv.org/abs/1"
        assert record["text_source"] == "paper_abstract"
        assert record["extraction"] == "blockquote_abstract"
        assert record["extraction_configured"] == "blockquote_abstract"
        assert "10% of the KV cache" in record["text"]

    def test_a_paper_already_held_as_an_announcement_is_recorded_not_landed(self):
        """The incident D57 records. Mistral has no publications page, so its
        papers leg cites `mistral.ai/news/<slug>` -- a URL the announcements leg
        already holds at full text. `raw_articles` is keyed on URL, so landing
        the paper replaced 13k characters of announcement with a 2k lead
        section. Silently: the load reported `updated: 2` and nothing else."""
        s = _papers_session([("mistral", "https://mistral.ai/news/x/",
                              {"title": "T", "date": "2026-07-22"},
                              "research/docs/announcements.json")])
        records, unresolved = paper_text.collect(s, LABS)
        assert records == []
        assert len(unresolved) == 1
        assert "already in the corpus as an announcement" in unresolved[0]["reason"]

    def test_a_paper_the_papers_leg_itself_landed_is_not_treated_as_a_duplicate(self):
        """The guard must not fire on the papers corpus's own rows, or the
        second firing would empty the register."""
        s = _papers_session([("deepseek", "https://arxiv.org/abs/1",
                              {"title": "T", "date": "2026-04-26"}, PAPERS_CORPUS)])
        records, unresolved = paper_text.collect(s, LABS)
        assert len(records) == 1 and unresolved == []

    @pytest.mark.parametrize("bad", [None, "", "n.d.", "2026-13-01", "26 April 2026"])
    def test_a_paper_with_no_usable_date_never_reaches_bronze(self, bad):
        """A POISON PILL, not a cosmetic gap. `transform` calls
        `date.fromisoformat(p["date"])` inside `_etl`'s single transaction, so
        one such row fails the run -- and, because the row stays in bronze, every
        subsequent firing and every `bitcap-db load` fails at the same line until
        someone deletes it by hand. Two harvesters can legitimately return None:
        `deepmind_harvest.detail_page_info` documents its date as "ISO or None"."""
        s = _papers_session([("deepseek", "https://arxiv.org/abs/1",
                              {"title": "T", "date": bad}, None)])
        records, unresolved = paper_text.collect(s, LABS)
        assert records == []
        assert "no usable publication date" in unresolved[0]["reason"]

    def test_the_date_that_survives_is_the_one_transform_can_parse(self):
        from datetime import date as _date
        s = _papers_session([("deepseek", "https://arxiv.org/abs/1",
                              {"title": "T", "date": "2026-04-26"}, None)])
        (record,), _ = paper_text.collect(s, LABS)
        assert _date.fromisoformat(record["date"]) == _date(2026, 4, 26)

    def test_a_lab_with_no_abstract_config_is_recorded(self):
        s = _papers_session([("xai", "https://x.ai/p", {"title": "T", "date": "2026-01-01"}, None)])
        records, unresolved = paper_text.collect(s, LABS)
        assert records == [] and "no abstract config" in unresolved[0]["reason"]

    def test_a_dead_page_does_not_abort_the_leg(self, monkeypatch):
        def boom(url, **kw):
            raise RuntimeError("gone")
        monkeypatch.setattr(fetch_cache, "fetch", boom)
        s = _papers_session([("deepseek", "https://arxiv.org/abs/1",
                              {"title": "T", "date": "2026-04-26"}, None)])
        records, unresolved = paper_text.collect(s, LABS)
        assert records == [] and "fetch failed" in unresolved[0]["reason"]


# --------------------------------------------------------------------------
# The routing that decides which prompt a paper is scored against
# --------------------------------------------------------------------------

class TestPapersAreScoredAgainstThePaperPrompt:
    """The regression the review found nothing red for: delete `**variant` from
    `classify_new` and every paper is scored against the v9 announcement prompt,
    cached under `announcement_scores/v9/`, and loaded as a *v9* classification.
    The p1 register stays empty for ever and the event-type disambiguation that
    justifies this whole branch is silently gone."""

    @pytest.fixture()
    def bronze(self):
        engine = create_engine("sqlite:///:memory:")
        create_all(engine)
        with Session(engine) as s:
            s.add(m.RawArticle(url="https://arxiv.org/abs/1", content_hash="h",
                               source_file=PAPERS_CORPUS,
                               payload={"url": "https://arxiv.org/abs/1", "text": "t",
                                        "lab": "deepseek", "date": "2026-04-26",
                                        "title": "T", "text_source": "paper_abstract"}))
            s.commit()
            yield s

    def _spy(self, monkeypatch):
        seen = {}
        import score_announcements as scorer

        def fake_run(articles, model, workers, batch, budget, variant=None):
            seen["variant"] = variant
            return {"scored": [], "failures": [], "cost_usd": 0.0,
                    "classified": len(articles), "skipped_for_budget": 0, "bands": {}}
        monkeypatch.setattr(scorer, "run", fake_run)
        return seen

    def test_the_papers_corpus_is_scored_under_the_paper_prompt(self, bronze, monkeypatch):
        from app.pipeline.budget import Budget
        from app.pipeline.classify import classify_new
        seen = self._spy(monkeypatch)
        classify_new(bronze, "p1", Budget(per_run_usd=5.0, per_month_usd=20.0),
                     include_source_files=(PAPERS_CORPUS,), corpus="papers")
        variant = seen["variant"]
        assert variant is not None, "papers were scored against the announcement prompt"
        assert variant.version == "p1"
        assert variant.prompt.name == "p1.md"
        assert variant.prompt.parent.name == "paper_scoring"
        assert variant.cache.parent.name == "paper_scores"

    def test_announcements_still_take_the_untouched_default_path(self, bronze, monkeypatch):
        """The announcements call must stay byte-identical, because its default
        resolves from the module globals -- the seam tests and one-off scripts
        monkeypatch to redirect a run at a scratch directory."""
        from app.pipeline.budget import Budget
        from app.pipeline.classify import classify_new
        seen = self._spy(monkeypatch)
        bronze.add(m.RawArticle(url="https://lab/a", content_hash="h2",
                                source_file="research/docs/announcements.json",
                                payload={"url": "https://lab/a", "text": "t",
                                         "lab": "deepseek", "date": "2026-08-01",
                                         "title": "A", "text_source": "full_text"}))
        bronze.commit()
        classify_new(bronze, "v9", Budget(per_run_usd=5.0, per_month_usd=20.0),
                     exclude_source_files=(PAPERS_CORPUS,))
        assert seen["variant"] is None

    def test_the_paper_variant_is_not_frozen_at_import(self, monkeypatch):
        """`papers()` is a function for the same reason `announcements()` is: a
        frozen constant ignores a redirected cache and writes into the real
        register."""
        import score_announcements as scorer
        monkeypatch.setattr(scorer, "PAPER_CACHE", Path("/tmp/elsewhere"))
        assert scorer.papers().cache == Path("/tmp/elsewhere")


class TestTheCliSpansBothVersions:
    def test_connect_from_the_cli_does_not_wipe_paper_connections(self, session):
        """`bitcap-db connect` is a documented command and `connect` rebuilds the
        whole table, so passing one version here deletes every paper's holding
        connections and rebuilds announcements only -- silently."""
        import inspect

        from app import cli
        source = inspect.getsource(cli.cmd_connect)
        assert "PAPER_PROMPT_VERSION" in source, (
            "cmd_connect passes a single version; papers lose every connection")

    def test_the_prompt_flag_reaches_the_join_and_the_digest(self):
        """`--prompt v10` classified and transformed at v10 while joining and
        publishing from the module constant, so the new version's rows got no
        connections and never reached a digest."""
        import inspect

        from app.pipeline import worker
        source = inspect.getsource(worker)
        assert "run_connect(\n            session, (prompt_version, PAPER_PROMPT_VERSION)" in source
        assert "session, (prompt_version, PAPER_PROMPT_VERSION), run.started_at" in source


class TestAnExtractionDowngradeIsASystemAlert:
    """Extraction falls back so a page-shape change degrades rather than dropping
    papers. That resilience is also the hazard: `_lead_section` returns the whole
    document when it finds no `<article>`, and arXiv's `/abs/` furniture clears
    the 200-character floor comfortably."""

    def test_a_lab_falling_back_raises_a_system_alert(self):
        got = alerts_mod.extraction_downgraded(None, {}, {"paper_extraction": [
            {"lab": "deepseek", "configured": "blockquote_abstract",
             "actual": "lead_section", "n": 8}]})
        assert len(got) == 1
        assert got[0].kind == "system"
        assert "deepseek" in got[0].subject

    def test_a_lab_using_its_configured_strategy_is_silent(self):
        got = alerts_mod.extraction_downgraded(None, {}, {"paper_extraction": [
            {"lab": "deepseek", "configured": "blockquote_abstract",
             "actual": "blockquote_abstract", "n": 8}]})
        assert got == []

    def test_it_is_registered_so_a_firing_actually_runs_it(self):
        assert "extraction_downgraded" in alerts_mod.RULES

    def test_one_page_shape_change_is_one_alert_not_one_per_firing(self):
        rows = [{"lab": "deepseek", "configured": "blockquote_abstract",
                 "actual": "lead_section", "n": 8}]
        a = alerts_mod.extraction_downgraded(None, {}, {"paper_extraction": rows})
        b = alerts_mod.extraction_downgraded(None, {}, {"paper_extraction": rows})
        assert a[0].dedupe_key == b[0].dedupe_key


class TestTheDashboardCanTellThemApart:
    def test_each_item_says_which_corpus_it_came_from(self, session):
        from api.queries import build_items
        transform(session, "v9")
        transform(session, "p1")
        session.flush()
        # `since=date.min`: this asserts about doc types, not about the display
        # window, and the fixture's dates would otherwise age out of it.
        items = {i["title"]: i["docType"]
                 for i in build_items(session, ("v9", "p1"), since=date.min)}
        assert items == {"Announcement": "announcement", "Paper": "paper"}
