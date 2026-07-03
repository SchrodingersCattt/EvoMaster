---
name: composition-optimization
description: Use for alloy composition search, element comparison, genetic algorithm optimization. Enforces symmetric per-candidate literature retrieval so that all candidates receive equal evidence before ranking.
---

# Composition Optimization Skill

Guidance-only orchestrator (no runnable scripts). Load via `use_skill action=get_info`.

## Workflow

### 1. Normalize

Extract from the user prompt:
- Target property/properties and their desired direction (minimize, maximize, range).
- Base alloy system.
- Constraints (composition bounds, phase requirements, excluded elements).
- Available assets: initial data, surrogate model URL, explicit structures.

### 2. Candidate Screening — Symmetric Retrieval Protocol

When ranking 2+ candidate elements or compositions, the following protocol is **mandatory**.

**Step A — Assemble candidate list.**
Identify ≥3 plausible candidates from domain knowledge. Do NOT rank or discard any yet.

**Step B — Define comparison axes.**
Before searching, list the property axes relevant to the user's objective (e.g. effect on target property, density contribution, phase solubility, Curie temperature shift). These become your evidence-table columns.

**Step C — Symmetric literature search.**
For every candidate, perform the **same** query templates with the **same** budget:

Template pattern (adapt `<base-alloy>` and `<property>` to the task):
- `<base-alloy> <X> <primary-target-property>`
- `<base-alloy> <X> <secondary-property-or-constraint>`

Rules:
- Same number of searches per candidate; no candidate may receive fewer queries.
- Do NOT discard a candidate before its searches complete.
- Record all retrieved evidence in a comparison table before proceeding.

**Step D — Build evidence table.**
Use the axes defined in Step B as columns. Mark each cell with evidence tier:
- T1: quantitative experimental measurement in the same or closely related alloy system.
- T2: experimental data in a related but not identical system.
- T3: computational prediction (DFT, MD).
- T4: qualitative reasoning only.

**Step E — Constraint verification + Rank.**
1. Re-read the constraints extracted in Step 1. For each candidate, verify it satisfies ALL hard constraints (direction of each target property, phase requirements, solubility bounds). Discard any candidate that violates a hard constraint, regardless of evidence tier.
2. Among remaining candidates: higher evidence tier wins over lower, regardless of qualitative argument strength.
3. At equal tier, rank by joint-objective merit.
- Save the completed table as `causal_chain.md`.

### 3. Surrogate Optimization (if available)

- If a surrogate model URL/path is provided and DART GA tool exists, run GA.
- Pass model path directly as `targets[*].model_path`.
- If surrogate absent, skip GA; return literature-based ranking only.

### 4. Composition → Structure (if needed)

Use heuristics in `reference/composition_to_structure_heuristics.md`. Validate via `structure-manager`.

### 5. Report

Ranked compositions with provenance. Disclose assumptions and evidence gaps.

## Rules

- Never rank before symmetric retrieval completes.
- Never fabricate structure details; use heuristic generation.
- Do not run DART GA without a surrogate unless user explicitly requests it.
- Do not call `action=run_script` for this skill (guidance-only).
- DART GA tool: prefer `mat_compdart_submit_run_dart_ga` or detect `*_run_dart_ga`.
