---
name: lit-data-organizer
description: "Use after two or more literature/search calls when the user wants a canonical CSV/JSONL property table, DOI-keyed dataset, comparison matrix, or other structured literature evidence. Avoid ad hoc extraction scripts."
skill_type: operator
depends_on: mcp-mat-doc
---

<!-- multi-server: mat_sn, mat_doc -->

# Lit Data Organizer Skill

Builds one canonical evidence table from structured literature outputs; exports as CSV or JSONL.

## Workflow

1. **Source preparation**: For PDFs, use `mat_doc_*` tools first. For web sources, use search/extraction tools.
2. **Normalize and merge**: `build_lit_table.py` harmonizes fields, deduplicates, preserves conflicts with metadata.
3. **Enrich** (agent-side): Read `_tmp/lit_data/normalized_rows.json`, apply pattern-based or semantic extraction (see `references/enrich_strategy.md`), write to `_tmp/lit_data/enrich_rows.json`, then call `--stage dedup --resume`.
4. **Export**: CSV or JSONL. For business deliverables (e.g. `candidates.csv`), see `references/business_export_candidates.md`.

## Script: build_lit_table.py

```
python ${SKILL_DIR}/scripts/build_lit_table.py --output <file> --format csv|jsonl \
  [--input_json <files>] [--input_dir <dir>] [--source_type auto|pdf|web|survey] \
  [--schema <config.json>] [--dedup_keys key1,key2] \
  [--state _tmp/lit_data/state.json] [--resume] [--stage ingest|normalize|dedup|conflict|export|all] \
  [--enrich_rows <path>]
```

Emits `LONGTASK_RESULT_JSON: {...}` per stage. Persistent: `state.json`, `events.jsonl`, `result.json`.

## Rules

- Do not parse raw PDFs directly — use `mat_doc_*` first.
- Keep one canonical table shape. Preserve conflicting values as separate records.
- Enrichment: Do NOT fabricate values. Leave unmatchable as "NA".
- References: `references/canonical_evidence_schema.md`, `references/enrich_strategy.md`, `references/pattern_guide.md`, `references/business_export_candidates.md`.
