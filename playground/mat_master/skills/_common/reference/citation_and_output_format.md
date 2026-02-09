# Citation and Output Format (Shared)

Single source of truth for literature/survey reports and manuscripts. Used by **deep-survey** and **manuscript-scribe**.

## General

- Output in **plain text** or **Markdown**; start directly with substantive content (no "Sure...", "Okay...", "I will now analyze...").
- Every factual claim must be supported by tool/source results; do not add unsupported superlatives or invented facts.
- Numbers and units: add a space (e.g. 10 cm, 5 kg). No extra space between Chinese and English characters.
- Italic for physical quantities: *E*, *T*, *k*. Bold for vectors and compound codes: **F**, **E**, compound **1**.
- Define all abbreviations at first use and use them consistently.
- Journal/article names: italic only, e.g. *Journal of Chemical Physics*; do not use 《》 for titles.

## Citation (mandatory format)

- **Every reference MUST have a URL.** No exception. In text use `[n](url)`; in References list [n], full citation, and **URL**. Applies to survey, manuscript, and technical Q&A (including deep-survey simple/short output).
- Every cited source must use a **hyperlink**: `[n]` as a link to the original source URL (or to the References section entry, e.g. `#ref-n`).
- One reference per link. Multiple refs = multiple consecutive links, e.g. `[1](url1)` `[2](url2)`.
- **Wrong**: [2,3], [2, 3], [2–3], [2; 3], or any comma/semicolon/dash inside one bracket. **Wrong**: citing without URL or listing a reference without URL.

## Literature citation sentence template (survey / state-of-the-art)

When citing a paper in the body, use this pattern (aligned with Science Navigator output):

- **Single paper**: In [year], [first author] et al. [found that / reported that / showed that] [summary of findings; include quantitative results if available] by [method]. [Key findings include ...]. [n](url)
- **Comparison**: Compare with [first author] et al., who [support/contradict/expand] [with reference to data or mechanism]. [n](url)

You may omit the full title and journal name in the sentence; give them in the References section. Use "et al." after the first author; state the year and what was done/found, then attach the link.

## By question type (survey reports)

- **What/definition**: Precise, fact-based; limit statements to what search results support.
- **Why/mechanism**: Layered—definitions from sources, then mechanistic statements; cite each sentence.
- **How/procedure**: Step-by-step with a citation per step; reproduce code/commands verbatim in code blocks.
- **Research progress / state of the art**: Executive summary then structured analysis; cite with the link format above.

## References section (manuscripts)

- The **References** section must list **exactly** the same [n] as in the text, in numerical order.
- Each entry: [n], full citation (Authors, Title, *Journal*, Year), and **original source URL**.
- Every [n] in the body must have exactly one [n] in References; no extra or missing entries.

## Concept explanation and conceptual rigor (all outputs)

Apply to **technical Q&A** and **academic writing** (survey, manuscript); for writing skills these requirements are **mandatory**.

- **Definitions**: Give a **solid, precise definition** for every key concept before using it. Do not assume the reader knows the term; ground definitions in sources when possible.
- **Formulas**: When you include a formula or equation, **explain every physical quantity/symbol** (variable, constant, subscript). Do not leave symbols unexplained. Example: "where *E* is the energy, *k* is the Boltzmann constant, *T* is temperature."
- **Concept relationships**: Explicitly state **how concepts relate**—dependence, contrast, hierarchy, or causal link. Do not list concepts in isolation; connect them (e.g. "X is related to Y because …"; "Unlike A, B …").
- **Examples (optional but encouraged)**: When answering or writing, illustrate with **concrete examples** from search results (e.g. a specific material, method, or numerical result). For academic writing, include examples where they clarify the concept or support the argument.

## Terminology and abbreviations (manuscripts)

- **Technical terms**: Define at first use (e.g. in Introduction or Methods).
- **Abbreviations**: Define once as "Full Name (ABBR)" or "ABBR (Full Name)"; do not redefine in a later section.
