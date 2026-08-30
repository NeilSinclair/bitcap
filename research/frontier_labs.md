# Frontier AI Labs — starter register

**Status:** v1, re-researched 2026-08-26. Every claim carries a fetched citation.
A starting point for the register, not the register itself. The register cut (which labs get full
individual coverage vs org-channel-only) is decided later on observed volume, per
[planning.md](../docs/planning.md) §7.

## Sourcing rule

**No claim in this document comes from model knowledge.** Every fact was fetched and is linked at
the entry that states it. Where nothing could be sourced, the field is omitted or says
*not sourced* — it is never filled from memory.

Two caveats that must travel with this file:

1. **Wikipedia is a tertiary source.** It is resolvable and citable, which clears this document's
   bar, but it does not clear the project's *primary source* non-negotiable. Facts destined for the
   design doc or for a shipped insight need promoting to the underlying filing, company post, or
   press report. Wikipedia's own inline references are the shortest path.
2. **Assistant knowledge ends May 2026.** Everything dated after that is sourced or absent.

⚠ marks a fact where **sources genuinely disagree**. It is not a confidence hedge — it means two
citations conflict and both are shown.

**Collection limits.** Reuters, FT, AP and WSJ are unreachable by the fetcher, so press citations
lean CNBC, Bloomberg, SCMP, Fortune, TechCrunch and Axios. Employee counts carry the "as of" date
their source gives; they are not all contemporaneous.

---

## What changed from v0

The previous version asserted founding dates, headcounts and locations from memory. Re-researched,
**seven of those were wrong**:

| Fact | v0 (from memory) | Sourced |
| --- | --- | --- |
| xAI founding | July 2023 | **9 March 2023** |
| OpenAI headcount | ~7,850 | **4,500** (2026) |
| Anthropic headcount | ~5,000 | **2,500** (2026) |
| Mistral headcount | ~350 | **1,000+** (2026) |
| Mistral ASML round | €1.7bn Series C at $13.8bn | **Sept 2025, €2bn at €12bn**, ASML 11% ⚠ |
| SSI valuation | $32bn | **$30bn** (March 2025) |
| Meta AI HQ | Menlo Park | **Astor Place, New York City** |

Two previously-unverified items resolved as **true**: the SpaceX IPO, and the Cursor acquisition
(completed 14 Aug 2026, $60bn). One resolved as **false**: DeepSeek's "$71bn valuation" — the
sourced figure is $52bn.

---

## Tier 1 — Closed frontier, scaling-led

### OpenAI
Founded **8 December 2015** by Elon Musk, Sam Altman, Ilya Sutskever, Greg Brockman, Trevor
Blackwell, Vicki Cheung, Andrej Karpathy, Durk Kingma, John Schulman, Pamela Vagata and Wojciech
Zaremba. HQ 1455/1515 Third Street, San Francisco. **4,500 employees** as of 2026.

- **Structure:** founded nonprofit; capped for-profit from 2019; **restructured October 2025 into
  OpenAI Group PBC**, with the nonprofit renamed OpenAI Foundation. Foundation holds 26%, Microsoft
  27%, employees and investors 47%.
- **Funding:** the round closed **31 March 2026 at an $852bn post-money valuation on $122bn of
  committed capital** — announced in February at $110bn against a $730bn pre-money, comprising
  $50bn from Amazon and $30bn each from Nvidia and SoftBank, and later including $3bn from retail
  investors. Revenue stated at $2bn/month.
- **Models:** GPT series (to GPT-5.6), o1/o3 reasoning models, ChatGPT, Codex, DALL·E, Whisper.
  Sora discontinued March 2026.
- Channels: `openai.com/research`, `openai.com/index`, model and system cards, `github.com/openai`.

