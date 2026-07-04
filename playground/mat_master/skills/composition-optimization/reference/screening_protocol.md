# Screening Protocol — Symmetric Retrieval

When ranking 2+ candidate elements or compositions, this protocol is **mandatory**.

## Step A — Assemble Candidate List

Identify ≥6 plausible candidates from domain knowledge. Do NOT rank or discard any yet.

Include at least 2 elements from outside the 3d transition metal series to ensure
diversity (e.g., Si, Ge, Ga, Sn from p-block).

## Step B — Define Comparison Axes

Before searching, list property axes relevant to the objective:
- Effect on primary target property (e.g., TEC)
- Density contribution (elemental density for linear mixture)
- Phase solubility in target crystal structure
- Effect on secondary properties (e.g., Curie temperature)

These become your evidence-table columns.

## Step C — Symmetric Literature Search

For every candidate, perform the **same** query templates with the **same** budget.

Template pattern (adapt `<base-alloy>` and `<property>` to task):
- `<base-alloy> <X> <primary-target-property>`
- `<base-alloy> <X> <secondary-property-or-constraint>`

Rules:
- Same number of searches per candidate; no candidate may receive fewer queries.
- Do NOT discard a candidate before its searches complete.
- Record all retrieved evidence in a comparison table before proceeding.

## Step D — Build Evidence Table

Use axes from Step B as columns. Mark each cell with evidence tier:

| Tier | Definition |
|---|---|
| T1 | Quantitative experimental measurement in same or closely related alloy |
| T2 | Experimental data in related but not identical system |
| T3 | Computational prediction (DFT, MD, surrogate model) |
| T4 | Qualitative reasoning only |

## Step E — Constraint Verification + Rank

1. Re-read constraints from Step 1 (Normalize). For each candidate, verify ALL
   hard constraints (target direction, phase requirements, solubility bounds).
   **Discard any candidate that violates a hard constraint**, regardless of tier.

2. Among remaining candidates: higher evidence tier wins over lower.

3. At equal tier, rank by joint-objective merit.

4. Save completed table as `causal_chain.md`.

## Critical: Do NOT Pre-Narrow Before GA

If DART GA is available, run it on ALL candidates passing Step E constraint
check (not just top 2-3). GA results provide T3-level evidence that may
overturn literature-only rankings. See `ga_submission.md` for submission details.
