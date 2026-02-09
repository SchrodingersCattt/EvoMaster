---
name: deep-survey
description: "Executes a COMPREHENSIVE literature review saved to a file. Use for: 'Survey the latest progress in Perovskite stability', 'Summarize methods for calculating melting points', or any request that needs a long-form review report. For one-off quick lookups or short answers in chat, use MCP search tools (e.g. mat_sn_web-search, mat_sn_search-papers-normal) instead—do NOT invoke this skill."
skill_type: operator
---

# Deep Survey Skill

A systematic researcher that generates **detailed review reports saved as Markdown files**. Distinguish from on-the-fly retrieval: use this skill only when the user explicitly wants a **written report** or **comprehensive survey**, not for quick factual answers in chat.

## When to use (deep survey) vs on-the-fly

| User intent | Use | Do not use |
|-------------|-----|------------|
| "Give me a comprehensive review on X" | **deep-survey** (`run_survey.py`) | — |
| "Survey the latest progress in Perovskite stability" | **deep-survey** | — |
| "Summarize methods for calculating melting points" (output to file) | **deep-survey** | — |
| "What are the common failures in VASP relaxation?" (answer in chat) | MCP paper/search tools, short answer | deep-survey |
| "Quick: what is X?" / one-off definition lookup | MCP web/search, short answer | deep-survey |

**Rule**: If the expected output is a **file** (e.g. `survey_TOPIC.md`) or a long-form report, use this skill. If the expected output is a **short reply in chat**, use MCP search tools and answer directly.

## Workflow (expand facets, then repeatedly retrieve)

When routing to **serious writing** (this skill), do **not** do a single shallow search. You must **expand the query into multiple facets** and **repeatedly call** retrieval tools (paper search, web search) for each facet. See **reference/search_facets_and_rounds.md** for facet types and minimum call counts.

1. **Plan (expand facets)**:
   * Analyze the topic and break it into **3–5 facets** (e.g. definition, mechanism, methods, reviews, caveats; see reference).
   * For each facet, plan **2–4 query variants** (keywords, synonyms, or alternate language; e.g. "X review", "X mechanism").
   * **Minimum**: enough queries so that total **retrieval tool calls** are at least **6–15** (e.g. 3–5 facets × 2–3 searches per facet). For deep surveys, use more rounds.
2. **Execute loop (repeated retrieval)**:
   * **For each facet and each query variant**: Call MCP retrieval tools (`mat_sn_search-papers-normal`, `mat_sn_scholar-search`, `mat_sn_web-search`, etc.) **repeatedly**—do not stop after one or two searches.
   * After each search: filter for relevance; keep only hits clearly related to that facet and user intent.
   * **Download**: For high-relevance papers, fetch full text where possible (e.g. structure-manager `fetch_web_structure.py` for known URLs, or MCP document extraction).
   * **Read**: Use RAG skill (`rag/scripts/search.py`) or PDF/document tools to extract key findings (Method, Result, Metrics). Optionally use `summarize_paper.py` for section-focused extraction.
3. **Write the report (LLM)**: Using the retrieval results in context, **you (the LLM)** write each section into the survey file: Executive Summary, Key Methodologies, State of the Art, Gap Analysis, References. Use **manuscript-scribe** `write_section` (with `--content_file` for long sections) or **str_replace_editor** to replace each (TBD) with full content. The script only creates the outline; all substantive content is written by you from the search results.

## Output format (artifact)

Reports must follow **../_common/reference/citation_and_output_format.md** (citation format, plain text/Markdown, units, abbreviations). The artifact file should contain:

* **Executive Summary**
* **Key Methodologies** (table format)
* **State of the Art** (results comparison)
* **Gap Analysis** (what is missing?)
* **References** (mandatory): each cited work must list **URL** (from search: use doi as `https://doi.org/<DOI>` or the paper’s url field). Use `[n](url)` in the body; in References list [n], full citation, and URL. If the user asks for links/URLs/链接, do not omit them.

## Scripts

### `run_survey.py`

Creates the survey **outline** (section headers + TBD) and a search plan. **You (the LLM)** fill all content: after running 6–15+ retrieval calls, use `write_section` or `str_replace_editor` to write Executive Summary, Key Methodologies, State of the Art, Gap Analysis, and References from the retrieval results. No Python report generation—content is LLM-written.

* **Usage**: `python run_survey.py --topic "DPA-2 for Alloys" --depth deep --output survey_dpa.md` (or `--title` as alias for `--topic`).
* **Then**: Run retrieval (mat_sn_*), then write each section into the survey file with manuscript-scribe or str_replace_editor. Do not leave (TBD) in the delivered file.

### `summarize_paper.py`

Section-focused extraction from a single paper (PDF or text).

* **Usage**: `python summarize_paper.py --pdf "paper.pdf" --focus "methodology"`
* **Logic**: Extract specific sections (Methods/Exp) rather than generic summary; output JSON or text for inclusion in the survey report.

### `write_survey_report.py`

Compiles collected findings into the final structured Markdown report (Executive Summary, Methodologies, State of the Art, Gap Analysis, References). Can be called by `run_survey.py` or after manual collection.

* **Usage**: `python write_survey_report.py --input "collected.json" --output "_tmp/surveys/survey_xyz.md" --topic "Perovskite stability"`

## When to use (summary)

* "Give me a comprehensive review on..." → `run_survey.py`
* "Survey the latest progress in X" → `run_survey.py`
* "Summarize methods for Y" (long-form to file) → `run_survey.py` (topic: Y methods)
* "What are the common failures in VASP relaxation?" (short answer) → Use MCP search + answer in chat; do **not** use this skill unless the user asks for a written report.

## Tool (via use_skill)

- **run_script** with **script_name**: `run_survey.py`, `summarize_paper.py`, or `write_survey_report.py`; **script_args**: as in Usage above.

## Rules

* **LLM writes content**: The script only creates the outline. **You** must write Executive Summary, Key Methodologies, State of the Art, Gap Analysis, and References (using write_section or str_replace_editor) from the retrieval results. Do not deliver a file that still contains (TBD).
* **Delivery**: When the report is complete, **first output the full final report** in your reply (message text) so the user sees it in the chat/frontend; then ensure it is saved to the survey .md file and call finish. Do not only write to file and say "Saved to path".
* **Expand facets, repeated retrieval**: For serious writing (this skill), **expand the query into multiple facets** and **repeatedly call** retrieval tools (paper search, web search)—**at least 6–15 retrieval calls** across facets; never a single shallow search. See reference/search_facets_and_rounds.md.
* Prefer academic sources (peer-reviewed papers, scholar results) for literature/review tasks; treat web-only hits as supplementary.
* After each search, filter by relevance; do not pass irrelevant URLs to extraction.
* Always write the report to a **file**; do not attempt to stream the full review in chat (token limit). In chat, report: "Survey completed. Saved to &lt;path&gt;. Please open the file."
