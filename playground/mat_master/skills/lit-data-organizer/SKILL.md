---
name: lit-data-organizer
description: Normalize multi-source literature evidence into a single canonical table and export CSV/JSONL. Use when users ask for literature data tables, material-property comparison matrices, dataset-style aggregation from PDF/web sources, or Pareto/plot-ready structured metrics that must be deduplicated, source-traceable, and conflict-aware.
skill_type: operator
prerequisites:
  - tool: mat_doc_extract_material_data_from_pdf
    reason: PDFs must be pre-extracted before ingestion; raw PDFs cannot be passed directly to this skill
---

# Lit Data Organizer Skill

Builds one canonical evidence table from structured literature outputs and exports it as CSV or JSONL.

## Scope

- Input sources:
  - Structured PDF extraction outputs (obtain via `mat_doc_*` first).
  - Structured web search or webpage extraction outputs.
- Output:
  - A single canonical evidence table (`lit_evidence_table`) with one row per evidence card.

## Workflow

1. Source preparation
   - For PDFs, use Mat document tools (`mat_doc_extract_material_data_from_pdf` or async submit/get pattern) before this skill.
   - For web sources, use search and webpage extraction tools to produce structured JSON.
2. Normalize and merge
   - Harmonize source fields, evidence fields, and material-property fields.
   - Keep per-record source traceability.
   - Apply deduplication by configurable keys.
   - Preserve conflicting measurements with explicit conflict metadata.
   - For long runs, use staged/resumable processing (`--state`, `--resume`, `--stage`).
3. Enrich (optional, when `--enrich` is set)
   - Run LLM in batches to decide keep/drop per row, fill material_name, property_name, property_value, property_unit (use "NA" when unknown), and optional enrich_note.
   - Survey context (topic and key_concepts) is read from collected_*.json (survey contract) when available.
   - Rows with enrich_keep=false are excluded from dedup and export.
4. Export
   - Export canonical rows to `csv` or `jsonl`.
   - Review stdout summary for counts, enrich stats (if used), and conflict statistics.

## Script

- **build_lit_table.py**
  - Merge structured input files into one canonical table.
  - Supports field mapping overrides via an external schema config.
  - Supports conflict tagging and deduplication.
  - Supports optional **enrich** stage (`--enrich`): LLM batch fill of material/property columns and keep/drop using survey topic and key_concepts; requires OpenAI-compatible API (e.g. OPENAI_API_KEY or LITELLM_PROXY_API_KEY).
  - Supports staged execution (`ingest|normalize|enrich|dedup|conflict|export|all`) with checkpoint/resume.

## Tool Usage (via use_skill)

- `run_script` with `script_name=build_lit_table.py`
- Required arguments:
  - `--output <file_path>`
  - `--format csv|jsonl`
- Common arguments:
  - `--input_json <file1> [file2 ...]`
  - `--input_dir <directory>`
  - `--source_type auto|pdf|web|survey`
  - `--schema <schema_config.json>`
  - `--dedup_keys key1,key2,...`
  - `--state _tmp/lit_data/state.json`
  - `--resume`
  - `--stage ingest|normalize|enrich|dedup|conflict|export|all`
  - `--enrich` — run LLM-based enrich stage (keep/drop, fill material/property columns; use when input is survey collected.json and you need a material-property table).
  - `--enrich_model <model>` — model for enrich (default: env LIT_ENRICH_MODEL or gpt-4o-mini).
  - `--enrich_batch <n>` — rows per LLM batch (default: 40).
  - `--enrich_survey <path>` — path to collected_*.json for survey context; auto-detected from input if omitted.

## Long-task status contract

- The script emits structured stage status as `LONGTASK_RESULT_JSON: {...}`.
- `status=completed`: stage finished successfully.
- `status=retryable_error`: stage missing prerequisite artifacts or data consistency issue; fix/re-run from checkpoint.
- `status=fatal_error`: unrecoverable runtime/argument error.
- Persistent files:
  - `state.json`: workflow state and processed input fingerprints
  - `events.jsonl`: append-only stage event log
  - `result.json`: latest normalized result envelope

## Rules

- Do not parse raw PDFs directly in this skill. Always use `mat_doc_*` first.
- Keep only one canonical table shape; do not create multiple output schemas.
- Preserve conflicting values as separate records with conflict metadata; do not silently overwrite.
- Keep skill-facing content generic and domain-agnostic.

## References

- Schema details: [canonical_evidence_schema.md](references/canonical_evidence_schema.md)
