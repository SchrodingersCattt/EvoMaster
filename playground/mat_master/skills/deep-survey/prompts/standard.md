# deep-survey: standard depth workflow

**This file is the authoritative workflow for `--depth standard`.**

---

## Purpose

Produce a concise, readable Markdown survey report: Executive Summary + References. Suitable when the user wants a short survey file or an intermediate step before deeper investigation.

---

## Workflow

### Step 1 — Plan facets
- Identify **2-3 facets** from the topic (e.g. definition/overview, methods, recent results).
- For each facet, plan **2-3 query variants** (keywords, synonyms, language variants).

### Step 2 — Retrieve
- Run **6-8 `mat_sn_*` calls** total (mix of paper search and web search per facet).
- Prefer English queries for broader coverage.
- Web search returns snippets: fetch full page for high-relevance URLs (`mat_doc_*`).
- Filter for relevance and source quality (authority, recency).

### Step 3 — Write the report (LLM)

#### Executive Summary
- **2-4 substantive paragraphs** covering: what the field addresses, current state, key findings/methods, and open questions.
- Do NOT write a single-paragraph abstract. Develop the content fully from retrieved sources.
- Cite sources inline: `[n](URL)` immediately after the relevant claim.

#### Formula rule (mandatory for standard)
- When a formula or mathematical expression appears in the source material and is central to understanding the topic, include it.
- **Every symbol must be explained on first appearance**: write "where *E* is the total energy, *k*_{B} is the Boltzmann constant, and *T* is temperature" immediately after the equation.
- Do not proactively insert formulas that are not from your sources; explain every one you do include.

#### References
- List every cited source as: `[n] Authors. Title. *Journal*, **Year**. URL`
- Every entry must have a URL. In-text [n] indices must be contiguous from [1].

### Step 4 — Output
- Save report to `_tmp/surveys/survey_<topic>.md`.
- Do NOT leave any `(TBD)` in the delivered file.

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

---

## Citation format

Follow `../_common/reference/citation_and_output_format.md` for citation style, URL requirements, and reference list format.

---

## What NOT to do at this tier

- Do NOT produce Key Methodologies, State of the Art, or Gap Analysis sections (those are `deep` only).
- Do NOT run 10+ retrievals — stay within 6-8 budget.
- Do NOT write from memory; every claim must be grounded in a retrieved source.
