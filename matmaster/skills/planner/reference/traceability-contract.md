# Traceability Contract

The planner artifact stack uses stable cross-file identifiers so tasks, criteria, and requirements can be referenced unambiguously across sessions and files.

## Identifier scheme

| Prefix | Lives in | Meaning |
|--------|----------|---------|
| `S#` | `SPEC.md` | specification item (requirement, assumption, constraint, decision, open question) |
| `A#` | `ACCEPTANCE.md` | acceptance criterion |
| `P#` | `PLAN.md` / subplans | phase or stream |
| `T#` | `PLAN.md` / subplans | task |

## Concrete examples in a materials-computation project

- `S3` — target accuracy: lattice constant within 1% of experiment, PBE+D3.
- `S6` — xc functional: PBE-sol for bulk oxides, PBE for gas-phase references.
- `S9` — resource constraint: wall-clock ≤ 48 h per job on the `c32_m128_cpu` partition.
- `A2` — SCF tolerance: energy ≤ 1e-6 eV for every production run.
- `A5` — cohesive energy converged to ≤ 1 meV/atom w.r.t. k-point density.
- `A8` — deliverable: band-structure figure + DOS CSV + run metadata JSON.
- `P2` — baseline and reference-state stream (clean bulk, isolated atoms, clean surface).
- `T12` — SCF on 6×6×6 k-mesh, PBE+D3, spin-polarized, ISMEAR=0 σ=0.05.
- `T19` — post-process: DOS projection onto Fe d-orbitals from `T18` CHGCAR.

## Rules

- Keep IDs stable across revisions whenever the underlying meaning is unchanged.
- Do not silently delete, rename, or reuse a major ID. Mark retirement explicitly and do not reuse that number.
- Every important `T#` should reference its supporting `S#` and / or `A#`.
- If a major `S#` has no task coverage, say so explicitly in the plan's coverage-gap list.
- If a major `A#` has no task coverage, say so explicitly.
- Use status markers where uncertainty exists:
  - `[specified]` — committed and evidence-backed
  - `[defaulted]` — chosen by the planner under the "you decide" rule; user can override
  - `[to-research]` — known unknown, pending literature or a convergence run
  - `[blocked]` — depends on an external answer

## No silent scope reduction

- Do not shrink user scope because it feels hard, large, or inconvenient.
- If the work is too large, split it into streams or subplans.
- If information is missing, mark `[blocked]` or `[to-research]` rather than drop the requirement.
- If a requirement is intentionally deferred, say so explicitly and link back to the deferring decision.

## Resumability rule

A fresh agent must be able to recover the project state from the artifacts alone. If an artifact can only be understood by reading chat history, it is broken — fix it by moving the missing context into the artifact.
