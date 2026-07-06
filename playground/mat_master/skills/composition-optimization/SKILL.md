---
name: composition-optimization
description: Alloy composition search with symmetric evidence collection and source-reliability-aware ranking.
---

# Composition Optimization Skill

Guidance-only orchestrator. Load via `use_skill action=get_info`.

## Workflow

### 1. Normalize

Extract from user prompt:
- Target properties and direction (minimize/maximize/range).
- Base alloy system.
- Constraints (composition bounds, phase, excluded elements).
- Available tools: surrogate models, literature retrieval, rule-based estimation.

### 2. Candidate Screening — Symmetric Protocol

**Step A — Assemble candidates.** Identify ≥6 plausible candidates. Do NOT rank yet.

**Step B — Evidence collection (symmetric and multi-source).**
For every candidate with equal effort:
1. Compute any rule-based or exactly-calculable properties.
2. Perform literature search (same query templates, same budget per candidate).
3. If surrogate available, predict target properties; note whether candidate is in-distribution (IND) or out-of-distribution (OOD).

**Step C — Source-reliability classification.**
Before ranking, tag each evidence item:
- **Exact**: rule-based calculations from tabulated constants. Usable as hard constraint.
- **IND surrogate**: model prediction within training chemistry. Usable as ranking signal.
- **OOD surrogate**: model prediction outside training chemistry. High uncertainty; do not use alone for decision.
- **Literature**: experimental or computational reports. Tier by directness (T1–T4).

**Step D — Build evidence table.**
Columns: candidate, each property, source tag, keep/reject.
Every candidate must appear. Never output only the winner.

Example (adapt columns to your task):

| Candidate | Property P (source) | Property Q (source) | Property R (source) | Keep/Reject | Reason |
|---|---|---|---|---|---|
| XX | meets target (exact) | pred ± σ (IND) | T2: one study supports | Keep | passes hard constraint, surrogate corroborates |
| YY | fails target (exact) | pred ± σ (IND) | T1: well-studied | Reject | fails exact constraint despite good surrogate |
| ZZ | meets target (exact) | pred ± σ (OOD) | T4: no direct study | Keep | passes exact; OOD unreliable, defer to literature |

**Step E — Rank.**
1. Apply exact hard constraints first (discard candidates that fail).
2. Among survivors, rank by joint objective using reliable signals.
3. OOD surrogate predictions serve only as tie-breakers or exploration cues.
4. Literature evidence resolves ties or corroborates/refutes uncertain predictions.

Save the table as `causal_chain.md`.

### 3. Surrogate Optimization (if available)

- Run GA within the element space selected through screening.
- For each target in the GA fitness function, classify its reliability:
  - Properties from tabulated constants or exact rules → hard constraints.
  - Surrogate predictions within training chemistry (IND) → ranking signal.
  - Surrogate predictions outside training chemistry (OOD) → uncertain; do not let them override exact constraints or strong literature.
- If surrogate is absent or all candidates are OOD, return the ranking from Step E without GA.

### 4. Report

Ranked compositions with provenance. `causal_chain.md` must show how each candidate was kept or eliminated and why.

## Rules

- Never rank before symmetric evidence collection completes for all candidates.
- Never let a single OOD surrogate prediction override an exact property or strong literature.
- Never output only the winning candidate; the rejection ledger is mandatory.
