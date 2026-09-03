# Planning Document

## Overview of this document

This document is used for planning the AI Engineer Case Study for Bit Capital. 

## Overview of the case study

### What to build

- A register of labs, tracked by their official channels, the key individuals and also the people a level below them. Who's key and why? Entity resolution across platforms. A personal post and post on the company page is different.

- Ingestion - gather data intelligently, keeping in mind cost, deduplication, freshness, rate limits etc. Thoughtfully scoped beats broad set done badly. 

- Extraction - for each tracked entity (labs, people, attributing to the correct source) extract key contributions over a three month period. Raw content -> structured insights. Every insight must cite its primary source.

- Scoring - both contributors and insights. What are the inputs, why those weights, how do you know it measures what you claim (evaluation!!!) - validate against ground truth, human judgement or proxy. "A simple model you can defend and have tested is exactly what we want."

- Signal vs noise. There's lots of noise in the firehose of information. "The single most important question we'll ask when we open your submission: did it surface something we'd genuinely want to know, and did it keep the noise out."

- Reports and alerts - period digest, with alerts when something material happens. Judgement: what counts as material and for whom? Both outputs must cite sources and be tailored for the audience. **"Actionable" means a reader knows what it means and what to do, not just what happened.**

- Web interface - A small surface to browse the register, see the scored insights and why they were flagged, and read past reports. Configuring what's tracked doesn't need a UI; config in code or files is fine. Keep it light, and don't spend much time polishing.

### The users of the output

#### PMs and Analysts

- What does this intelligence mean for BIT's positions. i.e. how might this effect companies BIT has positions in, but also how might this intelligence challenges BIT's holdings or support them

- How could this intelligence have impacts on other companies in the exposed to AI, e.g. Semi conductors or energy - for example, what was the impact of Deepseek directly on AI companies, but also NVIDEA or energy companies when this new technology promised to reduce computing needs

#### AI Team

- What should be adopted or investigated? What tools, models or innovations are being released that could benefit the AI team?

- This should be technically frames

### The outputs

- An app that they can use, preferably hosted online

- A human written design document, max 10 pages. This should include:
  - the architecture and how data flows through this
  - the key decisions and trade offs
  - model choices for which task and why, fallbacks and the choice of agent over deterministic processes
  - evaluation: how you measured extraction quality, controlled hallucination and validated the scoring with an explanation of a ground-truth approach  
  - cost
  - How I worked - agentic setup, what agents vs. human verified, where the loop breaks
  - What works, what I'd do next, the 3-5 most interesting insights

- Short video demo is a good idea!


## Overview of BIT Capital


## Sources

- Who/what are the frontier labs? (as in, what are their names)

- Who are the people who work here? How do we find this out? Is this through LinkedIn?

- What are the key documents the we can use for identifying information? GH, Arxiv, blog posts, conferences etc.

- What are the sources of these documents and how do we access them? Where do we scrape and where do we use an API for access? With respect to the accessing the sources, how do we include the temporal consideration of information within the past 3 months, but also with using historical information for calibrating the scores?

- If we want to understand the impact of research on BIT's positions, we need to have these positions stored

## Additional considerations

- The purpose of this work is not (just) to understand the first-order impact of advances in the AI space, but to understand the second-order impact on companies that BIT is invested in - or could be invested in 

- It's important to have a good understanding of what these developments mean in the context of companies that BIT is invested in, but I think it's also important or value to use this intelligence to understand additional companies BIT could invest in. 

- The importance of any piece of information needs to be understood within this context. A paper could be novel or, for example, propose a method that wildly reduces the cost of processing video. The goal here is to understand who that information is important to - and whether it's important at all 


## Hypothesis on the solution / high-level sketch

### Thoughts on the solution

- Start this incrementally. Pull a few examples from one frontier lab before thinking of the whole pipeline.

- Develop the context clearly. Start first with BIT's current positions. What the companies they're invested in are. What is the core of these companies' businessess. Develop hypotheses on what is likely to move the needle with regards to their stock - e.g. energy suppliers (data centers), data centre suppliers, customer service etc. Again, start with a small sample before building up the whole thing.

- Be tracking when noteworthy news comes up. Give it a score and a reason for the score, but also say WHY this matters and to which companies or positions in the portfolio. 

- Create a gold-set of posts and scores and compare the results from your scoring to this. Try to understand where things are going awry. Creating a good set here will make a big difference down the line. Don't skimp on this.

- You need to think through how different things in a publication could move the market - and you need some way to justify this. Is there some kind of gold-standard you can develop here looking at past data? Or does this just need to be justified more humanly, by looking at past outputs from these labs and seeing the impact?

- A lab, a person and an article all need to be scored so that we understand that impact of this piece of information. First step is understanding what's important and the second is what to do with this information. In understanding what's important. The scoring shouldn't be too intricate, as per the document, however it needs to defensible. 

- You need a very well sketched out CLAUDE.md to ensure that there's a very well engineered system with abstractions and primitives that would make this easy to extend. This needs to be a key part of the design of the project - designing the building blocks to spec before building the scaleable system.

### Initial steps

- Start with research on BIT. What is the company's goal? How do their investments represent this goal? What are all of their positions? How much is the exposure? What can we do to understand the companies in their portfolio better? e.g. what signals / events drive their growth and what signals / events drive their shrinking? **How can we validate this based on their internal documents**?

- Gather a list of the key frontier AI labs. Come up with a crude prioritisation scoring for them with a language model. Start with the top-scored one. This score will be thrown away later, but the purpose of this is to identify one or two frontier labs to start with the information extraction from and iterate through.

- Build the database related to these first one - three AI frontier labs. This database will include the lab, the key employees and then the layer below that. Each of these entities will be connected to their publications, which include blog posts, social media posts, github, papers, model & system cards, conference talks and technical blogs. 

- Examine this data that you collect. Try scoring one of these org- and people-entities. Experiment here with how you're going to come up with a gold-standard for comparing your results with. Iterate on the scoring model. Keep it simple and justifiable. 

- Start exploring the links between these insights and the few select companies that you've picked from BIT's portfolio. How does this data turn into signal which turns into action.

- Consider a crude App here to look at insights. 

- Start to scale the data on the positions that BIT has. Start to scale the extraction, keeping in mind the need to build this in a modular way. Start to scale the scoring. 

## Design Principles

- Google-style docstrings

- Sources always

- Validation of every module

- Modular design of the system components for reusability 

- Explainability: where it makes sense, when a language model makes a decision, there should be an explanation, preferably with a source; when the coding agent makes a decision, there should be a justification cites; audit-trail: the path that the coding/engineering process takes needs to be recorded so it can be audited later