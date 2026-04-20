---
name: plan-writer
description: "Writes or updates PLAN.md and workstream subplans as resumable trackers — decomposes a materials-computation project into phases and tasks with explicit dependencies, blockers, resume points, and links to SPEC / ACCEPTANCE."
skill_type: operator
---

# PLAN Writer Skill

Produce or revise the master `PLAN.md` and, when needed, workstream subplans under `plans/`, so a fresh agent can pick up the work without chat history. PLAN is the stateful work layer — it owns **how the work is organized and where we are in it**. It does not redefine scope and does not replace `SPEC.md` or `ACCEPTANCE.md`.

## What PLAN.md owns (and does not)

| Owns | Does not own |
|------|--------------|
| master topology, streams, phases, tasks | problem / system definition (→ `SPEC.md`) |
| dependencies, blockers, exit gates | pass/fail thresholds (→ `ACCEPTANCE.md`) |
| current active queue, ready-now, blocked-now, resume-from | bulky parameter catalogs, long tables, appendix material |
| approval state kept separate from work state | single-document narrative substitute for `SPEC.md` |
| links from tasks to `S#` and `A#` | silent scope reduction |

See `reference/mandatory-artifact-read.md` for required prior reads, and `reference/traceability-contract.md` for `P#` / `T#` conventions and cross-file linking.

## Typical materials-computation stream layout

Most projects break into some of these streams. Use them as a starting shape — merge or split based on the actual work.

| Stream | Purpose | Typical exit gate |
|--------|---------|-------------------|
| Convergence | cutoff, k-mesh, smearing, integration grids | chosen parameters justified against the relevant `A#` |
| Baseline / reference states | isolated atoms, clean bulk, clean surface, molecular references | reference energies recorded with identical settings to production |
| Structure generation / relaxation | bulk, surface, defect, supercell, molecule, adsorbate | relaxed structures archived with forces below threshold |
| Production calculations | SCF + NSCF, bands, DOS, phonons, MD, NEB, AIMD, optical, transport | all planned runs complete, logs parsed, results tabulated |
| Post-processing and analysis | charge density, projections, thermodynamics, ML featurization, statistical aggregation | derived quantities computed and checked against each `A#` |
| Report and deliverables | figures, tables, summary, supplementary data, data package | deliverables match the ACCEPTANCE deliverable criteria |

Use **rolling-horizon detail**: current and next phases decomposed to task level; later phases stay as coarse packages until upstream gates pass.

## Master PLAN.md minimum structure

1. State Snapshot — document status, work status, current stream / phase / task, ready-now, blocked-now, resume-from
2. Plan Topology — stream list, phase breakdown, dependency sketch
3. Active Streams
4. Cross-Stream Dependencies
5. Active Queue — next 1–3 concrete actions
6. Global Blockers and Escalation
7. Session Log
8. Approval and Handoff State

## Subplan minimum structure

Create a subplan at `plans/<stream-slug>.md` when a stream meets at least one of:
- spans multiple sessions,
- has its own blockers or exit gate,
- can proceed semi-independently of other streams,
- contains more than ~12 actionable tasks,
- would otherwise bloat the master plan.

Each subplan contains:
1. State Snapshot (same fields as master)
2. Stream Goal and Boundaries
3. Phases and Tasks
4. Active Queue
5. Blockers, Fallbacks, and Escalation
6. Expected Outputs and Evidence
7. Session Log
8. Closure Criteria

## Task table fields

Minimum columns per task row:

| Field | Example |
|-------|---------|
| ID | `T12` |
| Task | `SCF on 6×6×6 k-mesh, PBE+D3, spin-polarized, ISMEAR=0 σ=0.05` |
| Owner / mode | `execute-mode @ HPC-slurm partition=c32` |
| Status | `ready` / `in-progress` / `blocked` / `completed` / `superseded` |
| Depends on | `T07` (relaxed structure), `T09` (converged cutoff) |
| Spec refs | `S3`, `S6` |
| Acceptance refs | `A2` (SCF tolerance), `A5` (k-conv) |
| Deliverable / artifact path | `runs/bulk_scf_k666/OUTCAR` |
| Exit criteria | SCF converged ≤ 1e-6 eV, max force recorded, OUTCAR archived |
| Notes | est. wall-clock 6 h on c32; checkpoint every 30 min |

## Materials-specific dependency patterns

- **Convergence gates everything downstream.** Production runs must depend on the `T#` that records the chosen cutoff and k-mesh.
- **Reference-state consistency is a dependency, not a convention.** Tasks that compare energies must consume reference-state artifacts computed with the same xc, pseudopotentials, grids, and corrections — name the artifact in the `Depends on` column.
- **Relaxation precedes property calculations.** Every property task records which relaxation artifact it consumes (path or commit hash).
- **Multi-step handoffs must be explicit.** SCF → NSCF → DOS / bands; geometry → phonon → thermo — each step has a named handoff artifact.
- **HPC realities belong in PLAN.** Wall-clock budgets, checkpoint cadence, restart protocols for long MD / NEB / AIMD, queue fallbacks.

## What PLAN must NOT absorb

- Long parameter tables or pseudopotential catalogs — keep in `reference/` or an appendix doc.
- Full literature review — put anchors in `SPEC.md`, details in a research note.
- Running logs of scientific interpretation — use a notes doc; `PLAN.md` only captures "what moved the state".

## Output contract

- Create or update the master `PLAN.md`.
- Create or update any required subplans under `plans/<stream-slug>.md`.
- Keep existing `P#` / `T#` IDs stable where meaning is unchanged.
- Return a short summary that names:
  - which streams exist and which are currently active;
  - the current stream / phase / task;
  - what is ready now and what is blocked now;
  - which `S#` / `A#` still lack plan coverage (hand this directly to the plan checker).
