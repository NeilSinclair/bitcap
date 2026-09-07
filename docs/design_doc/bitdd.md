# BIT Capital Frontier Lab Intelligence Platform

The BIT Capital Frontier Lab Intelligence Platform pulls the latest signals from a series of Frontier AI Labs. The signals are linked with investments currently in the BIT Capital (BitCap) fund, indicating how these signals could impact BitCap positions as well as the strength of these impacts.

### Discussion of the principles of Design

A key design principle for the app was to link announcements to BitCap positions. I did an analysis of BitCap positions across funds and settled on the positions within the Technology Leaders fund as of 30 June 2026. These positions represent roughly 1.5B € or 50% of BitCap's AUM and announcements by AI Frontier Labs are also most likely to influence these tech-oriented positions, acknowledging that there is overlap in investments across funds.

When a source is scored, it links back to a BitCap position through a linked Mechanism (see Scoring below). The user can then see a rated impact of the source on the investment, whether positive, negative or mixed.  

Sources are also scored both for investment and AI Team impact, with some sources having relevance for both user bases and others having relevance for just one. For example, announcements from Labs often had relevance both for the investing team and the AI Team whereas GitHub releases often just had relevance for the AI Team.

### Scoring

The scoring was designed along a series of categories which were linked with BitCap's current holdings. The dimensions are mechanisms, event type (investment team), practices (AI Team) and confidence.

The *event type* answers what kind of event happened, and is the first of the two terms in the investment team score. Events are weighted 5 to 0, for example frontier_model_release (5), corporate_finance (4) and developer_tooling (2).

The *mechanism* category answers how the event transmits to BitCap’s holdings. The mechanism category scores were grouped into Compute and Infrastructure Demand, Efficiency and Displacement, Capability and Demand Shape and Structural, with individual categories under each. These were developed collaboratively with Sonnet, with each mechanism linking to a company and with a polarity in terms of impact. For example, *custom_silicon_substitution* is positive for Micron as their product goes into all accelerators, but negative for Nvidia who would likely lose out from an AI Lab making their own accelerators.

*Practices* are specifically for the AI Team and answer what would we do differently, these include integration, evaluation, model_capability, orchestration and serving efficiency.  These are also paired with an action score indicating whether the team should adopt, investigate or watch the development.

The model was asked to grant a confidence score to the rating which it gave. The confidence was based on the evidence found in the document. For every document it scored, the LLM had to support the evidence with a quote. The quotes were then checked by a deterministic process to ensure that the model was not hallucinating a quote. The check requires the quote to appear verbatim in the article text, and a tag whose quote is not there is dropped rather than downgraded, so it cannot contribute to a score at all. The quote is shown next to the tag on the item detail page and in the digest, so a reader can search for it in the original and find it.

The confidence scores were multiplied into the overall score, such that low = 0, medium = 0.5, high = 1.0. The multiplier for low was selected empirically based on looking at scored articles where the quoted evidence was weak. Medium was selected as 0.5 to indicate uncertainty and high a 1.0 to indicate certainty.

- To get an investment team score we take the event score multiplied by the magnitude and confidence of the highest scoring mechanism and normalise.

- To get an AI team score we take the action score and multiply it by the practices score times the confidence and normalise.

In both cases we take the highest scoring tag rather than the sum of them, so an item firing several weak mechanisms does not outrank one with a single strong path to a holding. 

There are separate scoring prompts for the sources. The announcements (articles) and GitHub releases share a scoring prompt (they share the same shape), papers and X posts each have their own. Each prompt is versioned. Prompts were written by Fable 5. In the case of the announcements, the prompts are compared against a model-scored gold set. This serves to test the prompt's agreement with this set, but also to measure variance in the results over time. This second point is discussed further in the *System Health* section. The gold set is not human-labelled: its labels were created by Fable 5, deliberately a different model from the Sonnet scorer it grades. The reason for this choice was based purely on time constraints. Given more time I would have either manually labelled these data sources. An iterative discussion with Claude on the differences between the results on the Fable-labelled gold set and the Sonnet 5 labeler gave me reasonable confidence in the robustness of Fable's scoring.

All sources are re-scored when a prompt relevant to that source type is updated to a new version.

Most of what is collected scores zero: 348 of 447 announcements and releases, 33 of 47 papers and 210 of 238 X posts. This is not on accident as the purpose of the pipeline is to remove noise. 

Although different sources are not weighted differently, X posts generally score lower, because most of them are not about an event at all. Of the 210 posts scoring zero, 194 carry no mechanism tag, and 119 were classified as event type other, which is commentary and advocacy rather than something with a path to a holding. However, a post with real signal still reaches the top: the highest scoring item anywhere in the system is a single post from OpenAI's Mark Chen committing to 4+ GW of NVIDIA capacity, which carriers clear investment signal.

