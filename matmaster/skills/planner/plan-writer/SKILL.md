---
name: plan-writer
description: "Writes or updates <planning-unit>/PLAN.md and any subplans under <planning-unit>/plans/ as resumable execution trackers — decomposes a materials-computation planning unit into phases and tasks with explicit dependencies, blockers, resume points, exit gates, and links to SPEC / ACCEPTANCE."
skill_type: operator
---

# PLAN Writer Skill

Produce or revise `<planning-unit>/PLAN.md` and, when needed, subplans under `<planning-unit>/plans/`, so a fresh agent, direct mode, or `plan-executor` can continue the work from the artifacts alone.

`PLAN.md` is the stateful work layer.
It owns **how the work is organized, what depends on what, what is active now, what is blocked, and where execution should resume**.
It does not redefine problem semantics from `SPEC.md`, and it does not replace quantitative acceptance semantics from `ACCEPTANCE.md`.

## Planning-unit targeting and prior reads

Resolve the target planning unit in this order:

1. an explicit planning-unit path or artifact path from the caller or user;
2. `docs/WORKSPACE.md` → `Current Planning Unit`;
3. the parent directory of an existing `PLAN.md` being revised.

If the target planning unit cannot be determined safely, report uncertainty and do not write to an ambiguous location.

Before writing:

1. read `docs/WORKSPACE.md` if present;
2. read `<planning-unit>/SPEC.md`;
3. read `<planning-unit>/ACCEPTANCE.md`;
4. read the existing `<planning-unit>/PLAN.md` if present;
5. read any active or relevant subplans under `<planning-unit>/plans/` if present;
6. read `<planning-unit>/REPLAN-REQUEST.md` or `<planning-unit>/BLOCKER-NOTE.md` if present and relevant to the revision;
7. read any user-provided files, prior runs, structures, notes, or handoff instructions explicitly in scope.

Use `reference/mandatory-artifact-read.md` and `reference/traceability-contract.md` when available.
If they are unavailable, follow the read order and traceability rules in this skill.
Never block solely because a named helper reference is unavailable.

Write only:

- `<planning-unit>/PLAN.md`
- `<planning-unit>/plans/<stream-slug>.md`

Do not create parallel copies such as `PLAN-v2.md`, `PLAN-new.md`, `plans/convergence-new.md`, or `plans/stream-copy.md`.

## Operational fallback

When named checker or helper routines are available, use them.

If they are unavailable:

- perform the equivalent manual cross-file review,
- check traceability by inspection,
- and verify resumability from the artifact state directly.

Never block solely because `plan-checker`, `stack-checker`, or another named helper is unavailable.

## What PLAN owns (and does not)

| Owns | Does not own |
|------|--------------|
| master topology, streams, phases, tasks | problem / system definition (→ `SPEC.md`) |
| dependencies, blockers, phase exit gates | pass/fail thresholds, validation methods, waiver rules (→ `ACCEPTANCE.md`) |
| current active queue, `ready-now`, `blocked-now`, `resume-from` | bulky parameter catalogs, long tables, appendix material |
| handoff entry for direct mode and `plan-executor` | single-document narrative substitute for `SPEC.md` |
| links from tasks to `S#` and `A#` | silent scope reduction or new scientific assumptions |
| stable `P#` / `T#` identifiers | renumbering across revisions |

## State model

`PLAN.md` must separate approval state from work state.

### Artifact status

Use artifact-level approval state such as:

- `draft`
- `review`
- `approved-for-handoff`
- `needs-replan`
- `superseded`

### Work status

Use plan-level work progress such as:

- `not-started`
- `in-progress`
- `blocked`
- `completed`

### Task status

Use task-level progress such as:

- `not-started`
- `in-progress`
- `blocked`
- `completed`
- `superseded`

`ready-now` is a derived list of runnable `not-started` tasks.
It is not a separate task status.

`blocked-now` is a derived list of tasks, phases, or streams currently prevented from advancing.

Do not use artifact approval state as work progress, and do not use task progress as artifact approval state.

## Write discipline

`PLAN.md` is the executable research work plan consumed by direct mode and `plan-executor`.

When creating or revising it:

- decompose work into phases or streams with stable `P#`;
- decompose executable work into bounded tasks with stable `T#`;
- make dependencies explicit;
- keep the current state recoverable through `State Snapshot`, `Active Queue`, and `resume-from`;
- keep work units concrete enough that an execution agent does not have to infer missing scientific decisions;
- preserve cross-file traceability to `S#` and `A#`;
- use rolling-horizon detail: current and next phases at task level; later phases may remain coarse;
- preserve handoff notes for direct mode and `plan-executor`;
- prefer updating the existing plan over replacing it.

Do not let `PLAN.md` become a narrative replacement for `SPEC.md` or a threshold contract replacement for `ACCEPTANCE.md`.

