---
name: deep-survey
description: "Retrieves literature evidence and always produces collected.json (structured evidence skeleton) regardless of depth. depth=brief outputs collected.json only (3-5 retrieval calls). depth=standard outputs collected.json + concise MD report (6-8 calls). depth=deep outputs collected.json + full 5-section review report (10-15+ calls). Use deep-survey when you need systematic literature coverage—not for quick one-off lookups or short chat answers."
skill_type: operator
---

# Deep Survey Skill

A systematic researcher that collects literature evidence and, for `standard`/`deep` depth, generates **detailed review reports saved as Markdown files**.

**When NOT to use**: If the user only wants a quick factual answer in chat (not a file), use MCP search tools directly (`web-search`, `mat_sn_search-papers-normal`) and answer in chat. Invoke deep-survey only when the output is a **file** or when another skill (e.g. composition-optimization) needs structured evidence.

## Depth tiers

All depths produce `collected.json` (structured evidence skeleton). The difference is whether a Markdown report is also written.

| depth | Retrieval calls | Output | Use when |
|-------|----------------|--------|----------|
| `brief` | 3-5 | `collected.json` only — evidence skeleton; no report | Sub-step within composition-optimization, or when only structured evidence is needed |
| `standard` | 6-8 | `collected.json` + Concise MD report (Executive Summary + References) | User wants a short survey file, or as an intermediate step with evidence persistence |
| `deep` | 10-15+ | `collected.json` + Full 5-section report (Executive Summary, Key Methodologies, State of the Art, Gap Analysis, References) | Standalone comprehensive review request |

For depth-specific facet counts and retrieval budgets, fetch: `use_skill action=get_reference skill_name="deep-survey" reference_name="search_facets_and_rounds.md"`.

## When to use deep-survey vs on-the-fly

| User intent | Use | Do not use |
|-------------|-----|------------|
| "Give me a comprehensive review on X" | **deep-survey** `--depth deep` | — |
| "Survey the latest progress in Perovskite stability" | **deep-survey** `--depth deep` | — |
| "Collect literature evidence for my paper on X" | **deep-survey** `--depth brief` | Do not use `deep`; use `brief` for evidence-only. |
| "Build candidates.csv / table from literature" | **deep-survey** `--depth brief` or `standard` → then **lit-data-organizer** | Do not use `deep` or long report as main deliverable. |
| "Summarize methods for calculating melting points" (output to file) | **deep-survey** `--depth standard` or `deep` | — |
| "What are the common failures in VASP relaxation?" (answer in chat) | MCP paper/search tools, short answer | deep-survey |
| "Quick: what is X?" / one-off definition lookup | MCP web/search, short answer | deep-survey |

## Depth-specific workflow

**After selecting depth, immediately read `prompts/<depth>.md`** (i.e. `prompts/brief.md`, `prompts/standard.md`, or `prompts/deep.md`) for the complete workflow, output rules, and quality requirements for that tier. Do not rely on memory for per-depth instructions.

Quick orientation (authoritative detail is in the per-depth prompt files):

- **brief**: 3-5 retrieval calls → `collected.json` evidence skeleton only; no Markdown report.
- **standard**: 6-8 retrieval calls → `collected.json` + concise Markdown report (Executive Summary + References).
- **deep**: 10-15+ retrieval calls → `collected.json` + full 5-section review (Executive Summary, Key Methodologies, State of the Art, Gap Analysis, References).

For depth-specific facet counts and retrieval budgets, fetch: `use_skill action=get_reference skill_name="deep-survey" reference_name="search_facets_and_rounds.md"`.

## Output and citation format

All reports follow the **citation and output format rules already injected into your system prompt** (`citation_and_output_format.md`): citation format, Markdown structure, units, abbreviation rules. This is the single source of truth for citation format — do not duplicate these rules in section content.

**Note on `manuscript-scribe` delegation**: For `deep` mode, deep-survey may delegate report writing to manuscript-scribe's `write_section` tool. One-way only: deep-survey → manuscript-scribe. manuscript-scribe does not trigger deep-survey. **When you delegate to manuscript-scribe, you MUST use profile `literature_review`** (5-section structure: Executive Summary, Key Methodologies, State of the Art, Gap Analysis, References). Do **not** use the `review` profile (that is for full 6000+ word review articles with different section layout). For evidence-only or table-building workflows, prefer `brief` or `standard` depth and do not initialize manuscript-scribe at all unless the user explicitly asked for a written report.

## Scripts

### `run_survey.py`

Creates the survey **outline** (section headers + TBD) for `standard`/`deep`, or the evidence skeleton (`collected.json`) for `brief`. For all depths, also creates a `collected_<topic>.json` skeleton in `_tmp/surveys/`. The LLM fills all content via retrieval calls.

- **Usage**:
  - `python run_survey.py --topic "DPA-2 for Alloys" --depth deep --output survey_dpa.md`
  - `python run_survey.py --topic "Perovskite stability" --depth brief`
  - `python run_survey.py --title "My Survey" --depth standard --output survey.md`
  - `python run_survey.py --topic "A vs B" --key_concepts "dipole,polarization"` *(optional: override key concepts for coverage check)*
- **Then**: Run retrieval calls. After retrieval is complete, call `collect_evidence.py` to auto-populate `evidence_cards`. Then write report content. Do not leave (TBD) in the delivered file.

### `summarize_paper.py`

