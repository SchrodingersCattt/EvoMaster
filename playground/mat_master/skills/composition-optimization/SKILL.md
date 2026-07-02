---
name: composition-optimization
description: Use for alloy composition search, element comparison, genetic algorithm optimization. Enforces symmetric per-candidate literature retrieval so that all candidates receive equal evidence before ranking. 
---

# Composition Optimization Skill

Guidance-only orchestrator (no runnable scripts). Load via `use_skill action=get_info`.


## Workflow

### 1. Normalize

Extract objectives, constraints, search space, and note what the user provided (data, surrogate, structures).

### 2. Candidate Screening — Symmetric Retrieval Protocol

When ranking 2+ candidate elements, the following protocol is **mandatory**.

**Step A — Assemble candidate list.**
Identify ≥3 plausible candidates from domain knowledge. Do NOT rank them yet.

**Step B — Symmetric literature search.**
For every candidate in the list, perform the **same** queries with the **same** budget:

| Query template (fill `<X>` with element name) | Purpose |
|---|---|
| `<base-alloy> Invar <X> thermal expansion Curie temperature` | TEC / magnetic effect |
| `<base-alloy> Invar <X> density FCC solubility` | Density / phase compatibility |

Rules:
- Same number of searches per candidate; no candidate may receive fewer queries.
- Do NOT discard a candidate before its searches complete.
- Record all retrieved evidence in a comparison table before proceeding.

**Step C — Build evidence table.**
Columns: Element | Density advantage | TEC evidence (quantitative preferred) | Curie-T effect | FCC solubility | Key references.

**Step D — Rank.**
- Candidates with quantitative property data (measured values) outrank those with only qualitative reasoning.
- At equal evidence tier, rank by joint-objective merit (lower TEC + lower density).
- Save table as `causal_chain.md`.

### 3. Surrogate Optimization (if available)

- If a surrogate model URL/path is provided and DART GA tool exists, run GA.
- Pass model path directly as `targets[*].model_path`.
- If surrogate absent, skip GA; return literature-based ranking only.

### 4. Composition → Structure (if needed)

Use heuristics in `reference/composition_to_structure_heuristics.md`. Validate via `structure-manager`.

### 5. Report

Ranked compositions with provenance. Disclose assumptions.

## Rules

- Never rank before symmetric retrieval completes.
- Never fabricate structure details; use heuristic generation.
- Do not run DART GA without a surrogate unless user explicitly requests it.
- Do not call `action=run_script` for this skill (guidance-only).
- DART GA tool: prefer `mat_compdart_submit_run_dart_ga` or detect `*_run_dart_ga`.