If a required scientific decision is missing from `SPEC.md` or `ACCEPTANCE.md`, do not invent it silently in `PLAN.md`.
Instead, flag the missing upstream artifact revision need explicitly.

## Master plan vs subplan ownership

The master `PLAN.md` owns:

- top-level stream list and overall phase topology;
- cross-stream dependencies;
- global blockers and escalations;
- the top-level `State Snapshot`;
- the primary execution entry point and handoff notes.

A subplan under `<planning-unit>/plans/` owns:

- stream-local or workstream-local phases and tasks;
- stream-local blockers, fallback paths, and session log;
- the detailed queue when that stream is too large or stateful for the master plan alone.

If detailed execution for a stream lives in a subplan:

- the master `PLAN.md` should point to that subplan explicitly;
- the master `State Snapshot` should identify the active stream / phase / task and, when relevant, the active subplan path;
- the master plan should not duplicate the full detailed task table already owned by the subplan.

Create a subplan when one or more of the following is true:

- the workstream spans multiple sessions;
- it has its own blockers or exit gate;
- it can proceed semi-independently of other streams;
- it contains more than about 12 actionable tasks;
- it would otherwise bloat the master plan.

## Typical materials-computation stream layout

Most projects break into some of these streams.
Use them as a starting shape — merge or split based on the actual work.

| Stream | Purpose | Typical exit gate |
|--------|---------|-------------------|
| Convergence | cutoff, k-mesh, smearing, integration grids | chosen parameters justified against the relevant `A#` |
| Baseline / reference states | isolated atoms, clean bulk, clean surface, molecular references | reference energies recorded with identical settings to production |
| Structure generation / relaxation | bulk, surface, defect, supercell, molecule, adsorbate | relaxed structures archived with forces below threshold |
| Production calculations | SCF + NSCF, bands, DOS, phonons, MD, NEB, AIMD, optical, transport | all planned runs complete, logs parsed, results tabulated |
| Post-processing and analysis | charge density, projections, thermodynamics, ML featurization, statistical aggregation | derived quantities computed and checked against each relevant `A#` |
| Report and deliverables | figures, tables, summary, supplementary data, data package | deliverables match the acceptance deliverable criteria |

## Master PLAN.md minimum structure

Every master `PLAN.md` should include at least:

1. `Artifact Status`
2. `State Snapshot`
   - artifact status
   - work status
   - current stream
   - current phase
   - current task
   - `ready-now`
   - `blocked-now`
   - `resume-from`
3. `Plan Topology`
4. `Phase Graph` or `Stream Map`
5. `Cross-Stream Dependencies`
6. `Coverage Gaps and Global Blockers`
7. `Active Queue`
8. `Handoff Notes`
9. `Session Log`

`Active Queue` should contain the next 1–3 concrete actions for the current phase or active stream.

`resume-from` should point to an exact artifact location such as:

- a section heading,
- a `P#` block,
- a `T#` row,
- or a subplan path plus anchor.

## Subplan minimum structure

Each subplan at `<planning-unit>/plans/<stream-slug>.md` should include at least:

1. `Artifact Status`
2. `State Snapshot`
3. `Stream Goal and Boundaries`
4. `Phases and Tasks`
5. `Active Queue`
6. `Blockers, Fallbacks, and Escalation`
7. `Expected Outputs and Evidence`
8. `Session Log`
9. `Closure Criteria`
10. `Handoff Notes` when execution should enter here directly

## P# / T# traceability rules

Use stable identifiers.

- Important phase blocks should have stable `P#` identifiers.
- Concrete executable tasks should have stable `T#` identifiers.
- Tasks should reference relevant `S#` and `A#`.
- Keep existing `P#` / `T#` numbers when meaning is unchanged.
- Never renumber reviewed items.
- If a phase or task is retired, keep the identifier and mark it `superseded` with a brief rationale.
- Do not reuse retired identifiers.

If a major `S#` or `A#` lacks plan coverage, call that out explicitly under `Coverage Gaps and Global Blockers` instead of pretending the plan is complete.

## Task table fields

Minimum columns per task row:

| Field | Example |
|-------|---------|
| ID | `T12` |
| Task | `SCF on 6×6×6 k-mesh, PBE+D3, spin-polarized, ISMEAR=0 σ=0.05` |
| Owner / mode | `execute-mode @ HPC-slurm partition=c32` |
| Status | `not-started` / `in-progress` / `blocked` / `completed` / `superseded` |
| Depends on | `T07` (relaxed structure), `T09` (converged cutoff) |
| Spec refs | `S3`, `S6` |
| Acceptance refs | `A2`, `A5` |
| Deliverable / artifact path | `runs/bulk_scf_k666/OUTCAR` |
| Exit criteria | `SCF converged ≤ 1e-6 eV; OUTCAR archived; max force recorded` |
| Notes | `est. wall-clock 6 h on c32; checkpoint every 30 min` |

