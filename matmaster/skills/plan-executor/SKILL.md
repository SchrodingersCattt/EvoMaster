---
name: plan-executor
description: "Executes one planner-defined phase from a planning unit's SPEC/ACCEPTANCE/PLAN stack to its exit gate under strict state-write discipline. Use when the current planning unit contains PLAN.md or an execution handoff points to one — one invocation advances at most one phase."
skill_type: operator
---

# Plan Executor Skill

Consume the planner artifact stack (`SPEC.md`, `ACCEPTANCE.md`, `PLAN.md`, and any subplans in the planning unit's `plans/` directory) and advance work.

Never replan.
Never redefine scope.
Never perform planner-only structural edits.
Never close a phase without verification.

One invocation drives **at most one phase** to its exit gate and then terminates.
If the current phase is already completed on entry, run its gate (if not yet run) and advance `State Snapshot` only as needed to finish that phase transition — do not execute tasks from the next phase within the same invocation.

This skill is the execution counterpart to the planner's writer/checker skills.
It is read-heavy on `SPEC.md` / `ACCEPTANCE.md`, write-restricted on `PLAN.md` / subplans, and write-free on the structural parts of the plan.

All relative paths in this skill are resolved from the **planning unit root** unless a task explicitly names another location.

## Activation

Activate this skill when direct mode is executing inside a managed planning unit.

Resolve the target planning unit in this order:

1. an explicit planning-unit path or artifact path from handoff or user instruction;
2. `docs/WORKSPACE.md` → `Current Planning Unit`;
3. exactly one complete planning stack in the workspace.

If **no** planning artifacts exist, the task is ad-hoc; fall back to direct mode's default execution rules and do not invoke this skill.

If a planning unit cannot be determined safely, do not invoke this skill until the target unit is explicit.

Execution requires a complete core stack in the planning unit root:

- `SPEC.md`
- `ACCEPTANCE.md`
- `PLAN.md`

If the planning unit is managed but the core stack is incomplete, halt immediately, emit terminal state `replan-requested`, and write `<planning-unit>/REPLAN-REQUEST.md` naming the missing artifact(s).

## Operational fallback

When named domain skills, helper routines, or verification helpers are available, use them.

If they are unavailable:

- execute with the equivalent direct toolchain,
- perform the equivalent manual gate review,
- and preserve the same artifact boundaries and write restrictions.

Never request replanning solely because a named helper is unavailable.

Verification remains mandatory at phase gates even when fallback is used.

## Artifact boundaries

| Artifact | Read | Write | Notes |
|----------|------|-------|-------|
| `SPEC.md` | yes | **never** | stable problem contract; changes require planner |
| `ACCEPTANCE.md` | yes | **never** | completion contract; changes require planner |
| `PLAN.md` | yes | **restricted** | work-state only; never structural or approval-state edits |
| `plans/*.md` | yes | **restricted** | same rules as `PLAN.md`; phase-local ownership may live here |
| `runs/<T#>/*` | yes | **free** | execution evidence, archived logs, and produced deliverables unless the task declares another path |
| task-declared deliverable paths | yes | as the task prescribes | still archive or reference them from `runs/<T#>/EVIDENCE.md` |

When the current phase is executed inside a subplan:

- the subplan owns task rows and phase-local status,
- `PLAN.md` owns the top-level `State Snapshot` and stream pointer,
- and writes must still stay inside the allowed fields below.

Do not create parallel copies such as `SPEC-v2.md`, `PLAN-new.md`, or `runs/T12_v2/`.
If the current plan cannot carry the work, the response is a replan request, not a new artifact.

## Startup sequence

Every invocation must run these steps **in order** before touching any task:

0. **Resolve the planning unit.**
1. **Read `SPEC.md`.** Load all `S#` items. Capture constraints that gate downstream tasks such as XC functional, pseudopotential/basis family, dispersion treatment, magnetism, supercell policy, correction policy, and resource caps.
2. **Read `ACCEPTANCE.md`.** Load every `A#` with its threshold, evidence requirement, and validation method.
3. **Read `PLAN.md`.** Parse the `State Snapshot` block:
   - current stream / phase / task
   - `resume-from` anchor
   - `ready-now` and `blocked-now`
4. **If `State Snapshot` points into a subplan, read that `plans/<stream>.md`** and work inside its Active Queue thereafter.
5. **Report the startup position** to the parent direct-mode agent:
   - planning unit path
   - detected artifact paths
   - current `P#` and expected exit gate
   - next 1–3 `T#` items from the active queue

If any of steps 1–3 fails because a required file is missing, a required block is malformed, or the current phase has neither an in-progress task nor a usable active queue, halt immediately, emit terminal state `replan-requested`, and write `<planning-unit>/REPLAN-REQUEST.md` naming the defect.

## Task execution loop

For each `T#` popped from the Active Queue of the current phase:

1. **Validate readiness.**
   Confirm every entry in the task's `Depends on` column has Status `completed` and the named deliverable actually exists on disk.
   If not, halt the loop, mark the task `blocked`, and go to the **Blocker branch**.

2. **Mark `in-progress`.**
   Update the task row's Status using the PLAN write rules.
   Update `State Snapshot.current task` and `State Snapshot.resume-from`.

3. **Execute.**
   Perform the computation, file writes, tool invocations, or domain workflow the task prescribes.
   Use existing domain skills (`abacus`, `mlips`, `lammps`, etc.) when available.
   This skill never redefines or reimplements planner semantics.

4. **Place the deliverable.**
   Write the artifact to the exact path named in the task's `Deliverable / artifact path` column.
   Do not append suffixes such as `_v2` or `_final` unless the task itself explicitly asks for named variants.

5. **Write `runs/<T#>/EVIDENCE.md`.**
   Use the schema below.

6. **Update the task row.**
   If every exit criterion is satisfied, set Status to `completed` and append one Session Log entry of the form:

   `T12 completed at <ISO-timestamp>; evidence=runs/T12/EVIDENCE.md`

   If any exit criterion is not satisfied, do **not** mark the task `completed`; go to the **Blocker branch**.

7. Advance to the next ready `T#` in the same phase.
   When none remain, proceed to **Phase exit gate**.

### Task selection rules

- Only pick from the **current phase's** Active Queue, or from `ready-now` if the Active Queue is empty but dependencies are satisfied.
- Never invent a new `T#` to cover a perceived gap.
  Missing coverage means `replan-requested`.
- Never reopen a `completed` task to polish results unless `PLAN.md` or the active subplan explicitly re-adds it as a revision entry.
- Never cross into a later phase inside one invocation, even if the next phase appears trivially ready.
- If the plan uses `not-started` rather than `ready` for runnable tasks, preserve that vocabulary; do not introduce a second synonym.

## Blocker branch

Use the blocker branch for **execution-time blockers**.
Use `replan-requested` only when the plan itself needs structural change.

When a task cannot proceed because a dependency, deliverable, environment prerequisite, queue condition, or runtime precondition is unsatisfied:

1. mark the current `T#` as `blocked`;
2. add it to `State Snapshot.blocked-now`;
3. update `State Snapshot.resume-from`;
4. write `<planning-unit>/BLOCKER-NOTE.md` containing:
   - affected `P#` / `T#`,
   - blocker type,
   - missing prerequisite or failing condition,
   - checked paths / logs,
   - minimal unblock condition;
5. append a Session Log entry;
6. terminate with terminal state `blocked`.

## EVIDENCE.md schema

Every completed `T#` gets `runs/<T#>/EVIDENCE.md`.

Minimum structure:

```markdown
# Evidence: T12 — SCF on 6×6×6 k-mesh

## Task reference
- Plan ID: T12 (phase P2)
- Deliverable path: runs/T12/OUTCAR
- Spec refs: S3, S6
- Acceptance refs: A2, A5

## Exit criteria checklist
- [x] SCF energy-diff converged ≤ 1e-6 eV (observed 4.8e-7 eV, OSZICAR line 42)
- [x] max ionic force ≤ threshold (observed 0.012 eV/Å, OUTCAR final step)
- [x] OUTCAR archived at runs/T12/OUTCAR (ls verified)
- [x] OSZICAR archived at runs/T12/OSZICAR

## Key numbers
- Total energy: -123.456 eV/cell → -10.288 eV/atom (12 atoms)
- Magnetic moment (total): 3.98 μB
- k-mesh used: 6×6×6 Γ-centered, 216 irreducible points

## Acceptance mapping
- A2 (SCF ≤ 1e-6 eV): PASS, margin 0.52e-6 eV
- A5 (cohesive-energy k-convergence ≤ 1 meV/atom): contributes to the k-series;
  standalone verdict pending T14

## Raw log pointers
- SCF trace: runs/T12/OSZICAR
- Final OUTCAR: runs/T12/OUTCAR
- INCAR archived: runs/T12/INCAR
```

Rules:

- Every `Exit criterion` row from the task must appear as a checkbox.
  An unchecked box means the task is **not** `completed`.
- Every numeric claim must cite the file and line or section it came from.
- Every referenced `A#` must get an explicit verdict line: `PASS`, `contributes`, or `insufficient`.
- Do not paste long raw outputs; link to the archived file instead.

## PLAN.md write permissions

**Allowed writes** (the only fields this skill may modify in `PLAN.md` or any active subplan):

| Field | Allowed transitions |
|-------|---------------------|
| `Task.Status` | existing pre-run state (`ready` or `not-started`) → `in-progress` → `completed` / `blocked` / `superseded` |
| `Phase.Status` *(if present)* | `not-started` / `in-progress` → `completed` / `blocked` for the current phase only |
| `State Snapshot.current phase` | advance only after the current phase's gate passes |
| `State Snapshot.current task` | advance within the current phase; clear or reset only at phase transition after gate `PASS` |
| `State Snapshot.ready-now` | recompute from the task dependency graph |
| `State Snapshot.blocked-now` | recompute or add when a task becomes blocked |
| `State Snapshot.resume-from` | update on every task state change or phase transition |
| `Session Log` | **append only** — never edit or delete prior entries |

If a plan file has no explicit `Phase.Status` field, do not invent one.
Record phase completion through `State Snapshot` and `Session Log` only.

**Forbidden writes** (modifying any of these triggers a replan request instead of an edit):

- phase definitions, phase ordering, or phase exit gates
- stream composition: creating, deleting, renaming, splitting, or re-parenting streams
- `Depends on` columns
- `Exit criteria` columns
- `Spec refs` / `Acceptance refs` columns
- task or phase IDs (`T#` / `P#`)
- artifact approval / review state fields
- planning-unit metadata
- any content inside `SPEC.md` or `ACCEPTANCE.md`

If the right move requires a forbidden write, stop, emit `replan-requested`, and name the structural change in `<planning-unit>/REPLAN-REQUEST.md`.

## Phase exit gate

When every `T#` in the current phase is `completed`:

1. **Assemble the gate package:**
   - the current phase's `P#` block from `PLAN.md` or the active subplan
   - all `runs/<T#>/EVIDENCE.md` files for tasks in this phase
   - every `A#` in `ACCEPTANCE.md` referenced by any task in this phase
   - the original user objective from `SPEC.md`
   - raw log pointers named in the EVIDENCE files

2. **Verify the phase.**
   Verification is **mandatory** at every gate.

   - Prefer the `Agent` tool with `subagent_type = "verification"` when available.
   - If that helper is unavailable, perform an explicit manual gate review against:
     - the phase `Exit criteria`,
     - the referenced `A#` items,
     - and the original objective in `SPEC.md`.
   - Record when fallback verification was used.

3. **Handle the verdict:**

| Verdict | Action | Terminal |
|---------|--------|----------|
| `PASS` | Mark the current phase completed if a phase-status field exists; advance `State Snapshot.current phase`; update `ready-now`, `blocked-now`, and `resume-from`; append Session Log with the verification summary | `phase-completed` |
| `FAIL` | Write `<planning-unit>/BLOCKER-NOTE.md` with the verification report; mark offending `T#` rows `blocked`; do **not** advance `State Snapshot` | `blocked` |
| `PARTIAL` | Write `<planning-unit>/REPLAN-REQUEST.md` naming the unresolved structural items; mark affected current-phase `T#` rows `blocked`; do **not** advance | `replan-requested` |

Do not attempt to "fix" a `FAIL` in the same invocation.
After `FAIL`, terminate and let planner or direct mode react to the blocker note.

## Replan triggers

Emit `replan-requested` (write `<planning-unit>/REPLAN-REQUEST.md` and terminate) when any of the following occurs:

1. **Missing coverage.**
   A task requires an upstream artifact with no corresponding `T#`.

2. **Acceptance threshold appears ill-founded.**
   A task's exit criteria cannot be met not because of execution error, but because the relevant `A#` threshold conflicts with the method, assumptions, or physics.

3. **Verification structural failure.**
   Verification returns `FAIL` or `PARTIAL` citing reference-state inconsistency, wrong method tier for the target property, unconverged settings baked into the plan, or a similar planning-layer defect.

4. **Structural edit required.**
   The correct next action would touch a forbidden-write field.

5. **Scope divergence.**
   The remaining work naturally splits into independent studies that no longer belong to one coherent plan.

6. **Incomplete or malformed execution stack.**
   The planning unit lacks a required core artifact, or the execution-critical PLAN state is malformed.

`<planning-unit>/REPLAN-REQUEST.md` must contain:

- which `S#` / `A#` / `P#` / `T#` is affected;
- the specific structural change requested;
- supporting evidence: file paths, log excerpts, or verification verdicts.

One request per invocation.
If several issues surface, collect them in a single file under numbered sections.

## Terminal states

Every invocation ends in **exactly one** of these four states.
The final message to the parent agent must include the terminal keyword on its own line so it is machine-parseable.

| Terminal | Emitted when | Required artifacts |
|----------|--------------|--------------------|
| `phase-completed` | phase gate returned `PASS` | `PLAN.md` and/or active subplan updated with the new `State Snapshot`; verification summary appended to `Session Log` |
| `tasks-in-progress` | wall-clock or turn budget exhausted mid-phase with ≥ 1 task still `in-progress` or runnable | `State Snapshot.resume-from` points at the next entry; any in-flight task is marked accurately |
| `blocked` | a task dependency or runtime precondition cannot be satisfied, or phase gate returned `FAIL` | `<planning-unit>/BLOCKER-NOTE.md` naming the blocker and affected `T#` |
| `replan-requested` | any replan trigger fires, including phase gate `PARTIAL` | `<planning-unit>/REPLAN-REQUEST.md` with the structural change description |

## Final report contract

The final report to the parent agent must include:

- `Planning Unit:`
- `Current Phase:`
- `Tasks Completed This Invocation:`
- `Files Changed:` grouped by
  - plan artifacts
  - deliverables
  - evidence
  - docs
- `Next Action:`
- `Terminal:`

The terminal keyword must also appear on its own line.

## Hard rules

1. Always resolve the planning unit, then read `SPEC.md`, `ACCEPTANCE.md`, and `PLAN.md` in that order before touching any task.
2. Never modify `SPEC.md` or `ACCEPTANCE.md`.
3. Never modify `PLAN.md` or subplan structural fields: phases, dependencies, exit criteria, refs, IDs, stream composition, or approval state.
4. Never create a new `T#`, `P#`, or stream. Missing coverage means replan request, not invent-and-proceed.
5. Never close a phase without completing gate verification and receiving `PASS`.
6. Never mark a task `completed` while its `EVIDENCE.md` has any unchecked exit criterion.
7. Never cross phase boundaries in one invocation. One phase per session.
8. Never silently reduce scope. An `A#` that cannot be met yields a replan request.
9. Terminal state must be one of the four explicit keywords, with the corresponding evidence or note file actually present on disk when required.
10. A fresh agent must be able to resume from `PLAN.md` `State Snapshot` alone — every in-progress state must be recoverable without this session's chat.
11. Never modify artifact review or approval state; executor updates execution state only.
