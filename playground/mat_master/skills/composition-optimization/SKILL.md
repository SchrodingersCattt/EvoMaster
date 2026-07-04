---
name: composition-optimization
description: Alloy composition optimization via symmetric literature retrieval and DART genetic algorithm. Supports bohrium-job submission (preferred) and MCP mat_compdart fallback.
skill_type: hybrid
---

# Composition Optimization Skill

Multi-objective alloy composition search combining literature evidence with
surrogate-model GA optimization.

## Quick Workflow

1. **Normalize** — extract targets, constraints, base alloy from user prompt.
2. **Screen** — symmetric retrieval protocol (≥6 candidates, equal queries).
   → See [`reference/screening_protocol.md`](reference/screening_protocol.md)
3. **Optimize** — run DART GA on ALL constraint-passing candidates (not just top 2-3).
   → See [`reference/ga_submission.md`](reference/ga_submission.md)
4. **Compare** — rank by GA-optimized joint objective (TEC + density).
5. **Report** — output `recommendation.json` with provenance.

## Sub-Document Index

| Document | Purpose |
|---|---|
| [`reference/screening_protocol.md`](reference/screening_protocol.md) | Symmetric retrieval, evidence tiers, constraint gate |
| [`reference/ga_submission.md`](reference/ga_submission.md) | DART GA via bohrium-job (image, machine, workflow) |
| [`reference/ga_config_schema.md`](reference/ga_config_schema.md) | GA config JSON schema and field reference |
| [`reference/composition_to_structure_heuristics.md`](reference/composition_to_structure_heuristics.md) | Structure generation from composition |
| [`examples/ga_config_invar.json`](examples/ga_config_invar.json) | Working example for Fe-Ni Invar system |

## Scripts

| Script | Purpose | Invocation |
|---|---|---|
| `prepare_ga_config.py` | Generate `ga_config.json` + `run_ga.py` wrapper | `use_skill composition-optimization run_script prepare_ga_config.py --help` |
| `parse_ga_results.py` | Parse GA output → ranked compositions | `use_skill composition-optimization run_script parse_ga_results.py --help` |

## Key Rules

- Never rank before symmetric retrieval completes for ALL candidates.
- Run GA on ALL constraint-passing candidates — do NOT pre-narrow to top 2-3.
- Prefer bohrium-job submission over MCP `mat_compdart` (more stable).
- If GA fails after 2 retries, compute linear mixture density manually as fallback.
- Never fabricate structure details; use heuristic generation.