Task-writing rules:

- task names must be concrete and executable;
- deliverable paths must be exact, not implied;
- exit criteria must be checkable by an executor and verifier;
- every nontrivial task should map to at least one relevant `S#` or `A#`;
- avoid vague tasks such as `run DFT`, `analyze results`, or `do convergence` unless the exact scientific meaning is already encoded elsewhere in the same task row.

## Materials-specific dependency patterns

- **Convergence gates downstream work.** Production runs should depend on the `T#` that records chosen cutoff, k-mesh, smearing, or equivalent settings.
- **Reference-state consistency is a dependency, not a convention.** Energy comparisons should depend on named reference-state artifacts computed with matching functional, basis / pseudopotentials, grids, and corrections.
- **Relaxation precedes property calculations.** Property tasks should name which relaxation artifact they consume.
- **Multi-step handoffs must be explicit.** SCF → NSCF → DOS / bands; geometry → phonon → thermo — each step needs an explicit handoff artifact.
- **HPC realities belong in PLAN.** Wall-clock budgets, checkpoint cadence, restart protocol, and queue fallback logic belong here when they affect execution order or resumability.
- **Method-compatibility dependencies should be explicit.** If a post-processing or comparison step requires the same `+U`, SOC, solvation, or charged-cell treatment, name that dependency.

## What PLAN must not absorb

Do not put the following into `PLAN.md` except as short references or links:

- long parameter tables or pseudopotential catalogs;
- full literature review;
- bulky appendices that do not change execution order;
- long-form scientific interpretation logs;
- hidden scope or threshold changes that really belong in `SPEC.md` or `ACCEPTANCE.md`.

`PLAN.md` should capture **what moved the work state**, not become the archive for all background material.

## Interfaces to SPEC.md, ACCEPTANCE.md, and execution

`PLAN.md` must prepare execution without replacing upstream artifacts.

It should make clear:

- which `S#` items create streams, dependencies, or special handling;
- which `A#` items impose phase exit gates, required evidence, or validation-relevant tasks;
- which issues remain uncovered and therefore block handoff;
- where direct mode or `plan-executor` should begin.

`Handoff Notes` should name:

- the files execution should read first;
- the active subplan, if any;
- the execution entry from `State Snapshot` and `Active Queue`;
- instruction to use `plan-executor` when available.

Do not redefine scope from `SPEC.md`.
Do not redefine thresholds or waiver rules from `ACCEPTANCE.md`.

## Material update rule

A material update changes one or more of:

- plan topology, stream structure, or phase decomposition;
- task graph, dependencies, or exit gates;
- current stream / phase / task;
- `ready-now`, `blocked-now`, or `resume-from`;
- handoff entry or execution ordering;
- the meaning, lifecycle, or downstream role of a `P#` or `T#`;
- coverage of major `S#` or `A#` items.

Typos, wording-only edits, and formatting cleanup do not count as material updates.

## Recommended output paths

Primary paths:

```text
<planning-unit>/PLAN.md
<planning-unit>/plans/<stream-slug>.md
```

When creating a new planning unit under the current workspace convention, the default root is:

```text
docs/<topic-slug>/
```

## Output contract

- Create or update the master `<planning-unit>/PLAN.md`.
- Create or update required subplans under `<planning-unit>/plans/<stream-slug>.md`.
- Include `Artifact Status` and `State Snapshot`.
- Keep artifact approval state separate from work progress.
- Keep existing `P#` / `T#` IDs stable where meaning is unchanged.
- Mark retired work items as `superseded` rather than deleting their identity.
- Include `Handoff Notes` that tell direct mode and `plan-executor` what to read first.
- Use `approved-for-handoff` only when `SPEC.md`, `ACCEPTANCE.md`, and `PLAN.md` are coherent, review-ready, and resumable from the artifacts alone.
- Use `needs-replan` when execution or checking found a structural issue that requires planner revision.
- Use `plan-checker` and `stack-checker` when available; otherwise perform the equivalent manual review.
- Do not silently fix missing upstream scientific decisions in `PLAN.md`; report required `SPEC.md` or `ACCEPTANCE.md` revisions explicitly.

After any create or material update, return a short checkpoint that includes:

- artifact paths created or revised;
- whether the master plan or any subplans changed materially;
- streams present and which are currently active;
- current stream / phase / task;
- what is `ready-now` and what is `blocked-now`;
- `P#` / `T#` items added, updated, or superseded;
- which `S#` / `A#` still lack plan coverage;
- whether `ACCEPTANCE.md` or `SPEC.md` should now be revised;
- one review prompt: `approve` / `revise` / `provide missing input`.

A checkpoint is satisfied when the user approves the artifact, requests revisions, provides missing input, or explicitly defers unresolved non-blocking items.