Sources: [Wikipedia — OpenAI](https://en.wikipedia.org/wiki/OpenAI) · [CNBC — round closes at $852bn](https://www.cnbc.com/2026/03/31/openai-funding-round-ipo.html) · [Bloomberg — $852bn after $122bn round](https://www.bloomberg.com/news/articles/2026-03-31/openai-valued-at-852-billion-after-completing-122-billion-round) · [CNBC — Feb announcement, Amazon/Nvidia/SoftBank](https://www.cnbc.com/2026/02/27/open-ai-funding-round-amazon.html) · [Bloomberg — $110bn at $730bn](https://www.bloomberg.com/news/articles/2026-02-27/openai-finalizes-110-billion-funding-at-730-billion-valuation) · [TechCrunch — retail investors](https://techcrunch.com/2026/03/31/openai-not-yet-public-raises-3b-from-retail-investors-in-monster-122b-fund-raise/)

### Anthropic
Founded **26 January 2021** by Dario Amodei, Daniela Amodei, Jared Kaplan, Jack Clark, Chris Olah,
Ben Mann, Sam McCandlish and Tom Brown. HQ 500 Howard Street, San Francisco. **2,500 employees**
as of 2026.

- **Models:** Claude in four tiers — Haiku, Sonnet, Opus, Fable — plus Claude Code, Claude Cowork,
  Claude Design, Claude Science, and Claude Mythos (restricted to partnered US organisations).
- **Published research:** Constitutional AI (training an LLM against a written set of principles),
  mechanistic interpretability including dictionary-learning feature extraction, and work on
  alignment and societal impact. The interpretability stream is a differentiated, high-signal
  publication channel.
- **Funding:** $13bn Series F September 2025 at $183bn post-money; $30bn Series G February 2026 at
  $380bn post-money. Later 2026 marks circulated by aggregators (up to $965bn) could not be
  sourced to press and are **excluded**.
- Channels: `anthropic.com/research`, `anthropic.com/news`, `transformer-circuits.pub`,
  `github.com/anthropics`.

Sources: [Wikipedia — Anthropic](https://en.wikipedia.org/wiki/Anthropic) · [Sacra](https://sacra.com/c/anthropic/) · [PitchBook](https://pitchbook.com/profiles/company/466959-97)

### Google DeepMind
DeepMind incorporated **23 September 2010**, launched **15 November 2010**, by Demis Hassabis,
Shane Legg and Mustafa Suleyman. Acquired by Google **26 January 2014** for a reported $400–650m.
**Merged with Google Brain April 2023.** HQ London. **~6,000 employees** (2025).

- **Contributions:** AlphaGo beat Lee Sedol 4–1 in March 2016; AlphaFold set state-of-the-art on
  protein-folding benchmarks in 2020 and had released over 200 million predicted structures by
  July 2022; Gemini released 6 December 2023, **Gemini 3 Pro on 18 November 2025**.
- **August 2026 leadership change:** Demis Hassabis ceded the CEO role to become chairman of Google
  DeepMind and chief scientist at Alphabet; **Koray Kavukcuoglu** took over the frontier push.
  Reported against model delays, low morale and senior departures, with Gemini 3.5 Pro said to have
  missed three release deadlines.
- Leadership churn plus slipped releases at one of three Western frontier labs is itself a
  high-value register entry.
- Channels: `deepmind.google/discover/blog`, `research.google`, arXiv, model cards.

Sources: [Wikipedia — Google DeepMind](https://en.wikipedia.org/wiki/Google_DeepMind) · [CNBC — Kavukcuoglu](https://www.cnbc.com/2026/08/12/google-deepmind-koray-kavukcuoglu.html) · [Time — reshuffle](https://time.com/article/2026/08/06/google-deepmind-ai-demis-hassabis/) · [Axios](https://www.axios.com/2026/08/06/googles-ai-leadership-shuffle) · [Fortune — delays and exits](https://fortune.com/2026/08/10/how-stalled-models-missed-deadlines-and-staff-burnout-lead-to-the-unraveling-of-googles-deepmind/) · [Axios — morale](https://www.axios.com/2026/07/23/googles-deep-mind-ai-model-race)

### Meta AI / Meta Superintelligence Labs (MSL)
Founded **2013** as Facebook AI Research (FAIR) by Rob Fergus, Yann LeCun, Serkan Piantino and Mark
Zuckerberg. LeCun directed FAIR until 2018, succeeded by Jérôme Pesenti. HQ **Astor Place, New York
City**. Headcount not stated by the source.

- **MSL formed 2025**, reorganising FAIR, LLM development and other AI research and product groups
  under one umbrella. **Alexandr Wang** is Meta's first chief AI officer and leads MSL, appointed as
  part of a **$14.3bn investment in Scale AI**; he also heads the **TBD Lab** unit. **Shengjia Zhao**
  (ex-OpenAI) named chief scientist July 2025.
- **October 2025 reorg** cut roles across FAIR, product AI and AI infrastructure while sparing
  TBD Lab. MSL headcount reported at ~1,300 — ~600 foundation modelling, ~400 applied, remainder
  long-horizon research and infrastructure — via The Information, secondary.
- **Llama released February 2023**; most recent Llama release is Llama 4 as of January 2026.
  **Muse Spark** debuted April 2026 as Meta's first major model since the Wang deal, with a 1.1
  update in July 2026; a more powerful model code-named **Watermelon** is in training. Reporting
  describes a shift away from open weights toward proprietary models.
- Directly relevant to the `open_weights_release` mechanism: the largest Western open-weights
  publisher stepping back.
- Channels: `ai.meta.com/research`, `ai.meta.com/blog`, `github.com/facebookresearch`, arXiv.

Sources: [Wikipedia — Meta AI](https://en.wikipedia.org/wiki/Meta_AI) · [CNBC — Zuckerberg's MSL memo](https://www.cnbc.com/2025/06/30/mark-zuckerberg-creating-meta-superintelligence-labs-read-the-memo.html) · [CNBC — Shengjia Zhao](https://www.cnbc.com/2025/07/25/zuckerberg-shengjia-zhao-meta-ai-lab-chief-scientist-openai.html) · [Axios — TBD Lab reorg](https://www.axios.com/2025/10/22/meta-superintelligence-tbd-ai-reorg) · [CNBC — Muse Spark debut](https://www.cnbc.com/2026/04/08/meta-debuts-first-major-ai-model-since-14-billion-deal-to-bring-in-alexandr-wang.html) · [Fortune — Muse Spark 1.1](https://fortune.com/2026/07/09/meta-muse-spark-1-1-release-alexandr-wang-superintelligence-labs-mark-zuckerberg/) · [CNBC — one year of Wang](https://www.cnbc.com/2026/06/14/meta-hired-alexandr-wang-to-build-ai-its-zuckerbergs-job-to-sell-it.html) · [Built In](https://builtin.com/artificial-intelligence/meta-superintelligence-labs) · [Implicator — 600 cuts](https://www.implicator.ai/meta-cuts-600-ai-roles-to-speed-its-superintelligence-push/)

### xAI → SpaceXAI — **listed via SpaceX (Nasdaq: SPCX)**
Founded **9 March 2023** by Elon Musk. HQ 1450 Page Mill Road, Stanford Research Park, Palo Alto.
**1,200+ employees** as of 2025.

- **SpaceX acquired xAI on 2 February 2026** in an all-stock transaction structuring xAI as a
  wholly owned subsidiary — SpaceX valued at $1tn, xAI at $250bn, combined $1.25tn, the largest
  merger on record. Stated rationale: orbital data centres. **Rebranded SpaceXAI in July 2026.**
- **SpaceX IPO'd on Nasdaq (SPCX) 12 June 2026** — 555.6m shares at $135, raising $75bn at a
  $1.77tn valuation, the largest IPO on record. Closed the debut at ~$161 (+19%), market cap above
  $2tn; added a further ~20% the next session.
- **SpaceX completed its acquisition of Cursor (Anysphere) on 14 August 2026 for $60bn.**
- All original xAI co-founders had departed by early 2026; Musk announced a re-org 11 February 2026.
- **Products:** Grok, Grokipedia, Grok Build, Grok Bot, X, Cursor, the Colossus supercomputer, and
  a data-centre business.
- **This makes xAI the first frontier lab inside a listed US issuer.** A lab-level event now has a
  direct ticker with no mapping layer in between — the same structural case as Zhipu and MiniMax.
- Channels: `x.ai/news`, `x.ai/blog`, model cards, `github.com/xai-org`.

Sources: [Wikipedia — xAI/SpaceXAI](https://en.wikipedia.org/wiki/XAI_(company)) · [CNBC — IPO takeaways](https://www.cnbc.com/2026/06/12/spacex-ipo-spcx-live-updates.html) · [CNBC — $75bn raise](https://www.cnbc.com/2026/06/11/spacex-raises-75-billion-in-record-setting-ipo-ahead-of-nasdaq-debut.html) · [CNBC — $2tn market cap](https://www.cnbc.com/2026/06/12/spacex-stock-jumps-2-trillion.html) · [Bloomberg — day two](https://www.bloomberg.com/news/articles/2026-06-15/spacex-shares-rise-set-to-extend-gains-after-record-ipo-mqexlibz) · [TechCrunch — IPO close](https://techcrunch.com/2026/06/12/spacex-ipo-closes-up-19-and-delivers-the-worlds-first-trillionaire/) · [CNBC — merger](https://www.cnbc.com/2026/02/02/elon-musk-spacex-xai-ipo.html) · [CNBC — Cursor acquisition](https://www.cnbc.com/2026/06/16/spacex-spcx-cursor-acquisition-ipo.html) · [CNBC — re-org](https://www.cnbc.com/2026/02/11/musk-announces-xai-re-org-following-key-departures-spacex-merger.html)

---

## Tier 2 — China frontier

Two of these are **listed equities with real tickers**, which makes them directly investable rather
than only a transmission mechanism. xAI is a third, via SpaceX.

### DeepSeek
Founded **17 July 2023** by Liang Wenfeng, owned by hedge fund **High-Flyer** (founded February
2016). HQ Hangzhou, Zhejiang. **160 employees** as of 2025 — small enough that headcount is a poor
capability proxy, which is worth remembering before any scoring input uses it.

- **DeepSeek-R1 launched 20 January 2025 under the MIT licence** — the canonical
  `inference_cost_down` shock and the calibration event named in the project brief. It passed
  ChatGPT as the most-downloaded free iOS app in the US by 27 January 2025, and was reportedly
  trained at substantially lower cost than comparable Western models.
- V3 December 2024; **V4 preview 24 April 2026**.
- **Series A May 2026: $7bn raised at a $52bn post-money valuation.** The "$71bn" figure that
  circulated is **not supported** — an April 2026 discussion of a $300m round is the other sourced
  data point.
- Channels: `api-docs.deepseek.com`, `github.com/deepseek-ai`, Hugging Face, arXiv.

Sources: [Wikipedia — DeepSeek](https://en.wikipedia.org/wiki/DeepSeek) · [Wikipedia — High-Flyer](https://en.wikipedia.org/wiki/High-Flyer) · [Wikipedia — Liang Wenfeng](https://en.wikipedia.org/wiki/Liang_Wenfeng) · [Fortune — Liang on working practices](https://fortune.com/2026/08/01/deepseek-founder-liang-wenfeng-workers-dont-do-overtime-or-kpis-china-996-culture/) · [ChinaTalk](https://www.chinatalk.media/p/deepseek-from-hedge-fund-to-frontier)

### Alibaba Qwen (Tongyi Lab)
Developed by **Alibaba Cloud**. Beta April 2023; public release September 2023 after regulatory
clearance.

- **Predominantly open weights**, across Apache 2.0, a source-available Qwen Licence, a
  non-commercial Qwen Research Licence, and proprietary large versions via Alibaba Cloud. This mix
  matters: "open weights" is not one thing, and the licence determines whether a release actually
  enables self-hosting.
- Release cadence: Qwen2 June 2024, Qwen2.5 September 2024, Qwen3 April 2025, Qwen3.5 February
  2026, Qwen3.6 April 2026, Qwen3.7 May/June 2026, Qwen3.8 August 2026. Most recent:
  **Qwen3.8-Max, 2.4tn parameters, 3 August 2026**; Qwen3.8-27B, 14 August 2026, Apache 2.0.
- Channels: `qwenlm.github.io`, `github.com/QwenLM`, `huggingface.co/Qwen`, arXiv.

Sources: [Wikipedia — Qwen](https://en.wikipedia.org/wiki/Qwen)

### Zhipu AI / Z.ai — **listed, SEHK: 2513**
Founded **2019** by **Tang Jie and Li Juanzi**. HQ Beijing. **800+ employees** (2024).
Legal name Z.AI Co., Ltd., formerly Beijing Zhipu Huazhang Technology, also known as **Knowledge
Atlas Technology Joint Stock Co., Ltd.**

- **IPO'd on the Hong Kong Stock Exchange 8 January 2026** — the first major Chinese LLM company to
  go public. Raised ~$560m; closed the debut up 13.1%. Market cap reported at HK$585.8bn by
  mid-2026, roughly 2.7× MiniMax's. Preparing a Shanghai listing as of June 2026.
- **Models:** GLM series, through GLM-5.3 and vision-language variants.
- Channels: `z.ai/blog`, `github.com/THUDM`, `github.com/zai-org`, Hugging Face, arXiv.

Sources: [Wikipedia — Z.ai](https://en.wikipedia.org/wiki/Zhipu_AI) · [CNBC — Zhipu debut](https://www.cnbc.com/2026/01/08/china-ai-tiger-goes-ipo-zhipu-hong-kong-debut-openai-knowledge-atlas-hsi-hang-seng-listing.html) · [SCMP — $560m share sale](https://www.scmp.com/business/investor-relations/ipo-quote-profile/article/3338107/chinas-zhipu-ai-launches-us560-million-share-sale-hong-kongs-ipo-tech-race-heats) · [SCMP — relative performance](https://www.scmp.com/tech/big-tech/article/3356390/minimax-once-led-zhipu-hong-kongs-ai-stock-race-how-tables-have-turned) · [CNBC — Shanghai listings](https://www.cnbc.com/video/2026/06/03/zhipu-minimax-prepping-shanghai-listings.html)

### MiniMax — **listed, SEHK: 100**
Founded **December 2021** by Yan Junjie, Yun Yeyi and Zhou Yucong. HQ Xuhui, Shanghai.
**415 employees** (2025). Investors include **MiHoYo** (early), Alibaba, Tencent, GL Ventures,
HongShan and IDG Capital.

- **IPO'd on HKEX 9 January 2026, one day after Zhipu**, raising HK$4.8bn (~$620m); shares closed
  the debut at HK$345 against a HK$165 offer — **up 109%**. Market cap HK$159.3bn by mid-2026.
- **Models:** ABAB series, MiniMax-01 line, MiniMax-M series (to M3), Speech-02, and Hailuo AI for
  text, music and video generation.
- Channels: `minimax.io/news`, `github.com/MiniMax-AI`, Hugging Face, arXiv.

Sources: [Wikipedia — MiniMax](https://en.wikipedia.org/wiki/MiniMax_(company)) · [CNBC — MiniMax debut](https://www.cnbc.com/2026/01/09/minimax-hong-kong-ipo-ai-tigers-zhipu.html) · [SCMP — IPO size](https://www.scmp.com/business/banking-finance/article/3338766/minimaxs-hong-kong-ipo-set-hit-us538-million-amid-chinese-ai-sector-frenzy) · [SCMP — both listings](https://www.scmp.com/tech/tech-trends/article/3339301/minimax-and-zhipus-stellar-hong-kong-ipos-supercharge-chinas-ai-ambitions)

### Moonshot AI (Kimi)
Founded **March 2023** by Yang Zhilin, Zhou Xinyu and Wu Yuxin. HQ JD Technology Building, Haidian,
Beijing. **300 employees** (2026). **Alibaba holds 36%**; other investors include Tencent, Gaorong
Capital and IDG Capital.

- **Valuation $35bn (July 2026); ARR $200m (April 2026).** Considering a Hong Kong IPO as of March
  2026, no confirmed timeline.
- **Models:** Kimi K1.5 (January 2025), K2 (July 2025, 1tn parameters), K2 Thinking (November 2025),
  K2.5 (January 2026, multimodal), **K3 (July 2026, 2.8tn parameters)**.
- Channels: `moonshotai.github.io`, `github.com/MoonshotAI`, Hugging Face, arXiv.

Sources: [Wikipedia — Moonshot AI](https://en.wikipedia.org/wiki/Moonshot_AI) · [Fortune — Chinese AI IPO rush](https://fortune.com/2026/07/23/moonshot-deepseek-great-chinese-ai-ipo-rush/) · [Dealroom](https://dealroom.co/news/136199-inside-chinas-ai-ecosystem-beyond-deepseek-zhipu-minimax-moonshot-byteda/)

### ByteDance — Doubao / Seed
Developed by **ByteDance**, released **August 2023**, powered by **Volcano Engine (Volcengine)**.

- **330 million users as of May 2026**, up from ~60m monthly actives in November 2024. Reported at
  120 trillion tokens per day. **Doubao Seed 2.0 released 14 February 2026.**
- Sibling model lines Seedream (image) and Seedance (video) — ByteDance is the strongest Chinese
  lab in video generation.
- Channels: `seed.bytedance.com`, `github.com/bytedance-seed`, arXiv.

Sources: [Wikipedia — Doubao](https://en.wikipedia.org/wiki/Doubao) · [Wikipedia — Seedance 2.0](https://en.wikipedia.org/wiki/Seedance_2.0)

---

## Tier 3 — Western challengers and big-tech in-house

### Mistral AI
Founded **28 April 2023** by **Arthur Mensch** (ex-Google DeepMind), **Guillaume Lample** and
**Timothée Lacroix** (both ex-Meta). HQ Paris. **1,000+ employees** as of 2026.

- **September 2025: €2bn raised at a €12bn ($14bn) valuation, with ASML acquiring an ~11% stake for
  €1.3bn.** ⚠ Mistral's own announcement headlines the raise as **€1.7bn**; Wikipedia states €2bn.
  Both are cited below — the discrepancy is unresolved and the figure should be taken from the
  filing before use.
- This is a **direct semiconductor-equipment ↔ frontier-lab equity link**, exactly the case the
  research notes flag as worth tracking.
- Earlier rounds: €105m June 2023 at €240m; €385m December 2023 at €2bn+; $16m from Microsoft
  February 2024; €600m June 2024 at €5.8bn; €100m CMA CGM partnership April 2025. **$830m March
  2026 for datacentre construction.**
- **Models:** Mistral (7B/Small/Medium/Large), Mixtral MoE, Ministral, Codestral, Devstral,
  Magistral (reasoning), Voxtral (speech), Pixtral (multimodal).
- Channels: `mistral.ai/news`, `huggingface.co/mistralai`, `github.com/mistralai`.

Sources: [Wikipedia — Mistral AI](https://en.wikipedia.org/wiki/Mistral_AI) · [Mistral — €1.7bn announcement (primary)](https://mistral.ai/news/mistral-ai-raises-1-7-b-to-accelerate-technological-progress-with-ai/) · [Reworked — ASML round](https://www.reworked.co/digital-workplace/mistral-ai-secures-2b-in-funding-led-by-asml/) · [CNBC Disruptor 50](https://www.cnbc.com/2026/05/19/mistral-cnbc-disruptor-50-ranking.html)

### Cohere
Founded **2019** by **Aidan Gomez** (co-author of *Attention Is All You Need* at Google Brain),
**Nick Frosst** (Google Brain) and **Ivan Zhang**; all three attended the University of Toronto.
HQ Toronto. **450+ employees** (2025).

- **Funding:** $40m Series A September 2021; $125m Series B February 2022; $270m Series C June 2023
  at $2.2bn; $500m July 2024 at $5.5bn; $500m in 2025 at $6.8bn; $100m September 2025 at ~$7bn.
  **Revenue $240m (February 2026).**
- **Models:** Command A+, Command A Reasoning, Command A Translate, Command A Vision, Cohere North
  (secure AI workspace), Aya Vision. Enterprise and RAG focus rather than consumer.
- Channels: `cohere.com/blog`, `cohere.com/research`, `huggingface.co/CohereLabs`.

Sources: [Wikipedia — Cohere](https://en.wikipedia.org/wiki/Cohere)

### Ai2 (Allen Institute for AI)
Founded **2014** by **Paul Allen**. HQ Seattle, with an office in Tel Aviv. **501(c)(3) non-profit.**
Headcount not stated by the source.

- **The most genuinely open lab:** OLMo ships weights, data *and* training code, which makes Ai2
  uniquely valuable as an extraction-evaluation corpus — the ground truth is actually inspectable.
- **Releases:** OLMo announced May 2023, 1B/7B open-sourced February 2024; OLMoE September 2024;
  OLMo 2 November 2024 with a 32B variant March 2025; **Olmo 3 November 2025; Olmo 3.1 December
  2025.** Tulu instruction-tuned models from June 2023 through Tulu 3 November 2024.
- **Leadership:** Oren Etzioni (Sept 2013 – Sept 2022), Peter Clark interim, **Ali Farhadi CEO from
  31 July 2023 until he stepped down, with Peter Clark returning as interim CEO on 10 March 2026.**
  Reporting elsewhere says Farhadi went to Microsoft with several Ai2 researchers — a clean worked
  example of the talent-movement indicator.
- Channels: `allenai.org/blog`, `github.com/allenai`, `huggingface.co/allenai`.

Sources: [Wikipedia — Allen Institute for AI](https://en.wikipedia.org/wiki/Allen_Institute_for_AI) · [LLMReference — Ai2](https://www.llmreference.com/researcher/ai2)

### Microsoft AI (MAI)
Division formed **19 March 2024**. Led by **Mustafa Suleyman** (CEO and EVP, Superintelligence
division); Jacob Andreou EVP of the Copilot division from March 2026. HQ Redmond.
**10,000 employees** (2025).

- **MAI Superintelligence team formed 6 November 2025** under Suleyman, with three stated
  applications: AI companions, healthcare assistance, and clean-energy breakthroughs.
- **Models:** MAI-Voice-1 and MAI-1-preview (28 August 2025); MAI-Image-1 (13 October 2025);
  MAI-Image-2.5, MAI-Code-1-Flash and five further models (June 2026).
- Channels: `microsoft.com/en-us/ai`, Microsoft Research blog, arXiv.

Sources: [Wikipedia — Microsoft AI](https://en.wikipedia.org/wiki/Microsoft_AI) · [Tech-Insider](https://tech-insider.org/microsoft-mai-in-house-ai-models-openai-2026/)

### Amazon AGI — **BIT's largest holding**
Amazon introduced the **Nova** foundation-model family at AWS re:Invent in **December 2024** —
Nova Micro (text), plus Nova Lite, Nova Pro and Nova Premier (multimodal).

- **Leadership churn through 2026:** **Rohit Prasad** led the AGI team from 2023 and his departure
  was announced 17 December 2025; **Peter DeSantis** took over the group, with **Pieter Abbeel**
  (who joined via the Covariant acquisition in 2024) leading frontier model research.
  **David Luan**, head of the AGI lab and previously founder of Adept, left in February 2026.
  Amazon **laid off employees in the AGI unit in July 2026**.
- **Why it matters here:** Amazon is BIT's largest holding at ~9.7% of book, and is simultaneously
  a lab, a hyperscaler, and a $50bn investor in OpenAI's 2026 round. **Any mechanism touching
  Amazon has three independent routes** — the clearest argument for a max-not-sum propagation rule.
- Channels: `amazon.science/blog`, AWS AI blog, model cards.

Sources: [Amazon — introducing Nova](https://www.aboutamazon.com/news/aws/amazon-nova-artificial-intelligence-bedrock-aws) · [Amazon press release](https://press.aboutamazon.com/2024/12/introducing-amazon-nova-a-new-generation-of-foundation-models) · [CNBC — Prasad leaving, DeSantis takes AGI](https://www.cnbc.com/2025/12/17/amazon-ai-chief-prasad-leaving-peter-desantis-agi-group.html) · [Fortune — leadership shake-up](https://fortune.com/2025/12/17/amazon-ceo-andy-jassy-announces-departure-of-ai-exec-rohit-prasad-in-leadership-shakeup/) · [CNBC — AGI unit layoffs](https://www.cnbc.com/2026/07/22/amazon-lays-off-some-employees-in-its-agi-unit.html) · [Amazon — DeSantis leadership update](https://www.aboutamazon.com/news/company-news/andy-jassy-peter-desantis-amazon-leadership-update)

### Reflection AI
Founded **2024** by **Misha Laskin** and **Ioannis Antonoglou**, both former Google DeepMind
researchers. HQ Brooklyn, New York. Headcount not stated.

- **Funding:** $130m total March 2025 ($25m seed + $105m Series A) at ~$545m; **$2bn October 2025 at
  $8bn; $25bn valuation June 2026** — a 46× mark in fifteen months.
- Describes itself as "an open-source artificial intelligence company" and "an open-model
  alternative to closed frontier AI labs", and planned to release model weights publicly in 2025.
  **Specific open-weight releases are not documented in the source** — worth verifying before
  treating it as an open-weights publisher.
- **Product:** Asimov, a code-comprehension agent that reads source code, email, Slack, project
  updates and documentation.
- Channels: `reflection.ai`, Hugging Face, arXiv.

Sources: [Wikipedia — Reflection AI](https://en.wikipedia.org/wiki/Reflection_AI)

---

## Tier 4 — Stealth and new-modality

Low publication volume, high per-item signal. Cheap to watch; a single release moves the map.

### Safe Superintelligence (SSI)
Founded **19 June 2024** by **Ilya Sutskever** (former OpenAI chief scientist), **Daniel Gross**
(former head of Apple's AI efforts) and **Daniel Levy** (investor, former OpenAI researcher).
Offices Palo Alto and Tel Aviv. **~50 employees** as of July 2025.

- **Funding:** $1bn September 2024 from SV Angel, DST Global, Sequoia and Andreessen Horowitz;
  **$30bn valuation March 2025**, six times the previous $5bn. **Nvidia announced a planned $5bn
  investment July 2026** as part of a strategic partnership, reported alongside a shift from Google
  TPUs to Nvidia GPUs.
- **No product release is stated by the source.**
- **A first release here would be a maximum-signal, near-zero-volume event** — the cleanest case to
  tune the alerting path against.
- Channels: `ssi.inc` (essentially static), personnel accounts on X.

Sources: [Wikipedia — Safe Superintelligence](https://en.wikipedia.org/wiki/Safe_Superintelligence) · [TechCrunch — Nvidia partnership](https://techcrunch.com/2026/07/27/ilya-sutskevers-safe-superintelligence-partners-with-nvidia-to-scale-its-ai-research/) · [Calcalist](https://www.calcalistech.com/ctechnews/article/hjfywdtajl)

### Thinking Machines Lab
Founded **February 2025** by **Mira Murati** (former OpenAI CTO). HQ 2300 Harrison Street, Mission
District, San Francisco. **100 employees** (2026). Organised as a **public benefit corporation**.

- **Team:** Murati (CEO), **John Schulman** (chief scientist, OpenAI co-founder), **Barret Zoph**
  (ex-OpenAI VP Research), **Lilian Weng** (ex-OpenAI VP). A concentrated OpenAI-alumni cluster —
  the talent-movement indicator in its purest form.
- **Funding:** $2bn July 2025 at $12bn, led by Andreessen Horowitz, with Nvidia, AMD, Cisco, Jane
  Street and the government of Albania ($10m). *A "$5bn Series B at $50bn, March 2026" appeared in
  v0 of this file and could **not** be sourced — it has been removed.*
- **Products:** **Tinker** (1 October 2025), a fine-tuning API; **Inkling** (15 July 2026), a 975bn
  parameter open-weights model under Apache licence; **Inkling Small** (31 July 2026), a 276bn
  parameter distillation with comparable performance.
- Channels: `thinkingmachines.ai/blog`, X.

Sources: [Wikipedia — Thinking Machines Lab](https://en.wikipedia.org/wiki/Thinking_Machines_Lab)

### World Labs
Founded **2024** by **Fei-Fei Li** and three colleagues (not named by the source).

- **Funding:** $230m in 2024; **$1bn in 2026**, at a valuation above $1bn.
- **Focus:** spatial intelligence — AI that understands how the three-dimensional physical world
  works, aiming to integrate visual perception with action so robotic systems can perform everyday
  tasks from verbal instruction.
- Channels: `worldlabs.ai/blog`, arXiv.

Sources: [Wikipedia — Fei-Fei Li](https://en.wikipedia.org/wiki/Fei-Fei_Li)

### Physical Intelligence
Founded **2024** by former Google DeepMind researchers together with Stanford and UC Berkeley
academics; co-founders include **Sergey Levine** (UC Berkeley) and **Lachy Groom**.

- **Funding:** **$600m at a $5.6bn valuation (November 2025)**, led by Alphabet's CapitalG with Lux
  Capital, Thrive Capital, Jeff Bezos, Index Ventures and T. Rowe Price. **In talks March 2026 to
  raise ~$1bn at above $11bn**, roughly doubling the mark in four months.
- **Models:** π0.7, described by the company as an early step toward a general-purpose robot brain
  that can power any robot for any application.
- The leading indicator for any robotics-linked mechanism.
- Channels: `physicalintelligence.company/blog`, arXiv.

Sources: [Bloomberg — $5.6bn valuation](https://www.bloomberg.com/news/articles/2025-11-20/robotics-startup-physical-intelligence-valued-at-5-6-billion-in-new-funding) · [Bloomberg — $11bn talks](https://www.bloomberg.com/news/articles/2026-03-27/ex-deepmind-staffers-robotics-startup-in-talks-for-11-billion-valuation) · [TechCrunch — $1bn raise talks](https://techcrunch.com/2026/03/27/physical-intelligence-is-reportedly-in-talks-to-raise-1-billion-again/) · [TechCrunch — inside the company](https://techcrunch.com/2026/01/30/physical-intelligence-stripe-veteran-lachy-grooms-latest-bet-is-building-silicon-valleys-buzziest-robot-brains/) · [TechCrunch — untaught tasks](https://techcrunch.com/2026/04/16/physical-intelligence-a-hot-robotics-startup-says-its-new-robot-brain-can-figure-out-tasks-it-was-never-taught/) · [Axios](https://www.axios.com/2025/11/21/robots-physical-intelligence-ai)

---

## Entity-resolution hazards

Named now because collisions are a stated failure mode and these are the ones that will bite.

| Hazard | Note |
| --- | --- |
| **Figure AI ≠ Figure Technology Solutions** | BIT's `FT Inter Inc. Cl. A` (~3.0% of book) is Figure Technology Solutions, a fintech lender. Figure AI is an unrelated private humanoid-robotics company. A naive name match routes robotics news onto a fintech holding with total confidence and no error. |
| **Zhipu has four names** | Z.AI Co., Ltd. / Beijing Zhipu Huazhang Technology / Knowledge Atlas Technology / Zhipu AI, trading as SEHK 2513. |
| **xAI is now SpaceXAI, inside SpaceX** | Renamed July 2026, subsidiary since February 2026, listed as SPCX. Lab news routes to a different corporate parent than it did in 2025. |
| **Cursor belongs to SpaceX** | Since 14 August 2026. Any Cursor/Anysphere item is now a SPCX item. |
| **DeepMind / Google DeepMind / Google Brain** | One merged org since April 2023; author affiliation is unreliable pre-merger. |
| **FAIR / MSL / TBD Lab** | Three nested Meta identities publishing concurrently with opposed postures — FAIR open, MSL increasingly proprietary. |
| **Doubao / Seed / Seedream / Seedance / Volcano Engine** | One ByteDance effort, five names. |
| **Two Daniels at SSI** | Daniel Gross and Daniel Levy — distinct people, frequently conflated. |

---

## Deliberate omissions

- **Wrapper and application companies** — Perplexity, Harvey. Consumers of frontier models, not
  producers; they belong in the mapping layer. *Cursor has moved: it is now part of SpaceX.*
- **Image/video-only labs** — Black Forest Labs, Runway, Luma, Stability AI. Reconsider if a
  mechanism routes through media generation.
- **Adjacent robotics/world-model labs surfaced during research but not yet assessed** — General
  Intuition (reported ~$6bn, August 2026) and Generalist (reported ~$3bn, August 2026).
- **Plausible additions pending cut criteria** — Nvidia Research, Apple, IBM Granite, Baidu ERNIE,
  Tencent Hunyuan, Nous, EleutherAI, Liquid AI, Decart, H Company, Skild AI.

## Next actions

1. **Promote key facts from Wikipedia to primary sources.** This file clears "every claim is
   sourced"; it does not yet clear the project's *primary source* bar. Wikipedia's inline
   references are the shortest path.
2. **Resolve the Mistral ⚠** — €1.7bn (company) vs €2bn (Wikipedia) for the ASML round.
3. **Verify Reflection AI's open-weight releases.** It self-describes as open-source, but no
   specific weight release is documented.
4. **Add lab→ticker links to the mapping layer** for SPCX, SEHK 2513 and SEHK 100. Three frontier
   labs are now inside listed issuers; for these the mechanism route is not the only path.
5. **Harvest the channel URLs** into per-lab source entries for ingestion.
6. **Use Tier 4 to test the alerting path** — low volume, high per-item importance.
