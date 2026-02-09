# Search Facets and Repeated Retrieval (Deep Survey)

When the task is **serious writing** (comprehensive survey / long-form report), retrieval must be **expanded by facets** and **repeated**: many tool calls across multiple aspects of the query, not one or two shallow searches.

## 1. Expand the query into facets

From the user's topic or question, derive **multiple search facets** (aspects). Use at least **3–5 distinct facets** so the report covers definition, mechanism, methods, state of the art, and caveats. Adjust to the topic.

| Facet | Description | Prefer |
|-------|-------------|--------|
| **Definition** | What the term/concept means; scope | Web + papers |
| **Mechanism** | How it works; underlying physics/chemistry | Papers, then web |
| **Methods** | Experimental or computational procedures | Web (protocols), papers (methods) |
| **Tutorials / how-to** | Step-by-step guides, best practices | Web |
| **Reviews / state of the art** | Recent summaries, consensus, trends | Papers (review articles) |
| **Caveats** | Limitations, controversies, pitfalls | Papers + web |

**Example**: Topic "Perovskite stability under moisture"

- Definition: perovskite (materials), stability, moisture degradation
- Mechanism: hydrolysis, ion migration, role of humidity
- Methods: encapsulation, characterization (XRD, PL), aging protocols
- Reviews: recent progress, 2020–2024 reviews
- Caveats: conflicting reports, measurement differences

**Example**: Topic "VASP convergence failures"

- Definition: SCF convergence, convergence criteria
- Mechanism: why convergence fails (charge sloshing, geometry)
- Methods: ALGO, mixing, smearing; workarounds
- Reviews: best practices, benchmarks
- Caveats: when to switch codes, known issues

## 2. Repeatedly call retrieval tools

- **Do not** run a single search (e.g. one `mat_sn_search-papers-normal` with the raw topic) and stop.
- **Do** run **multiple searches per facet**:
  - For each facet, formulate **2–4 query variants** (e.g. different keywords, Chinese/English, synonyms, "X review", "X mechanism").
  - Call **paper search** (e.g. `mat_sn_search-papers-normal`, `mat_sn_scholar-search`, `mat_sn_search-papers-enhanced`) for each variant.
  - Call **web search** (e.g. `mat_sn_web-search`) where the table above says "Web" (definitions, tutorials).
- **Minimum**: Roughly **3–5 facets × 2–3 queries per facet** ⇒ **at least 6–15 retrieval tool calls** before synthesizing. For "deep" surveys, more rounds (e.g. second round on under-covered facets) are expected.
- After each search: **filter for relevance**; keep only hits clearly related to that facet and the user intent. Then continue to the next query or facet.

## 3. Tool usage by facet

- **Definition / tutorials**: Prefer `mat_sn_web-search` (e.g. "X definition", "X tutorial", "X 教程").
- **Mechanism / methods / reviews**: Prefer `mat_sn_search-papers-normal`, `mat_sn_scholar-search`, or `mat_sn_pubmed-search` with queries like "X mechanism", "X 机理", "X review", "X 综述".
- **Single URL deep-dive**: When a result URL is clearly relevant, use `mat_doc_extract_info_from_webpage` (or equivalent) to pull main text. Do not extract every URL—only those that clearly match the facet or user intent.

Use **multiple query variants** (Chinese/English, synonyms) per facet when the first round is thin.

## 4. Summary rule

- **Simple lookup** (answer in chat): 1–2 search calls are acceptable.
- **Serious writing** (deep-survey, report to file): **Expand query into facets → repeatedly call retrieval tools for each facet → then** download/read and synthesize. The agent must perform **many** retrieval calls; "searching too little" is not acceptable for this path.
