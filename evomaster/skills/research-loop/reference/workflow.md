# Research-Loop Workflow

Step-by-step flow, query templates, and tool quick reference for the research-loop skill.

## 1. Typical Aspects to Cover

When answering a conceptual or professional question, consider searching these aspects (adjust to the question):

| Aspect | Description | Prefer web or papers |
|--------|-------------|------------------------|
| **Definition** | What the term/concept means | Web + papers |
| **Mechanism** | How it works, underlying physics/chemistry | Papers, then web |
| **Methods** | Experimental or computational procedures | Web (protocols), papers (methods sections) |
| **Tutorials** | Step-by-step guides, best practices | Web |
| **Reviews** | Recent summaries, consensus, trends | Papers (review articles) |
| **Caveats** | Limitations, controversies, pitfalls | Papers + web |

## 2. Per-Aspect Search Strategy

- **Definition / tutorials**: Start with `mat_sn_web-search` (e.g. "X 定义", "X tutorial", "X 教程").
- **Mechanism / methods / reviews**: Use `mat_sn_search-papers-normal`, `mat_sn_scholar-search`, or `mat_sn_pubmed-search` with queries like "X mechanism", "X 机理", "X review", "X 综述".
- **Single URL deep-dive**: If a search result URL looks **relevant** (see below), use `mat_doc_extract_info_from_webpage` to pull the main text (synchronous; no job polling). Do not extract every URL—only those that clearly match the aspect or user intent.

Use multiple query variants (Chinese/English, synonyms) per aspect when the first round is thin.

## 2b. Between Steps: Discriminate Relevance

After **each** search (or each batch of results):

1. **Evaluate each hit** by title, URL, and snippet against the current aspect and the user’s question (e.g. 为什么 / 如何 / 是什么 / 研究进展).
2. **Mark as relevant / irrelevant**: Keep only hits that are clearly related; skip generic, off-topic, or duplicate content.
3. **Downstream use**: Use only **relevant** hits for (a) synthesis into the final answer, (b) calling `mat_doc_extract_info_from_webpage`. Do not pass irrelevant URLs to extract—it wastes steps and dilutes the answer.
4. **Between rounds**: When moving to the next aspect or next round, re-evaluate so that the final synthesis is based on a filtered, relevant subset of all results, not on everything returned.

Many search results are irrelevant; step-level discrimination keeps the loop focused and the answer evidence-based.

## 2c. Source Quality (研究进展 / 文献类)

For **研究进展** (research progress), **查文献**, or any literature/review-style question:

- **You must run paper search**: Use `mat_sn_search-papers-normal`, `mat_sn_search-papers-enhanced`, or `mat_sn_scholar-search` and use the results as the **main** basis of the answer. One web search only is not acceptable for these tasks.
- **Prefer academic sources**: Peer-reviewed papers, official reports, university/institute pages. Treat web-only hits (e.g. 知乎专栏、B站专栏、澎湃、营销站、百科) as **supplementary**; do not build the whole answer on them.
- **In the synthesis**: Cite papers/scholar results first; add web snippets only where they add clear value (e.g. definitions, tutorials). If you have no paper results, run paper search before finishing—do not finish on web-only sources for 研究进展/文献 tasks.

This avoids answers that rest on unreliable or non-authoritative Chinese web pages only.

## 3. Tool Quick Reference

| Tool | Use for |
|------|--------|
| `mat_sn_web-search` | General web search (tutorials, definitions, pages). |
| `mat_sn_search-papers-normal` | Academic paper search (normal mode). |
| `mat_sn_search-papers-enhanced` | Academic paper search (enhanced). |
| `mat_sn_scholar-search` | Scholar / citation-oriented search. |
| `mat_sn_pubmed-search` | Biomedical / life-sciences literature. |
| `mat_doc_extract_info_from_webpage` | Extract main content from a single URL (sync). |

All of the above are **synchronous**: call once, get the result, then proceed. No submit/query_job_status/get_job_results in this workflow.

## 3b. Output Format and Citation (Human-Friendly)

Apply these rules when synthesizing the final answer (aligned with Science Navigator–style quality).

**General**
- Output in **plain text**; start directly with substantive content (no "Sure...", "Okay...", "I will now analyze...").
- Every factual claim must be supported by tool results; do not add unsupported superlatives or invented facts.
- Numbers and units: add a space (e.g. 10 cm, 5 kg). No extra space between Chinese and English characters.
- Italic for physical quantities: *E*, *T*, *k*. Bold for vectors and compound codes: **F**, **E**, compound **1**.
- Define all abbreviations at first use and use them consistently.
- Journal/article names: italic only, e.g. *Journal of Chemical Physics*; do not use 《》 for titles.

**Citation (mandatory format)**
- Every cited source must use this exact form: `<a href="URL" target="_blank">[n]</a>`.
- One reference per link. Multiple refs = multiple consecutive links, e.g. `<a href="URL2" target="_blank">[2]</a><a href="URL3" target="_blank">[3]</a>`.
- **Wrong**: [2,3], [2, 3], [2–3], [2; 3], or any comma/semicolon/dash inside one bracket. Each [n] must be its own `<a href="..." target="_blank">[n]</a>`.

**By question type**
- **"是什么" (what/definition)**: Precise, direct, fact-based; merge overlapping definitions from snippets into one clear explanation; limit statements to what search results support.
- **"为什么" (why/mechanism)**: Layered explanation—(1) definitions/fundamentals from sources, (2) mechanistic or causal statements from sources, (3) synthesized reasoning if needed, clearly labeled; cite each supporting sentence with the link format above.
- **"如何" (how/procedure)**: Step-by-step procedure with a citation link for each step; if code or commands appear in sources, reproduce them verbatim in a code block and explain briefly.
- **研究进展 (research progress)**: Executive summary (key concepts, recent breakthroughs, main challenges) then structured analysis; state relevance of papers to the query where useful; cite with the same link format.

## 4. Example: "Explain why Flack parameter is ~1e-4 at room temperature and ~0.1 at high temperature for chiral molecular crystals in SC-XRD"

**Aspect split (examples):**

- Definition: Flack parameter, absolute structure, SC-XRD.
- Mechanism: Why temperature affects Flack (thermal motion, Debye–Waller, anomalous scattering).
- Methods: How Flack is refined, what "good" values mean.
- Reviews/caveats: Typical pitfalls, interpretation.

**Query sequence (illustrative):**

1. Web: "Flack parameter definition", "Flack parameter 定义", "absolute structure SC-XRD".
2. Web: "Flack parameter temperature", "Flack 参数 温度 影响".
3. Papers: "Flack parameter temperature dependence", "anomalous scattering thermal motion".
4. Papers: "chiral crystal absolute configuration refinement".

**Relevance:** After each search, keep only hits relevant to the aspect (e.g. Flack, temperature, anomalous scattering); do not extract or cite off-topic URLs.

**Synthesis:** After 2–3 aspects are covered with **relevant** sources, write the answer following the format and citation rules in §3b (plain text, cite with `<a href="URL" target="_blank">[n]</a>`), then call **finish** with `task_completed=true`.

## 5. Exit Condition

- At least 2–3 distinct aspects have been searched and have yielded usable content.
- No need to exhaust every aspect; stop when the answer is well-supported.
- Always call the **finish** tool to end the task; do not only write a summary in the message body.
