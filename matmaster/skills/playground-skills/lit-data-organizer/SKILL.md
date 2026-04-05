---
name: lit-data-organizer
description: Consolidate any multi-source literature evidence into one canonical CSV/JSONL table. Use whenever two or more mat_sn_* search calls have been made to produce a structured dataset, property table, or DOI-keyed CSV (material-property matrices, Ps/Pr datasets, comparison tables, or aggregations). Do NOT write manual extraction scripts (extract_*.py, build_dataset.py) when this skill is available; route all multi-source literature-to-CSV tasks here instead.
skill_type: operator
depends_on: mcp-mat-sn, mcp-mat-doc
---

<!-- multi-server: mat_sn, mat_doc -->

# Lit Data Organizer Skill

Builds one canonical evidence table from structured literature outputs and exports it as CSV or JSONL.

## Scope

- Input sources:
  - Structured PDF extraction outputs (obtain via `mat_doc_*` first).
  - Structured web search or webpage extraction outputs.
  - Structured survey outputs from `mat_sn_*` tools, including abstracts that contain necessary data.
- Output:
  - A single canonical evidence table (`lit_evidence_table`) with one row per evidence card. This is the **intermediate** output. For business deliverables (e.g. `candidates.csv` with columns material_name, class, Ps, Pr, DOI, reference_URL), the agent should either map columns via `--schema` at export or add a follow-up step to produce the final CSV from the canonical table (see [business_export_candidates.md](references/business_export_candidates.md)).

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
3. Enrich (agent-side fill — structured extraction before dedup)
   - After normalize completes, the agent reads `_tmp/lit_data/normalized_rows.json`.
   - **Choose one enrichment strategy** (see [enrich_strategy.md](references/enrich_strategy.md)):
     - **Pattern-based (regex)**: Fast, deterministic extraction from `quote_text` and `claim_text` for known field structures.
     - **Semantic-based (LLM)**: Higher-cost semantic understanding for complex, ambiguous, or cross-reference resolution.
   - Agent writes enriched rows to `_tmp/lit_data/enrich_rows.json` and updates `state["enrich_rows_file"]` in `_tmp/lit_data/state.json`.
   - Then call `--stage dedup --resume`; the script will auto-load `enrich_rows.json` via `state["enrich_rows_file"]`.
   - Rows with `enrich_keep=false` are excluded from dedup and export.
   - **Rules**: Do NOT fabricate values. Leave unmatchable fields as "NA". Preserve confidence/uncertainty information.
4. Export
   - Export canonical rows to `csv` or `jsonl`.
   - Review stdout summary for counts, enrich stats (if used), and conflict statistics.

## Script

- **build_lit_table.py**
  - Merge structured input files into one canonical table.
  - Supports field mapping overrides via an external schema config.
  - Supports conflict tagging and deduplication.
  - Enrich is agent-side: agent writes `enrich_rows.json` + updates `state["enrich_rows_file"]`, then calls `--stage dedup --resume`. Pass `--enrich_rows <path>` to override the state path explicitly.
  - Supports staged execution (`ingest|normalize|dedup|conflict|export|all`) with checkpoint/resume.

## Tool Usage (via Skill)

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
  - `--stage ingest|normalize|dedup|conflict|export|all`
  - `--enrich_rows <path>` — explicit path to agent-generated enrich_rows.json (overrides state["enrich_rows_file"]).

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
- Enrich strategy guide: [enrich_strategy.md](references/enrich_strategy.md)
- Pattern writing guide: [pattern_guide.md](references/pattern_guide.md)
- Business export (e.g. candidates.csv from canonical table): [business_export_candidates.md](references/business_export_candidates.md)
