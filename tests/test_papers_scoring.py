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
        whose provenance nobody can state. The announcements set is
        human-adjudicated; a model-labelled set is a cross-model proxy and has
        to be reported as one."""
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
