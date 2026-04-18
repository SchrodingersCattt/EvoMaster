---
name: deep-survey
description: "Systematic literature evidence retrieval producing collected.json and optional reports. Use for comprehensive literature review — not for quick one-off lookups."
skill_type: operator
depends_on: mcp-mat-doc
---

<!-- multi-server: mat_sn, mat_doc -->

# Deep Survey Skill

Systematic researcher that collects literature evidence and optionally generates review reports saved as Markdown files.

**When NOT to use**: Quick factual answers in chat — use MCP search tools directly. Invoke deep-survey only when output is a **file** or when another skill needs structured evidence.

## Depth tiers

| depth | Retrieval calls | Output | Use when |
|-------|----------------|--------|----------|
| `brief` | 3-5 | `collected.json` only | Sub-step for composition-optimization, or structured evidence only |
| `standard` | 6-8 | `collected.json` + concise MD report | User wants a short survey file |
| `deep` | 10-15+ | `collected.json` + full 5-section report | Standalone comprehensive review |

**After selecting depth, read `prompts/<depth>.md`** for complete workflow and quality requirements.

## Workflow

1. Run `run_survey.py --topic "..." --depth <tier> [--output survey.md]` to create outline/skeleton.
2. Execute retrieval calls (mat_sn_*, web-search). If a tool fails, switch to a different available tool.
3. Run `collect_evidence.py --collected_json _tmp/surveys/collected_<topic>.json [--facet "..."]` to auto-populate evidence_cards. **Mandatory** — do NOT manually write evidence_cards.
4. Write report content (standard/deep only). Do not leave (TBD) in delivered file.
5. For deep mode, may delegate report writing to manuscript-scribe with `literature_review` profile (not `review`).

## Scripts

| Script | Purpose | Key args |
|--------|---------|----------|
| `run_survey.py` | Create outline/skeleton | `--topic "..." --depth <tier> [--output file.md]` |
| `summarize_paper.py` | Section-focused extraction | `--pdf "paper.pdf" --focus "methodology"` |
| `collect_evidence.py` | Auto-populate evidence_cards | `--collected_json <path> [--facet "..."]` |
| `write_survey_report.py` | Compile final report | `--input collected.json --output survey.md --topic "..."` |

## Tool availability and fallback

If any `mat_sn_*` tool returns an error or is unavailable, switch to a different search tool (web-search, extract_info_from_webpage). Do NOT retry the same failing tool.

## Rules

- **Choose depth explicitly** via `--depth brief|standard|deep`.
- **LLM writes content**: Scripts only create outlines. You must fill content from retrieval.
- All depths produce `collected.json`. `standard`/`deep` also produce Markdown reports.
- After ALL retrieval, run `collect_evidence.py` before writing report or calling downstream skills.
- Prefer `collect_evidence.py --facet <facet>` at ingest time for proper tagging.
- `collected_*.json` is the survey contract (schema_version 2). Downstream tools read it.
- User uploads: fully parse/read every uploaded file before writing.
- One-way delegation: deep-survey → manuscript-scribe only.
