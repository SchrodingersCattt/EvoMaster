---
name: mcp-mat-recommend-db
description: Use this skill when material information (especially candidate monomers/material properties) needs to be retrieved from databases based on user requirements. Supports semantic querying, candidate filtering, property-table enrichment, and structured outputs.
skill_type: mcp-loader
mcp_server: mat_recommend_db
---

# Material Info Retrieval (MCP) — Query Guide

## Tool Mapping

- Primary tool: `fetch_material_info_from_db`
- Purpose: Retrieve materials that satisfy user requirements from databases and return candidate details plus recommendation outputs.

## Efficiency Rules

- **Limit to 1-2 main queries per user request**: Start with one broad-recall query; if results are insufficient, run one stricter or broader retry.
- **Do not retry with identical parameters**: If the first call times out or returns empty, the second call must adjust parameters (for example, `max_rows` or `use_preprocessed_filters`).
- **For batch tasks, use breadth-first strategy**: Cover all targets first, then perform focused follow-up for critical ones.
- **Reduce wasted turns**: Avoid spending more than 3 consecutive rounds on the same target.

## Recommended Call Pattern

1. Start with broad recall (high coverage):
   - `include_pr_row_properties=true`
   - `use_preprocessed_filters=false`
2. If noise is high, run one precision-focused retry:
   - `use_preprocessed_filters=true`
3. If too few candidates are returned, increase `max_rows` and retry once.

## Parameter Guide

| Parameter | Recommended Value | Notes |
|---|---|---|
| `query` | User original request (required) | Example: "Find photoresist monomers with good solubility and provide reasoning." |
| `db_names` | Empty by default | Usually let the system auto-select target databases |
| `max_rows` | 500-5000 | Candidate retrieval upper bound; increase for complex tasks |
| `include_pr_row_properties` | `true` | Enrich from property tables (for example, name/value pairs); recommended |
| `use_preprocessed_filters` | `false` (default) | Broad recall; switch to `true` if noise is high |

## Response Interpretation

Focus on these fields:

- `n_found` / `returned`: matched vs returned counts
- `by_source_found`: hit counts by data source
- `sample_rows`: sample candidates from each source
- `scored_candidates`: ranked candidates (score/confidence/reason)
- `llm_recommendation`: recommendation payload (JSON string)
- `errors`: execution or downstream reasoning errors

Notes:
- `structure_files` is usually an empty list for this tool; this is normal.
- If property enrichment is enabled, candidates may contain `properties` (a property-name/property-value list or empty).

## Query Strategy by User Intent

| User Intent | Query Strategy |
|---|---|
| "Find materials/monomers meeting constraints" | Start broad, then rank candidates by score |
| "Must satisfy explicit constraints (CAS/name/elements)" | Enable `use_preprocessed_filters=true` |
| "Need complete property details" | Ensure `include_pr_row_properties=true` |
| "Need recommendation reasons/confidence" | Use both `llm_recommendation` and `scored_candidates` in output |

## Honesty Constraint

- If the database returns no results, explicitly state: no matching materials were found.
- Do not fabricate material properties, CAS numbers, SMILES, or solubility conclusions.
- If outputs are inference-based candidate suggestions, clearly label them as "candidate/suggestion" and distinguish them from direct database facts.

## Output Style Requirement

- Prefer a structured summary:
  - Query conditions
  - Match statistics
  - Top candidates (name, SMILES, properties, score, reason)
  - Risk and uncertainty notes
- For empty results, provide actionable next steps (for example, relax constraints, increase `max_rows`, disable strict filtering).
