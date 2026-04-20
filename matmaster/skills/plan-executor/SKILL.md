---
name: plan-executor
description: "Drives a planner-produced SPEC/ACCEPTANCE/PLAN artifact stack from its current state to the next phase exit gate under strict state-write discipline. Use when the workspace contains SPEC.md, ACCEPTANCE.md, or PLAN.md — one invocation advances at most one phase."
skill_type: operator
---

# Plan Executor Skill

Consume the planner artifact stack (`SPEC.md`, `ACCEPTANCE.md`, `PLAN.md`, and any `plans/*.md` subplans) and advance work. Never replan. Never redefine scope. Never close a phase without independent verification.

One invocation drives **at most one phase** to its exit gate and then terminates. If the current phase is already completed on entry, run its gate (if not yet run) and advance State Snapshot — do not cross into the next phase within the same invocation.

This skill is the execution counterpart to the planner's writer/checker skills. It is read-heavy on SPEC / ACCEPTANCE, write-restricted on PLAN, and write-free on the structural parts of the plan.

## Activation

Activate this skill **whenever** the workspace contains any of:

- `SPEC.md`
- `ACCEPTANCE.md`
- `PLAN.md`
- any file under `plans/`

If none of these exists, the task is ad-hoc; fall back to direct mode's default execution rules and do not invoke this skill.

## Artifact boundaries

| Artifact | Read | Write | Notes |
|----------|------|-------|-------|
| `SPEC.md` | yes | **never** | stable problem contract; changes require planner |
| `ACCEPTANCE.md` | yes | **never** | completion contract; changes require planner |
| `PLAN.md` | yes | **restricted** | see "PLAN.md write permissions" |
| `plans/*.md` | yes | **restricted** | same rules as PLAN.md |
| `runs/<T#>/*` | yes | **free** | production deliverables, evidence, logs |
| `docs/REPLAN-REQUEST.md` | read if present | create / append | triggers planner re-entry |
| `docs/BLOCKER-NOTE.md` | read if present | create / append | names the blocker |

Do not create parallel copies such as `SPEC-v2.md`, `PLAN-new.md`, or `runs/T12_v2/`. If the current PLAN cannot carry the work, the response is a replan request, not a new artifact.

## Startup sequence

Every invocation must run these steps **in order** before touching any task:

1. **Read `SPEC.md`.** Load all `S#` items. Capture constraints that gate downstream tasks (xc functional, pseudopotential set, magnetism, supercell policy, resource caps).
2. **Read `ACCEPTANCE.md`.** Load every `A#` with its threshold and validation method.
3. **Read `PLAN.md`.** Parse the State Snapshot block:
   - current stream / phase / task
   - `resume-from` anchor
   - `ready-now` and `blocked-now` lists
4. **If State Snapshot points into a subplan**, read that `plans/<stream>.md` and work inside its Active Queue thereafter.
5. **Report startup position** to the parent direct-mode agent:
   - detected artifact paths
   - current `P#` and expected exit gate
   - next 1–3 `T#` items from the Active Queue

If any of steps 1–3 fails (missing file, malformed State Snapshot, empty Active Queue with no in-progress task), halt immediately, emit terminal state `replan-requested`, and write `docs/REPLAN-REQUEST.md` naming the defect.

## Task execution loop

For each `T#` popped from the Active Queue of the current phase:

1. **Validate readiness.** Confirm every entry in the task's `Depends on` column has Status `completed` and the named deliverable actually exists on disk. If not, halt the loop, mark the task `blocked`, go to the "blocker" branch.
2. **Mark `in-progress`.** Update the task row's Status using the PLAN write rules. Update `State Snapshot.current task`.
3. **Execute.** Perform the computation, file writes, tool invocations, or domain workflow the task prescribes. Use existing domain skills (`matmaster/skills/abacus`, `mlips`, `lammps`, etc.) for the specifics — this skill never reimplements what a domain skill covers.
4. **Place the deliverable.** Write the artifact to the exact path named in the task's `Deliverable / artifact path` column. Do not append suffixes (`_v2`, `_final`) unless the task itself asks for named variants.
5. **Write `runs/<T#>/EVIDENCE.md`** per the schema below.
6. **Update task row** to Status `completed` and append one Session Log entry of the form:
   `T12 completed at <ISO-timestamp>; evidence=runs/T12/EVIDENCE.md`
7. Advance to the next ready `T#` in the same phase. When none remain, proceed to **Phase exit gate**.

### Task selection rules

- Only pick from the **current phase's** Active Queue, or `ready-now` if the Active Queue is empty but dependencies are satisfied.
- Never invent a new `T#` to cover a perceived gap. Missing coverage → replan-request.
- Never re-open a `completed` task to polish results unless PLAN.md explicitly re-adds it as a revision entry.
- Never cross into a later phase inside one invocation, even if the next phase looks trivially ready.

## EVIDENCE.md schema

Every completed `T#` gets `runs/<T#>/EVIDENCE.md`. Minimum structure:

```
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

- Every `Exit criterion` row from the task must appear as a checkbox. An unchecked box means the task is **not** `completed` — it is `blocked`.
- Every numeric claim must cite the file and line / section it came from.
- Every referenced `A#` must get an explicit verdict line (PASS / contributes / insufficient).
- Do not paste long raw outputs; link to the archived file instead.

## PLAN.md write permissions

**Allowed writes** (the only fields this skill may modify in `PLAN.md` or any subplan):