Section-focused extraction from a single paper (PDF or text).

- **Usage**: `python summarize_paper.py --pdf "paper.pdf" --focus "methodology"`
- **Logic**: Extract specific sections (Methods/Exp) rather than generic summary; output JSON or text for inclusion in the survey report.

### `collect_evidence.py`

Converts raw `mat_sn_*` tool outputs into `evidence_cards` and writes them to `collected.json`. **Call this after all retrieval is done — do not manually populate evidence_cards.**

- **Usage**:
  - `python collect_evidence.py --collected_json _tmp/surveys/collected_MyTopic.json`
  - `python collect_evidence.py --collected_json _tmp/surveys/collected_MyTopic.json --facet "Mechanism"` *(recommended: assign facet at ingest so cards are tagged for this batch)*
  - `python collect_evidence.py --collected_json _tmp/surveys/collected_MyTopic.json --tool_outputs_dir _tmp/tool_outputs`
  - `python collect_evidence.py --topic "MyTopic"` *(auto-derives collected_json path and tool_outputs_dir)*
- **Supported sources**: any `mat_sn_*` paper search tool (output with `data[]` field, e.g. `mat_sn_search-papers-enhanced`, `mat_sn_search-papers-normal`, `mat_sn_scholar-search`) and any web search tool (output with `results[]` field, e.g. `web-search`). If a tool is unavailable or returns errors, switch to a different available search tool or method — do not retry the same failing tool.
- **Output**: Prints `{"status":"ok","cards_added":<n>,"cards_total":<n>,"collected_json_path":"..."}`.
- **Survey contract**: `collected_*.json` is the survey contract (schema_version 2: `source_kind`, `key_concepts`). Downstream tools and the finish gate read these fields; do not rely on path heuristics.

### `assign_facet.py` (deprecated)

Backfill facet by keyword rules for **legacy** `collected_*.json` only. **Preferred**: use `collect_evidence.py --facet <facet>` when ingesting so cards get the correct facet at ingest time.

- **Usage**: `python assign_facet.py --collected_json _tmp/surveys/collected_MyTopic.json` *(only for old workspaces or one-off repair)*
- **Output**: Prints `{"status":"ok","assigned":<n>,"cards_total":<n>,"collected_json_path":"..."}`.

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

- **run_script** with **script_name**: `run_survey.py`, `summarize_paper.py`, `collect_evidence.py`, or `write_survey_report.py`; **script_args**: as in Usage above. Use `assign_facet.py` only for legacy repair.

## Tool availability and fallback

**If any `mat_sn_*` search tool returns an error, is unavailable, or returns 0 results repeatedly**: do NOT retry the same tool. Switch to a different available search tool or method (e.g. try a different `mat_sn_*` tool, use `web-search`, or use `extract_info_from_webpage` on a known URL). If all `mat_sn_*` tools are unavailable, proceed with whatever information is available and note the limitation.

## Rules

- **Choose depth explicitly**: Pass `--depth brief|standard|deep` based on context. Default is `deep`.
- **LLM writes content**: The script only creates the outline/skeleton. **You** must fill content from retrieval results. Do not deliver a file that still contains (TBD).
- **All depths produce `collected.json`**: `depth=brief` produces only `collected.json` (no report); `standard` and `deep` produce both a Markdown report and `collected.json`. Use `standard` or `deep` when the user wants a readable document.
- **Retrieval minimum is depth-dependent**: brief: 3-5 calls; standard: 6-8 calls; deep: 10-15+ calls. Do not apply the deep minimum universally.
- **Full-length retention (standard/deep)**: For every section, write the full body to a file first, then call `write_section` with `--content_file`. Never pass long section text in `--content`.
- **Delivery**: Save the report to the .md file and call finish. For `deep` tier, also output the full report in your reply so the user sees it (see `prompts/deep.md`); for `standard`, report the file path only.
- **Concept rigor (deep)**: Every key concept must have a solid definition; every formula must have every symbol explained; state how concepts relate.
- **User uploads (mandatory)**: If the user uploads files, you MUST fully parse/read every such file before writing any section.
- Always write the report to a **file**; do not stream the full review in chat.
- **One-way delegation**: deep-survey may call manuscript-scribe `write_section` for report assembly. manuscript-scribe does NOT call deep-survey.
- **Evidence card persistence (all depths, mandatory)**: After ALL retrieval calls complete, run `collect_evidence.py` to auto-populate `evidence_cards` in `collected_<topic>.json`. Do NOT manually write evidence_cards — the script reads raw tool outputs and handles everything. Call it before writing any report sections or calling `lit-data-organizer`. An empty `evidence_cards` array after retrieval (without having called `collect_evidence.py`) is a rule violation.
- **Facet and topic-concept coverage (attention)**: Pay attention to whether each evidence card is attributable to a facet and whether the topic’s key concepts (e.g. both sides of “A vs B”) are covered in the evidence. If a key concept has almost no coverage, add retrieval or re-run collect_evidence with the right facet; finish may be blocked until key concepts are covered. For legacy data only, `assign_facet.py` can backfill facets. Prefer `collect_evidence.py --facet <facet>` when ingesting.
- **Contract before downstream**: `collected_*.json` is the survey contract (schema_version 2: `source_kind`, `key_concepts`). Use `collect_evidence.py --facet` when ingesting so downstream and gates can rely on facet and coverage; only use `assign_facet.py` for old workspaces.
