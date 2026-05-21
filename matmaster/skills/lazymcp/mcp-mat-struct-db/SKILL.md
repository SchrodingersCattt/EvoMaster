---
name: mcp-mat-struct-db
description: 当需要从数据库检索已知晶体结构时调用本 skill。支持按化学式、组成、材料 ID、原型检索，返回 CIF/POSCAR。
skill_type: mcp-loader
mcp_server: mat_struct_db
---

# Structure Database (MCP) — Query Guide

## Efficiency Rules

- **Budget 1–2 query attempts per target compound**. If the first query fails or times out, try ONE alternative query form (different formula notation, composition range, or material ID). If both fail, move on.
- **Batch tasks (≥5 structures)**: Query breadth-first — submit all queries before waiting. Do not spend >3 turns on any single entry. **Turn economy is critical**: plan the minimum number of queries to cover all targets, batch independent queries in parallel, and fall back to local construction (pymatgen `from_spacegroup`) immediately when DB results are incomplete.
- **Timeout handling**: If the MCP tool returns a timeout or empty result, **do not retry the same query more than once**. Switch to an alternative source (literature search, web databases) or honestly report that the database did not return results.
- **Download contains only summary.json**: The `fetch_structures_from_db` download tarball sometimes contains only `summary.json` with metadata (formula, space group, lattice parameters) but **no actual CIF/POSCAR files**. The `structure_file` paths in the summary refer to server-internal locations and are not included in the download. When this happens:
  1. Extract lattice parameters, space group, and Wyckoff positions from the summary metadata.
  2. Build structures locally with `pymatgen Structure.from_spacegroup(...)` using the extracted parameters.
  3. **Do not** issue additional DB queries trying to get the CIF files — this wastes turns.
  4. Save built structures as CIF, then validate with `assess_structure.py` if the retrieve-structure skill is loaded.

## Query Strategies

| What you have | Query approach |
|---------------|---------------|
| Exact formula (e.g. LaH₁₀) | `fetch_structures_from_db` by formula |
| Composition system (e.g. La-H) | Query by composition/elements |
| Material ID (mp-XXXX, ICSD-XXXX) | Query by ID directly |
| Prototype (perovskite, spinel) | Query by prototype |
| Approximate composition | Use composition range query |

## Honesty Constraint

- If the database does not return a structure, **state this explicitly**. Do NOT fabricate crystal parameters, lattice constants, or space groups based on intuition or literature scraps.
- When falling back to literature, clearly distinguish "retrieved from database" vs "found in literature" vs "estimated/predicted".
- Partial results are acceptable — report what was found and what was not.
