---
name: deep-survey
description: "Retrieves literature evidence and optionally produces a written review report. depth=brief outputs collected.json (evidence skeleton, 3-5 retrieval calls) for use by downstream skills such as lit-data-organizer or manuscript-scribe. depth=deep produces a full 5-section review report (10-15+ calls). Use deep-survey when you need systematic literature coverage—not for quick one-off lookups or short chat answers."
skill_type: operator
---

# Deep Survey Skill

A systematic researcher that collects literature evidence and, for `standard`/`deep` depth, generates **detailed review reports saved as Markdown files**.

**When NOT to use**: If the user only wants a quick factual answer in chat (not a file), use MCP search tools directly (`mat_sn_web-search`, `mat_sn_search-papers-normal`) and answer in chat. Invoke deep-survey only when the output is a **file** or when another skill (e.g. composition-optimization) needs structured evidence.

## Depth tiers

| depth | Retrieval calls | Output | Use when |
|-------|----------------|--------|----------|
| `brief` | 3-5 | `collected.json` — evidence skeleton with cards per facet | Sub-step within composition-optimization, manuscript writing, or lit-data-organizer feed |
| `standard` | 6-8 | Concise MD report (Executive Summary + References) | User wants a short survey file, or as an intermediate step |
| `deep` | 10-15+ | Full 5-section report (Executive Summary, Key Methodologies, State of the Art, Gap Analysis, References) | Standalone comprehensive review request |

See **reference/search_facets_and_rounds.md** for depth-specific facet counts and retrieval budgets.

## When to use deep-survey vs on-the-fly

| User intent | Use | Do not use |
|-------------|-----|------------|
| "Give me a comprehensive review on X" | **deep-survey** `--depth deep` | — |
| "Survey the latest progress in Perovskite stability" | **deep-survey** `--depth deep` | — |
| "Collect literature evidence for my paper on X" | **deep-survey** `--depth brief` | — |
| "Summarize methods for calculating melting points" (output to file) | **deep-survey** `--depth standard` or `deep` | — |
| "What are the common failures in VASP relaxation?" (answer in chat) | MCP paper/search tools, short answer | deep-survey |
| "Quick: what is X?" / one-off definition lookup | MCP web/search, short answer | deep-survey |

## Workflow by depth

### brief

1. **Plan**: Identify 1-2 key facets from the topic.
2. **Retrieve**: Run 3-5 `mat_sn_*` calls across those facets.
3. **Output**: Populate `collected.json` (see `reference/collected_json_schema.md` for schema). Write each evidence card: `{source_title, source_url, year, first_author, facet, claim, data_points}`.
4. **Done** — no Markdown report. Pass `collected.json` to the calling skill (e.g. `lit-data-organizer`, `manuscript-scribe`).

### standard

1. **Plan**: Identify 2-3 facets; plan 2-3 query variants per facet.
2. **Retrieve**: Run 6-8 `mat_sn_*` calls (paper search + web search per facet).
3. **Write** (LLM): Using retrieval results, write **Executive Summary** (1-2 paragraphs) and **References** into the survey file using `write_section` or `str_replace_editor`. Do not leave (TBD) in delivered file.
4. **Output**: `_tmp/surveys/survey_<topic>.md` — concise report.

### deep

When routing to **serious writing** (this depth), expand the query into multiple facets and repeatedly call retrieval tools. See **reference/search_facets_and_rounds.md** for facet types and minimum call counts.

1. **Plan (expand facets)**:
   - Analyze the topic and break it into **3-5 facets** (e.g. definition, mechanism, methods, reviews, caveats; see reference).
   - For each facet, plan **2-4 query variants** (keywords, synonyms, or alternate language; e.g. "X review", "X mechanism").
   - Target: enough queries so total **retrieval tool calls** are at least **10-15**.
2. **Execute loop (repeated retrieval)**:
   - **For each facet and each query variant**: Call MCP retrieval tools (`mat_sn_search-papers-normal`, `mat_sn_scholar-search`, `mat_sn_web-search`, etc.) **repeatedly**. Prefer **English** for search queries when possible.
   - After each search: filter for relevance; keep only hits clearly related to that facet and user intent. Before writing, consider each source's quality (authority, relevance, recency).
   - **Web search returns snippets only**: parse/fetch full page content (e.g. `mat_doc_*` extract from webpage) for relevant URLs; do not rely on snippets alone.
   - **Download**: For high-relevance papers, fetch full text where possible.