| Field | Allowed transitions |
|-------|---------------------|
| `Task.Status` | `ready` → `in-progress` → `completed` / `blocked` / `superseded` (terminal) |
| `State Snapshot.current phase` | advance only after the current phase's gate passes |
| `State Snapshot.current task` | advance within the current phase |
| `State Snapshot.ready-now` | recompute from the task dependency graph |
| `State Snapshot.blocked-now` | add when a task becomes blocked |
| `State Snapshot.resume-from` | update on every task state change |
| `Session Log` | **append only** — never edit or delete prior entries |

**Forbidden writes** (modifying any of these triggers a replan request instead of an edit):

- Phase definitions, phase ordering, or phase exit gates
- Stream composition (creating, deleting, renaming, or re-parenting streams)
- `Depends on` columns
- `Exit criteria` columns
- `Spec refs` / `Acceptance refs` columns
- Task or phase IDs (`T#` / `P#`)
- Any content inside `SPEC.md` or `ACCEPTANCE.md`

If the right move requires a forbidden write, stop, emit `replan-requested`, and name the structural change in `docs/REPLAN-REQUEST.md`.

## Phase exit gate

When every `T#` in the current phase is `completed`:

1. **Assemble the gate package:**
   - The current phase's `P#` block from PLAN.md (or subplan)
   - All `runs/<T#>/EVIDENCE.md` files for tasks in this phase
   - Every `A#` in ACCEPTANCE.md referenced by any task in this phase
   - The original user objective from SPEC.md
   - Raw log pointers (OUTCAR, OSZICAR, trajectories, NEB images, …) named in the EVIDENCE files

2. **Dispatch the verification subagent.** Use the `Agent` tool with `subagent_type = "verification"`. Pass the gate package and the phase's `Exit criteria`. Verification is **mandatory** at every gate; there is no opt-out.

3. **Handle the verdict:**

| Verdict | Action | Terminal |
|---------|--------|----------|
| `PASS` | Mark `P#` completed; advance `State Snapshot.current phase` to the next phase; append Session Log with the verification summary | `phase-completed` |
| `FAIL` | Write `docs/BLOCKER-NOTE.md` with the verification report; mark offending `T#` rows `blocked`; do **not** advance State Snapshot | `blocked` |
| `PARTIAL` | Write `docs/REPLAN-REQUEST.md` naming the unresolved items; set partial tasks' Status tag to `[to-research]`; do **not** advance | `replan-requested` |

Do not attempt to "fix" a FAIL in the same invocation. The correct next step after FAIL is to terminate and let planner or direct mode react to the blocker note.

## Replan triggers

Emit `replan-requested` (write `docs/REPLAN-REQUEST.md` and terminate) when any of the following occurs:

1. **Missing coverage.** A task requires an upstream artifact with no corresponding `T#` — e.g., production SCF requires a converged cutoff but no cutoff-convergence task exists.
2. **Acceptance threshold appears ill-founded.** A task's Exit criteria cannot be met not because of execution error but because the `A#` threshold itself conflicts with the physics (e.g., 1 meV/atom demanded from a method that cannot deliver it).
3. **Verification structural failure.** The verification subagent returns `FAIL` citing reference-state inconsistency, wrong xc for the target property, unconverged settings in the plan, or similar planning-layer defects.
4. **Structural edit required.** The correct next action would touch a forbidden-write field.
5. **Scope divergence.** The remaining work naturally splits into independent studies that share no deliverable, violating the one-coherent-plan assumption.

`docs/REPLAN-REQUEST.md` must contain:

- Which `S#` / `A#` / `P#` / `T#` is affected
- The specific structural change requested (new task, retired criterion, split plan, etc.)
- Supporting evidence: file paths, log excerpts, verification verdict

One request per invocation. If several issues surface, collect them in a single file under numbered sections.

## Terminal states

Every invocation ends in **exactly one** of these four states. The final message to the parent agent must include the terminal keyword on its own line so it is machine-parseable.

| Terminal | Emitted when | Required artifacts |
|----------|--------------|--------------------|
| `phase-completed` | phase gate returned `PASS` | PLAN.md updated with new `State Snapshot`; verification summary appended to Session Log |
| `tasks-in-progress` | wall-clock / turn budget exhausted mid-phase with ≥ 1 task still `in-progress` or `ready` | `State Snapshot.resume-from` pointing at the next `T#`; any in-flight task properly marked |
| `blocked` | task dependency cannot be satisfied, or phase gate returned `FAIL` | `docs/BLOCKER-NOTE.md` naming the blocker and affected `T#` |
| `replan-requested` | any replan trigger fires, including phase gate `PARTIAL` | `docs/REPLAN-REQUEST.md` with the structural change description |

The final report must also list:

- terminal keyword
- current `P#`
- count of tasks moved to `completed` in this invocation
- all files created or modified, grouped by (plan artifacts / deliverables / evidence / docs)
- path to `REPLAN-REQUEST.md` or `BLOCKER-NOTE.md` if applicable

## Hard rules

1. Always read `SPEC.md`, `ACCEPTANCE.md`, and `PLAN.md` (in that order) before touching any task.
2. Never modify `SPEC.md` or `ACCEPTANCE.md`.
3. Never modify `PLAN.md` structural fields (phases, dependencies, exit criteria, refs, IDs, stream composition).
4. Never create a new `T#`, `P#`, or stream. Missing coverage means replan-request, not invent-and-proceed.
5. Never close a phase without dispatching the `verification` subagent and receiving `PASS`.
6. Never mark a task `completed` while its `EVIDENCE.md` has any unchecked Exit criterion.
7. Never cross phase boundaries in one invocation. One phase per session.
8. Never silently reduce scope. An `A#` that cannot be met yields a replan request.
9. Terminal state must be one of the four explicit keywords, with the corresponding evidence / note file actually present on disk.
10. A fresh agent must be able to resume from `PLAN.md` State Snapshot alone — every in-progress state must be recoverable without this session's chat.
