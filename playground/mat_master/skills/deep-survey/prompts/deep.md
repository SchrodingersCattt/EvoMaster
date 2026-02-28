# deep-survey: deep depth workflow

**This file is the authoritative workflow for `--depth deep`.**

---

## Purpose

Produce a full-length, multi-section review report. This is the most rigorous tier. Every section must be fully developed from retrieved sources — not a 1-2 page brief.

---

## Workflow

### Step 1 — Plan facets
- Analyze the topic and break it into **3-5 facets** (e.g. definition/background, mechanisms, methods/approaches, state of the art, gaps/challenges; see `reference/search_facets_and_rounds.md` for typed facet examples).
- For each facet, plan **2-4 query variants** (keywords, synonyms, alternate language, "X review", "X mechanism").
- Target total: **10-15+ retrieval tool calls**.

### Step 2 — Execute retrieval loop
- For each facet and each query variant: call `mat_sn_search-papers-normal`, `mat_sn_scholar-search`, `mat_sn_web-search` repeatedly. Prefer **English queries** for broader coverage.
- After each search: filter for relevance (authority, recency, direct topic match). Keep only hits clearly related to the facet.
- Web search returns snippets only: fetch full page content (`mat_doc_*`) for high-relevance URLs.
- For high-relevance papers, fetch full text where accessible.

#### Active facet review (mandatory)
After completing each facet's retrieval, **pause and evaluate**:
- Did results reveal sub-topics or related concepts not in the original facet plan?
- Should remaining facets have their query variants adjusted based on what was found?
- Is there a facet that now deserves more retrieval calls than originally planned?

Adjust the remaining plan before proceeding to the next facet.

### Step 3 — Write the report (LLM)

Write all five sections fully. Write each section's full body to a file first (e.g. `_tmp/surveys/section_Executive_Summary.md`), then call `write_section` with `--content_file`. Do NOT pass long section text in `--content` (may be truncated).

#### Executive Summary
- **3-5 paragraphs**: field overview, key developments, main methods, critical findings, open challenges.
- Grounded entirely in retrieved sources; cite inline `[n](URL)`.

#### Key Methodologies
- Table format plus narrative paragraphs.
- Columns: Method / Key features / Typical applications / Representative references.
- Follow table with narrative discussion of strengths and limitations.

#### State of the Art
- **Multiple subsections** (by theme, material class, time period, or approach — choose the most logical grouping for the topic).
- Each subsection: detailed discussion with quantitative comparisons, not just a list of papers.
- Extensive citation coverage; aim for 20+ unique sources across the full report.

#### Gap Analysis
- Several elaborated points (2-4 sentences each).
- **Order gaps by impact/urgency: most important first.** Do not list gaps in arbitrary order.
- Each gap must cite specific evidence: why this gap exists, what has been attempted, what is still missing.
- Distinguish between methodological gaps, data gaps, and conceptual gaps.

#### References
- Numbered list `[n] Authors. Title. *Journal*, **Year**. URL`.
- Every entry must have a URL. In-text [n] must be contiguous from [1].

### Step 4 — Output
- Assemble all sections into `_tmp/surveys/survey_<topic>.md`.
- Do NOT leave any `(TBD)` in the delivered file.
- When the report is complete: output the full final report in your reply so the user sees it, then confirm the file path.

---

## Concept rigor (mandatory for deep)

- **Define every key concept** at first use with a precise, field-standard definition. Do not assume the reader knows the term.
- **Explain every formula symbol**: when an equation appears, add "where *E* is ..., *k*_{B} is ..., *T* is ..." immediately after. Leave no symbol unexplained.
- **State how concepts relate**: dependence, contrast, hierarchy, or causal link. Do not list concepts in isolation.
- **Use examples**: where helpful, illustrate with a concrete example from retrieved material (specific material, value, or result).

---

## Formula rules (mandatory for deep)

- Include formulas when they are central to understanding the topic and present in your sources.
- Every symbol explained on first appearance in the text.
- Use Markdown notation: `*E*`, `_{subscript}`, `^{superscript}`, en-dash "–" for ranges, minus "−" for negatives.
- Do not proactively insert formulas that are not from sources; but do include all key equations from the retrieved material.

---

## De-AIGC rules (compressed — apply to all written sections)

Full guide: `use_skill action=get_reference reference_name="de_aigc_style_guide.md"` (in `skills/_common/reference/`).

**Core principles:**
1. Lead with the real problem, not broad context.
2. Prefer concrete verbs over abstract labels (`confirm`, `measure`, `reduce` — not `showcase`, `highlight`).
3. Calibrate claims to evidence — match confidence to what is directly shown.
4. One main point per sentence; split compound observations.
5. Replace vague statistics with named ones (MAD, RMSD, STD); add boundary conditions.
6. Remove filler openers: delete `Notably,`, `Significantly,`, `It is worth noting that`, `Importantly,`.

**Delete on sight**: `It is well known that`, `This section reviews`, `It should be noted that`, `Paves the way for`, `Groundbreaking`, `Unprecedented`, `Showcasing`, `Highlighting`, `Fosters`.

**After drafting each section**, apply the 5-pass checklist: claim calibration → specificity upgrade → sentence compression → redundancy removal → tone scan. See full guide for pattern rewrites.

---

## Length requirement

This must be a **full-length review** — minimum 4000 words across all sections. Do not deliver a 1-2 page brief. Every section must be substantive; the State of the Art alone should be 1500+ words.

---

## Citation format

Follow `../_common/reference/citation_and_output_format.md` for citation style, URL requirements, and reference list format.

---

## What NOT to do at this tier

- Do NOT write from memory; every claim must be grounded in a retrieved source with a citation.
- Do NOT order Gap Analysis arbitrarily — rank by impact/urgency.
- Do NOT skip the active facet review after each facet.
- Do NOT leave any symbol unexplained in formulas.
