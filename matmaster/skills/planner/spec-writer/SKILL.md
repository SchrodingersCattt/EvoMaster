---
name: spec-writer
description: "Writes or updates SPEC.md — the stable problem contract that freezes objective, scope, system definition, assumptions, and evidence anchors for a materials-computation project so later planning does not drift."
skill_type: operator
---

# SPEC Writer Skill

Produce or revise `SPEC.md` so a later planning or execution agent can continue the work from the artifact alone, without access to chat history. SPEC is the stable semantics layer — it defines **what we are doing and why**, not how we will do it and not what counts as done.

## What SPEC.md owns (and does not)

| Owns | Does not own |
|------|--------------|
| problem statement, objective, deliverables | task decomposition, queues, progress |
| scope and explicit non-scope | pass/fail thresholds (→ `ACCEPTANCE.md`) |
| system / model definition | blocker state, work status (→ `PLAN.md`) |
| assumptions, constraints, defaults | long narrative of session history |
| evidence anchors (DOIs, prior calcs, experimental data) | bulky catalogs that do not shape the next decision |
| stable `S#` identifiers | renumbering across revisions |

See `reference/mandatory-artifact-read.md` for required prior reads, and `reference/traceability-contract.md` for `S#` conventions and status markers.

## Materials-computation content checklist

Cover each item when it applies. If it does not apply, say so explicitly and why.

- **System**: material / molecule / interface / defect / reaction name, formula, composition.
- **Structure context**: periodic vs non-periodic, phase / polymorph / surface termination / supercell size / defect site / adsorbate orientation.
- **Electronic state**: charge state, spin / magnetism, open vs closed shell, expected magnetic ordering.
- **Environment**: temperature, pressure, solvent, vacuum gap, electrochemistry conditions, boundary conditions.
- **Method tier**: target accuracy (GGA / meta-GGA / hybrid / CCSD(T) / post-HF / DMFT / ML potential), basis / pseudopotential family, correction schemes (D3, +U, SOC, vdW).
- **Operator constraints**: software stack (VASP / QE / ABACUS / CP2K / LAMMPS / …), HPC partition, queue limits, wall-clock budgets, data storage, checkpoint policy.
- **Evidence anchors**: experimental values, reference calculations, literature with DOI / arXiv / internal memo ID, prior group results.

## S# item template

Every important requirement, assumption, constraint, or decision gets a stable `S#` tag:

```markdown
### S3 — Target accuracy tier
- Statement: lattice parameter within 1% of experimental value, forces converged to 0.01 eV/Å.
- Status: [specified]
- Rationale: consistent with the group baseline for oxide bulk phases; tighter than screening tier.
- Evidence: Chem. Mater. 2021, 33, 1234 (DOI: 10.xxx); internal memo 2024-07.
- Impacts: xc functional choice (S6), k-convergence criterion (A5).
```

Status markers (required where uncertainty exists):
- `[specified]` — committed, backed by evidence or user decision
- `[defaulted]` — chosen by the planner under the "you decide" rule, user can override
- `[to-research]` — known unknown, pending literature or a convergence run
- `[blocked]` — depends on an external answer we do not have yet

## Materials-specific evidence anchor patterns

- **Literature**: author, year, DOI or arXiv ID, and the specific value or figure being cited (not the whole paper).
- **Prior calculation**: commit hash or project path plus which deliverable justified the choice (e.g. `runs/bulk_scf_k666/OUTCAR` → cohesive energy).
- **Experiment**: sample ID, technique, measured value with uncertainty.
- **Heuristic / rule-of-thumb**: name the rule and why it applies here (e.g. "GGA underbinds noble-gas dimers → use D3-BJ").

## Recommended output path

```
docs/<topic-slug>/SPEC.md
```

## Preferred structure

1. Problem Statement
2. Objective and Intended Deliverables
3. Scope
4. Explicit Non-Scope
5. Research System / Model Definition
6. Constraints and Assumptions
7. Evidence Anchors
8. Major Decisions and Rationale
9. Open Questions and Research Gaps
10. Interfaces to `ACCEPTANCE.md` and `PLAN.md`

## Output contract

- Create or update `SPEC.md` at the recommended path.
- Keep existing `S#` IDs stable where meaning is unchanged; mark retired IDs explicitly and do not reuse their numbers.
- Return a short summary that names:
  - which `S#` items were added, updated, or retired;
  - what remains `[to-research]` or `[blocked]`;
  - which downstream artifacts (`ACCEPTANCE.md`, `PLAN.md`) must be revised as a consequence.