## Model selection

To score the sources, namely lab announcements, github releases, X-posts and papers, Sonnet 5 was used. Sonnet 5 was compared against Haiku 4.5 and GPT5-mini on the gold set of announcements over three runs. Haiku was removed because it consistently scored an important article 0. GPT5-mini was removed because of high variances with its results across runs.

A scoring drift report is run nightly (see Pipeline) and can be run on command to compare the results from the lab announcement scoring model with the gold set. I acknowledge that there is variance on these results from run-to-run and that the current score, generally around 0.85 Micro-F1 could be improved. I tested a version of the scorer that runs three times on each article and scores based on the majority label with both Sonnet 5 and GPT5-mini. Sonnet 5 performed well, but the variance was very high with GPT5-mini making a three way vote for this model infeasible. This cost of running three source classifications was, however, too high in development because I was regularly rescoring the whole 3 months of the corpus. If I had more time, I would have moved over to this scoring method for the sources.

Due to the length of the announcements being scored, I tried to see if it was possible to first summarise them and then score them. I tested summarisation with Sonnet 5, Haiku 4.5 and GPT5-mini, however there was significant degradation of the results on the gold set and the token saving was small – less than 20% per announcement / article on average. This was due partly to the length of the scoring prompt instructions. A significant cost saving (~50%) was achieved instead by applying prompt caching to this scoring prompt first and then running the scoring.

For choosing which Frontier AI Lab repos might be relevant to the AI team, the repos are classified using GPT5-mini. GPT5-mini was chosen over Haiku 4.5 for this task based on the results on a set of Fable 5 labelled repos, labelled for significance. The Fable 5 labels were validated by manually comparing them to a subset of 20 items from this list that I had hand scored; human agreement with Fable was 95%. Sonnet 5 was not tested for this task because the results with GPT5-mini were satisfactory and it is 1/8th the cost of Sonnet 5. Note this was a filter stage. Once filtered, the repo releases were then scored by the Sonnet 5 scorer.

## Sources

### Labs selected

From an intial list of 21 labs 7 were selected. This initial list was created through AI research where the labs were placed into four buckets, Closed Frontier, China, Western Challengers, and Stealth/New Modality. Meta AI entered Closed Frontier due to their move away from open weights models recently. 