3. **Write the report (LLM)**: Write all five sections — Executive Summary, Key Methodologies, State of the Art, Gap Analysis, References — using **manuscript-scribe** `write_section` (with `--content_file` for long sections) or **str_replace_editor**.
4. **Full-length retention**: Write each section's full text to a file first (e.g. `_tmp/surveys/section_Executive_Summary.md`), then call `write_section` with `--content_file <path>`. Do **not** pass long section body via `--content` (it may be truncated).

**Note on `manuscript-scribe` delegation**: For `deep` mode, deep-survey may delegate report writing to manuscript-scribe's `write_section` tool. This is a one-way delegation: deep-survey → manuscript-scribe. manuscript-scribe does not trigger deep-survey.

## Output format (artifact — for standard/deep)

Reports must follow **../_common/reference/citation_and_output_format.md** (citation format, plain text/Markdown, units, abbreviations). The artifact file should contain:

- **Executive Summary** — at least **2-3 paragraphs** (deep); 1-2 paragraphs (standard).
- **Key Methodologies** — (deep only) table plus optional narrative.
- **State of the Art** — (deep only) **multiple subsections** with **detailed discussion**.
- **Gap Analysis** — (deep only) several elaborated points (2-4 sentences each).
- **References** (mandatory): each cited work must list **URL** (`https://doi.org/<DOI>` or paper url). Use `[n](url)` in body; list [n], full citation, and URL in References section.
- **Citation sentence format**: In [year], [first author] et al. [found that / reported that ...]; key findings include [...]. [n](url).

**Concept rigor (mandatory for deep)**: Define every key concept; explain every symbol in formulas; state how concepts relate; use examples where helpful.

**Length (deep)**: Must be a **full-length review** — not a 1-2 page brief. Develop every section fully from retrieval results.

## Scripts

### `run_survey.py`

Creates the survey **outline** (section headers + TBD) for `standard`/`deep`, or the evidence skeleton (`collected.json`) for `brief`. The LLM fills all content via retrieval calls.

- **Usage**:
  - `python run_survey.py --topic "DPA-2 for Alloys" --depth deep --output survey_dpa.md`
  - `python run_survey.py --topic "Perovskite stability" --depth brief`
  - `python run_survey.py --title "My Survey" --depth standard --output survey.md`
- **Then**: Run retrieval calls at the appropriate tier, then write content. Do not leave (TBD) in the delivered file.

### `summarize_paper.py`

Section-focused extraction from a single paper (PDF or text).

- **Usage**: `python summarize_paper.py --pdf "paper.pdf" --focus "methodology"`
- **Logic**: Extract specific sections (Methods/Exp) rather than generic summary; output JSON or text for inclusion in the survey report.

### `write_survey_report.py`

Compiles collected findings into the final structured Markdown report.

- **Usage**: `python write_survey_report.py --input "collected.json" --output "_tmp/surveys/survey_xyz.md" --topic "Perovskite stability"`

## When to use (summary)

- "Give me a comprehensive review on..." → `run_survey.py --depth deep`
- "Survey the latest progress in X" → `run_survey.py --depth deep`
- "Collect literature evidence for my paper" → `run_survey.py --depth brief` → downstream skill reads `collected.json`
- "Summarize methods for Y" (short file) → `run_survey.py --depth standard`
- "What are the common failures in VASP relaxation?" (short answer) → MCP search + answer in chat; do **not** use this skill.

## Tool (via use_skill)

- **run_script** with **script_name**: `run_survey.py`, `summarize_paper.py`, or `write_survey_report.py`; **script_args**: as in Usage above.

## Rules

- **Choose depth explicitly**: Pass `--depth brief|standard|deep` based on context. Default is `deep`.
- **LLM writes content**: The script only creates the outline/skeleton. **You** must fill content from retrieval results. Do not deliver a file that still contains (TBD).
- **brief is for downstream use**: `depth=brief` produces `collected.json`; it is not a human-readable report. Use `standard` or `deep` when the user wants a readable document.
- **Retrieval minimum is depth-dependent**: brief: 3-5 calls; standard: 6-8 calls; deep: 10-15+ calls. Do not apply the deep minimum universally.
- **Full-length retention (standard/deep)**: For every section, write the full body to a file first, then call `write_section` with `--content_file`. Never pass long section text in `--content`.
- **Delivery**: When the report is complete, first output the full final report in your reply so the user sees it; then ensure it is saved to the .md file and call finish.
- **Concept rigor (deep)**: Every key concept must have a solid definition; every formula must have every symbol explained; state how concepts relate.
- **User uploads (mandatory)**: If the user uploads files, you MUST fully parse/read every such file before writing any section.
- Always write the report to a **file**; do not stream the full review in chat.
- **One-way delegation**: deep-survey may call manuscript-scribe `write_section` for report assembly. manuscript-scribe does NOT call deep-survey.
