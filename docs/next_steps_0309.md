# Next Steps

## App / Website 

### Deployment

- Setup React page/website/app on Render/Railway. 

- Setup Auth

- Setup CI/CD from GH for page

- Ensure regression tests run before any merge with main

### Design

- Incorporate alerts: recent news / data events with high scores

- Add better score filtering in the app (mechanism TBD)

- Better score search in the app - display events that effect a specific category of investments and display events that effect specific positions; same kind of filtering for the AI events.

- Incorporate a model reliability tab. This should have a timeline of results with the LLM classifier run compared to the gold set so we can see model reliability over time. This test should be run every time the pipeline is run (this, specifically would sit outside of design and in the Data collect / Pipeline section below), however there should also be a button to run it manually. Let's discuss which comparison metrics to show.

# Data collection / Pipeline

- Collect the names of the leaders of the frontier labs; this has to be inserted as part of the pipeline

- Set up X API to scrape X-pages

- Decide on a rule for selecting researchers based on papers - we want to limit the number, but include important researchers. Smaller is better now for this stage of the build.

- Decide on a rule for selecting contributors to GH for the AI labs - we want to limit the number, but include important researchers. Smaller is better now for this stage of the build

- Try to disambiguate / entity resolve at this stage so we don't collect the same things twice for the same people

- For selected researchers, GH contributors and the leaders of the AI labs, search for their blogs and X pages

- Scrape last 3 months of these researchers, GH contributors and leaders and manually examine results + get Fable to give you an overview of what's in there. When saving this data, these people should always be linked back to the lab they come from.

- We need a way to weight these people... however, for now it may just be fine give them a uniform weight which is to be calibrated later.

- Figure out a way to disambiguate articles - it's likely that a researcher and lab might post on something similar and then recording the effect of both of these articles in the frontend just increases the flood of information, giving an unneccessary weight to something by having multiple (potentially relevant) sources related to it. Disambiguation could be done by embedding all the articles and then doing the semantic similarity between them. For articles with a sufficiently high semantic similarity (this threshold would need to be tweaked) we can then pass these to a language model to check if the articles are the same. A precondition for this check is that the person who posted the article / X post would need to be linked to that lab.

- We need an assessment of the effort required to include a new lab in the pipeline. As far as I can tell, for each lab, we need a specific protocol for interacting with their data and finding a) their articles, b) their research papers and c) their github account. Once these have been found, we have a pipeline that is parameterised by this data. 