I selected all of the Closed Frontier labs namely OpenAI, Anthropic, Google Deepmind, Meta AI, xAI; DeepSeek from China; and Mistral from Western Challengers. The Closed Frontier labs were selected because of their size and impact on the market. DeepSeek was selected because of the impact of DeepSeek R1 on the market (-17% of Nvidia's share price a week after the launch). Mistral was chosen because of a hypothesised impact it could have on driving European-based AI development, including Data Centre build out. 

A second reason for chosing 7 labs was to limit the size of the sample in order to lower costs and make development more managable in the time frame. In a second phase of the project I would include additional labs.

### Lab announcements

Announcements are discovered per lab and the method differs because the sites do. Every entry is a config change in `config/sources.yaml`, but the code for extraction is the same for each.

| Lab | Channels | Why |
|---|---|---|
| Anthropic | Sitemap | No RSS feed exists at any path tried |
| OpenAI | RSS + model index + dev forum (3) | Cloudflare serves XML but 403s HTML, so discovery is RSS; the other two were added after RSS alone missed the GPT-6 Astra launch |
| DeepSeek | Sitemap | Release notes sit on the API docs site; slugs encode the date |
| Google DeepMind | RSS | The sitemap is not a blog index — it carried 12 articles to RSS's 30, missing nearly every model launch |
| Mistral | RSS | Feed is English-only with a clean pubDate; the sitemap duplicates every item under /fr/ |
| Meta AI | Blog listing + Newsroom RSS (2) | No blog RSS and the sitemap is crawler-gated; the Newsroom carries the data-centre and capex news the AI blog never does |
| xAI | Internet Archive (CDX) | Live x.ai is fully Cloudflare-blocked, including sitemap.xml itself |

### Papers

Papers have no shared discovery method and each lab's harvester/scraper is different because the labs publish differently. Once the papers have been downloaded, they are processed in the same way.

| Lab | How papers are found |
|---|---|
| DeepSeek | arXiv query `au:"DeepSeek-AI"`, plus a short extra-titles list |
| Google DeepMind | Sitemap discovery, then LLM byline extraction off the linked arXiv page |
| Meta AI | Listing 500s, so via ai.meta.com/results/ with the arXiv id resolved from the title |
| Anthropic | Deterministic parser, with an LLM fallback left off by default |
| OpenAI | A hardcoded list — OpenAI publishes no papers index that can be enumerated |
| Mistral | No publications page exists, so candidate titles are mined from Mistral's own announcements corpus |
| xAI | Not covered. arXiv has no affiliation search field, and every query for xAI or Grok collides with eXplainable AI, grokking, or third-party papers *about* Grok. Checked twice live |


### Github

The GitHub pages of the selected labs were found by research with Claude and included in the GitHub sourcing config file. Relevant repos were then selected such that forks, archived repos and anything not pushed to in the window were dropped first. Of the remaining 770 repos we ranked them by the number of stars on them and chose the top 10 from each lab. These were then passed to a classifier to pick which ones were relevant for our project (see Model Selection). Releases from only the selected repos were then scored using the LLM classifier in the same manner as lab announcements are scored.  

The GitHub data collected also includes all of the people who made commits during the time period. The initial idea was to use these people lists to explore the person blogs and X accounts of these people. This was abandoned for now due to the sparsity of X accounts and personal blogs for the contributors as well as costs of the X API. Given the data is available, this could be explored in a second stage of the project.

### X Posts

X posts were sourced only from the leaders of the Frontier AI Labs, where these leaders were sourced and verified through a research process with Claude. The LLM researcher was instructed to find two sources corroborating their leadership position. When their X-posts were linked to their profiles (i.e. position noted in the profile), this was amended to their profiles as a validating source.

An LLM-based online research showed that Elon Musk would have posted approximately 2500 - 3000 posts over the 3 month data collection period. Therefore, in the spirit of saving costs, his posts were excluded.

Posts were extracted using the X-API for leaders who have posted in the past three months. The posts were then scored with a separate X-specific prompt with Sonnet 5.

## Pipeline

The pipeline is maintained by a yaml file indicating the cadence, cost limits, back-off and retry parameters, and the relative frequency of certain runs for certain sources.

Render.yaml declares the pipeline as a cron service whereby it is run on schedule every morning at 5am Berlin time. It can also be run manually in the Pipeline tab of the tool. When running manually, the user can select to just run specific legs of the pipeline. Only one pipeline run can be run at a time. 

The pipeline runs in eight phases in a fixed order. There’s also a per source failure isolation, so if one of the sources breaks, the others can still be processed. 

![](media/image1.png)
*Figure 1 The stages of the ETL Ingestion Pipeline*

The data is processed in a medallion architecture. The Bronze layer is updated whenever new data is added to the pipeline when the pipeline is run for one or more of the parts. The Silver layer is then processed deterministically. For example, the deterministic scoring algorithm (but not the LLM labels the scores are based on) can be adjusted and the Silver Layer rerun without having to reprocesses the Bronze layer. The Gold layer brings together the company (BitCap) holding data with the data from the Silver layer to create the objects on the UI.

![](media/image2.png)
*Figure 2 The Medallion architecture*

### Grouping

Articles covering the same event are grouped so the Dashboard feed shows one row per event rather than one row per source. Three deterministic passes run first: exact matches on lab, date and title, release trains from a single repo, and a requirement that the event type matches before anything can merge at all. The remaining pairs are compared by embedding, and only those in a narrow cosine band are sent to an LLM to decide.  This is because on the pairs I labelled the cosine score does not separate duplicates from near misses cleanly enough to cut at one threshold. Pairs at or above 0.80 merge unasked, pairs below 0.70 stay separate, and the model is only asked in between. Grouping has not been implemented for X posts due to time constraints.

### Alerts Digest

The digest in the Alerts tab is a daily edition of the highest scoring items, rendered separately for the investment team and the AI team from the same underlying data. The landing view is a rolling seven day preview, so it is never empty on a quiet day. When the pipeline runs each night, it creates a published set of articles from the past 24 hours which the user can then investigate by selecting it in the drop down menu at the top of the Alerts - e.g. 'Published 6 Sept - 1d - 1 of 9' shows the key alert for the 24 hour period across 6 September. This would enable a user to open the tool in the morning and select the digest to see the previous day's alerts from sources. Only high scoring sources are shown here.

### Pipeline alerts and failures

The pipeline runs unattended overnight, so it is built to fail in a way one can see.

- Fetches retry four times with exponential backoff and are rate limited per host. Each source keeps its own watermark and failure count, so one source breaking does not reset or block the others.

- Re-runs are safe and cheap. The work list is whatever the current prompt version has not scored yet, read from the database, so a second run in the same night does nothing to already scored sources. Cost ceilings per run and per month stop further calls and finish with what they have rather than failing and discarding an ingest that already happened. Only one run can happen at a time, enforced by the database rather than a flag.

- System alerts are kept separate from content alerts. A content alert says the pipeline found something, a system alert says the pipeline itself is broken, for example a source failing three runs in a row or model-labelled gold set agreement dropping below 0.80.

### System Health

The pipeline run can be checked in the Health tab in the app. It indicates which parts of the pipeline ran with and without errors. The user can reset the errors with a button at the bottom of the page.

There is also a drift check which compares the model’s output to a gold set of scored example annoucements. This drift reports two things. One, the latest agreement of the scoring model on the gold set and two, how this drifts over time. The drift over time serves as a proxy for the variability of the scoring model's outputs. If the score drops below 0.8 Micro F1 (essentially the average weighted F1 across the sources), it triggers a health alert.

## Costs

Every LLM call records its model, tokens and cost at the call site as it runs, so the figures below are what was charged rather than an estimate made afterwards. Based on these, the cost of scoring the full pipeline for the three-month period is roughly ~13.50€, with the X posts pulled from the limited list of leadership members costing ~3€ over the same period.

The costs for each daily pipeline run are ~1€. This includes the cost for the drift checker at ~0.80€.

## Development cycle

I started off with a planning document based on the case-study and sketched out the high-level steps for the project. The key points from this document were included in the CLAUDE.md file governing the sessions. I clearly stated in the planning document that every decision needs to be recorded in a decisions document. I also indicated that unit tests must be written for everything the agents do.

Each part of the pipeline started with an interactive research session with a Claude agent. Sources were discovered and scripts built for extracting them. These were then built into a repeatable pipeline once they had been validated.

I ran different agents in different work-trees simultaneously to work on different features. Along with tracking all key decisions in the decisions document, the agents also wrote short handover documents for other agents once a larger feature was complete. This helped the agents to maintain context throughout the project as it developed.

I cycled in a loop of using planning mode to define a feature to be worked on, then having the agent write the code, pass it to a code reviewer agent, make the changes, often call the code reviewer again, make final changes and then submit a PR.

As part of the CI process, a suite of unit tests are run that must be passed for every push and PR on Github. The app is deployed to Render. Whenever a merge happens on the `deployment` branch, the app is deployed to Render.

## Security Analysis

A security analysis of the code base was conducted using GPT-6 Astra in Codex. Three vulnerabilities were found, as listed in Codex:

- Medium: The unauthenticated /api/health endpoint exposes internal operational details, including pipeline errors, source information and processing state.

- Medium: A race condition in login rate limiting allows concurrent requests to exceed the intended attempt limit increasing password guessing and potentially exhausting server resources.

- Low: When the local Docker Compose database is running, someone who can reach your computer over the network on port 5432 — subject to firewall rules — could use the committed superuser password to read, modify or delete the database in Docker.

These concerns are acknowledged, however the risk appears to be low given all of the information is public and the database could be reconstructed easily in the third risk identified. This also just affects the local Docker container the Postgres DB is running in. If I had more time I would have, however, fixed these issues.

## Insights

- Mark Chen of OpenAI on X, 17 August: "We're excited to go big with NVIDIA and sign up for 4+ GW of capacity", scored 100 as a compute commitment, naming a holding and a gigawatt figure which has impact for chip and power providers (i.e. IREN).

- Meta's venture with BlackRock to build a 1 GW data centre in El Paso at roughly $14 billion in development costs, also scored 100 which has impact for chip and power providers (i.e. IREN).

- Although Astra was announced on September 3rd, there was an earlier announcement on September 1st about assessing Astra's cybersecurity capabilities and the safeguards required which provided signal that a new model would be released soon.

- The model scored the annoucement of OpenAI's Jalapeno accelerator highly, noting the positive impact to numerous accelerator component manufacturers whose components would likely still be used in this accelerator, but a negative impact on Nvidia who potentially lose market share from a lab's own accelerator.

## Next Steps and Improvements

- Incorporate additional labs into the data. I would start off with incorporating additional labs from China into the sample.

- Update the scoring LLM to do three scorings of each source and choose the most often occuring label. In the event of a tie, take the median of the three runs' event weights, rounding down. Do the same reduction applied to a mechanism's magnitude and confidence. Flag the item as contested rather than resolving it silently. 

- The grouping of articles together is not currently functioning on the alerts digest as it functions on the dashboard. With additional time, I would include this feature. 

- The tweets are currently not being grouped either and there is some overlap. I would apply the grouping process to these too in a future release.

- Create a Watch List of companies for the UI where the user is alerted when any intelligence surfaces that impacts these companies. This would serve as signal to potentially invest in these companies.

