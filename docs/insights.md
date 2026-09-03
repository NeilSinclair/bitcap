# Insights

Real things the system surfaced, captured as they appeared. 3–5 go in the final doc.

---

**BIT did not hold Micron through the memory downturn — they rebuilt at the
trough.** The 13F shows 22 consecutive quarters of "holding", but through 2022 it
was 4,000 shares worth $0.2m (0.04% of the book). The real position was built in
Q1 2023 near the memory-cycle low and has been material since. In Q2 2026 they
cut shares 40% while the weight still rose — trimming into strength. That reads
as cycle discipline, not a structural AI holding, and it changes which
transmission mechanism they are actually trading (`ai_capex_investments`, not
`memory_intensity_up`).

**BIT's own audited report contains an entity-resolution failure.** Page 40 lists
ISIN `AU0000185993` twice — "Iris Energy Ltd." at 5.60% and "IREN Ltd." at 3.99%,
the same company either side of a rename. The ISIN is stable; the name is not.
A live argument for identity-by-identifier in our own register.

**Name turnover averages ~20% a quarter and hit 50.8% in Q2 2026.** Anything we
build against the disclosed book is describing a portfolio that has already
moved. Argues for mechanism-level output over name-level recommendations.

**BIT's biggest positions are pinned by regulation, not conviction.** Measured
against each fund's own NAV, all six funds sit just inside the UCITS 5/10/40
limits — largest position anywhere 10.43%, over-5% bucket peaking at 37.1%
against a 40% ceiling. The aggressive trimming of Micron and IREN into strength
reads as compliance rather than a view change. If so, the intelligence product's
job is not to rank conviction but to say which fund has headroom and what a
purchase displaces.

**The same analysis reverses sign depending on the window.** Over four quarters
BIT look like relentless sellers of their top names; over twelve they are net
accumulators of the same names — IREN shares +57%, Amazon +479%, TSMC +9,579%.
They build for years, then cut hard once a name goes vertical. Any scoring input
derived from position changes has to state its horizon or it is meaningless.

**Four frontier labs were invisible to the product until the ceiling replaced
the off-switch.** xAI, Google DeepMind, Mistral and Meta AI had their
announcements fetched months ago and sitting in the corpus — 61 articles — but
none were ever classified, because scoring was deactivated wholesale (D10) as a
guard against an uncontrolled LLM run. The register said seven labs; the
dashboard could only see three. Classifying them cost $1.08 and moved the
corpus from 175 scored articles to 236, mechanism tags from 101 to 141, and
holding connections from 612 to **809**. The lesson is not about the labs — it
is that a safety switch with no bounded alternative quietly becomes a coverage
gap that looks like a completed register.

**One xAI announcement fans out to seven holdings on a single sentence.**
"Grok 4.5 was trained across tens of thousands of NVIDIA GB300 GPUs" fires
`training_compute_up` at strength 0.67 against NVIDIA, TSMC, Micron, Marvell,
Amazon, IREN and TeraWulf simultaneously — the accelerator, the foundry, the
memory, the interconnect, the cloud and two power/datacenter names, each with
the same verbatim quote as evidence. That is the mapping layer doing exactly
what it was built for: a private lab's model release priced through to seven
public positions. It is also the clearest noise risk in the product — one
sentence producing seven digest rows — and the argument for grouping a digest by
*event* rather than by connection.

**The register found four possible researcher moves without being asked to.**
Building the people register surfaced four names appearing on papers at two
different labs inside the same three-month window — Jeffrey Wu (OpenAI and
Anthropic), Yonglong Tian (OpenAI and Google DeepMind), Jason Chen (OpenAI and
Meta AI), Yao Li (OpenAI and DeepSeek). Researcher departures are what the brief
calls top-tier signal, and nothing in the system produced them before. They fall
out of the register precisely *because* it refuses to merge people across labs:
had the design silently glued matching names together, each of these would have
become one tidy row and the signal would have been destroyed by the cleanup.
They are candidates, not confirmed moves — same name is not same person — but a
candidate list of four is a tractable thing to check by hand.

**The people register is 4,927 people and most of them do not work at the labs.**
Of 6,415 evidence rows, 1,996 are `unknown` — a GitHub account that touched a
lab's repo with no evidence of employment. `shoemoney` has one or two commits
across five different labs; `winklemad` the same. Drive-by open-source
contributors, not staff. Only 630 rows are `confirmed` (a commit from a
lab-owned email domain) and 583 `confirmed_org_wide` (a parent-company domain
like @google.com, which covers all of Alphabet). The lesson for the design doc:
a raw contributor count is nearly meaningless as a measure of a lab, and any
figure quoted has to carry its evidence tier or it is just repo popularity.
