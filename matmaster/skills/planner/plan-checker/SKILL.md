---
name: plan-checker
description: "Use when checks <planning-unit>/PLAN.md and any relevant subplans under <planning-unit>/plans/ will actually satisfy <planning-unit>/SPEC.md and <planning-unit>/ACCEPTANCE.md before review or execution handoff."
skill_type: operator
---

# PLAN Checker Skill

Audit `<planning-unit>/PLAN.md` and any relevant subplans under `<planning-unit>/plans/` to determine whether the current plan stack will actually deliver the outcome defined by `SPEC.md` and `ACCEPTANCE.md`.

This skill is **read-only**.
Do not modify artifacts.
Do not silently fill scientific gaps.
Do not ask whether the plan “looks complete.”
Ask whether it will work when executed by direct mode or `plan-executor`.

## Planning-unit targeting and prior reads

Resolve the target planning unit in this order:

1. an explicit planning-unit path or artifact path from the caller or user;
2. `docs/WORKSPACE.md` → `Current Planning Unit`;
3. the parent directory of an existing `PLAN.md` being checked.

If the target planning unit cannot be determined safely, return `BLOCKED` and say why.

Before checking:

1. read `docs/WORKSPACE.md` if present;
2. read `<planning-unit>/SPEC.md`;
3. read `<planning-unit>/ACCEPTANCE.md`;
4. read `<planning-unit>/PLAN.md`;
5. read any active or referenced subplans under `<planning-unit>/plans/`;
6. read `<planning-unit>/REPLAN-REQUEST.md` or `<planning-unit>/BLOCKER-NOTE.md` if present and relevant.

## Checking method

Use **goal-backward verification**.

Start from:

- the intended deliverables and objective in `SPEC.md`,
- each major or blocking `S#`,
- and each major or blocking `A#`.

For each of these, ask:

- which concrete `P#` / `T#` items deliver it;
- which dependencies must be satisfied first;
- which deliverables or evidence paths prove completion;
- which exit gates ensure the work is actually verified;
- and whether an execution agent could do the work without inventing missing scientific decisions.

Do **not** start from the task list and ask whether it looks plausible.
That misses absent tasks, missing dependencies, and uncovered criteria.

## Read-only boundary

This skill may:

- inspect `SPEC.md`, `ACCEPTANCE.md`, `PLAN.md`, subplans, and relevant blocker / replan notes;
- compare master-plan and subplan state;
- report coverage gaps, contradictions, and revision needs.

This skill must not:

- edit `SPEC.md`, `ACCEPTANCE.md`, `PLAN.md`, or subplans;
- invent new `P#` / `T#`;
- redefine scope, thresholds, or scientific assumptions;
- approve handoff by silence when blocking issues remain.

## Artifact gate checks

In addition to task-level quality, verify the artifact-gate pipeline.

| Gate | Checker question |
|------|------------------|
| Discuss Gate | Did the plan correctly consume captured intent, defaults, deferred ideas, and canonical references from `SPEC.md` where relevant to decomposition? |
| SPEC Gate | Are objective, system, scope, non-scope, assumptions, and open questions clear enough to support executable tasks without silent invention? |
| ACCEPTANCE Gate | Does every blocking `A#` have concrete task support, evidence collection, and a validation path? |
| PLAN Gate | Are phases, dependencies, `Active Queue`, `ready-now`, `blocked-now`, `resume-from`, and blockers coherent across master plan and subplans? |
| Handoff Gate | Can direct mode or `plan-executor` start from `State Snapshot` and `Active Queue` without inventing missing scientific decisions or structural edits? |

Status checks:

- `approved-for-handoff` is valid only if no blocking or major coverage, dependency, resumability, or handoff issues remain.
- `needs-replan` is appropriate when the correct fix would require structural plan edits, upstream artifact revision, or a change to planner-owned semantics.
- If the plan cannot support direct mode or `plan-executor` execution from the artifacts alone, the verdict must be `REVISE` or `BLOCKED`.

## Audit dimensions

| Dimension | Question | Typical failure in materials computation |
|-----------|----------|------------------------------------------|
| Coverage | Do major `S#` and `A#` items have concrete `P#` / `T#` support? | convergence required by `SPEC.md`, but no convergence stream or tasks exist |
| Dependency | Are dependencies coherent, complete, and non-circular? | production SCF scheduled before the relaxed structure or reference state it consumes |
| Granularity | Are tasks concrete enough to execute, verify, and resume? | a single task “run DFT” hides convergence, relaxation, property runs, and post-processing |
| Resumability | Are current state, `ready-now`, `blocked-now`, and `resume-from` explicit and coherent? | `in-progress` task exists, but no deliverable path or restart anchor is named |
| Boundary | Has `PLAN.md` drifted into `SPEC.md` or `ACCEPTANCE.md` territory? | XC functional re-decided in PLAN; thresholds hardcoded in tasks without `A#` linkage |
| State coherence | Are artifact status, work status, task status, and queue state internally consistent? | header says `approved-for-handoff`, but the active stream is blocked and uncovered |
| Master/subplan consistency | Do master plan and subplans agree about the active stream, phase, and current task? | master points to production; subplan still sits in convergence |
| Blocker visibility | Are critical blockers explicit rather than buried in prose? | waiting on pseudopotential buried in a session note |
| Handoff entry | Can execution tell what to read first and where to start? | `State Snapshot` exists, but no active queue or subplan entry is named |

## Common materials-computation pitfalls to check

- **Spin / magnetism not propagated**: `SPEC.md` fixes a magnetic setup, but downstream tasks do not preserve it.
- **Reference-state inconsistency**: adsorption / reaction / defect comparisons do not depend on matched reference-state artifacts computed with the same functional, basis / pseudopotentials, grids, and corrections.
- **Convergence not gating downstream work**: production tasks do not depend on the cutoff / k-mesh / smearing decisions they require.
- **Relaxation handoff missing**: property calculations do not name the relaxed structure they consume.
- **Method-compatibility drift**: downstream tasks silently drop `+U`, SOC, solvation, dipole correction, or charged-cell treatment required by upstream decisions.
- **No checkpoint / restart protocol** for long MD / NEB / AIMD / phonon workflows.
- **Analysis tasks plot but do not assert**: figures or tables are generated, but no task evaluates results against relevant `A#`.
- **Deliverable tasks missing**: runs are planned, but no final table / figure / report / data-package task exists.
- **Shared artifact reuse not declared**: multiple tasks consume the same relaxed structure or reference calculation without naming the shared artifact.
- **Queue / wall-clock realism absent**: long jobs have no restart or queue fallback path.
- **ML potential validation gap**: training exists, but validation / extrapolation checks / dataset version locking are absent.

## Severity levels

- **blocking** — handoff must not proceed; the plan is not safely executable or an upstream artifact is insufficient.
- **major** — revision is strongly required before review or handoff.
- **minor** — quality issue, but not a handoff blocker by itself.

Treat the following as **blocking** by default unless strong evidence shows otherwise:

- uncovered blocking `A#`;
- uncovered major `S#` that changes execution structure;
- dependency inversion or missing required upstream artifact;
- contradictory master/subplan state;
- missing or unusable `resume-from` / `Active Queue` / execution entry;
- reference-state inconsistency that invalidates the intended property;
- plan requiring silent scientific invention during execution.

## Verdict semantics

Use exactly one verdict:

- `PASS` — the plan is ready for user review or execution handoff from a plan-quality perspective.
- `REVISE` — the plan has correctable issues, but the fix can be made inside `PLAN.md` / subplans without changing upstream semantics.
- `BLOCKED` — the plan cannot pass because:
  - `SPEC.md` or `ACCEPTANCE.md` is insufficient,
  - the planning unit cannot be determined safely,
  - or the correct fix requires planner-level structural rework, upstream artifact revision, or `needs-replan`.

Use these distinctions:

- choose `REVISE` when the fix is local to plan decomposition, task wording, queue state, dependencies, blockers, or handoff notes **within the existing planning semantics**;
- choose `BLOCKED` when the fix requires changing scope, thresholds, assumptions, phase topology, exit-gate logic, stream structure, or other planner-owned structure.

`PASS` means `approved-for-handoff` is **permitted** from a plan-quality perspective, but overall stack approval may still require other checks.

## Output contract

Return a concise check report with these sections:

1. `Planning Unit`
2. `Artifacts Checked`
3. `Verdict` (`PASS` / `REVISE` / `BLOCKED`)
4. `Handoff Status`
   - whether `approved-for-handoff` is allowed from the plan-quality perspective;
   - whether `needs-replan` should be used instead
5. `Blocking Issues`
6. `Major Issues`
7. `Minor Issues`
8. `Coverage Gaps`
   - list every `S#` or `A#` lacking adequate plan support
9. `Dependency and Resumability Gaps`
10. `Required Revisions Before Pass`
11. `Recommended Next Action`

Reporting rules:

- reference relevant `S#`, `A#`, `P#`, and `T#` in every substantive issue whenever possible;
- distinguish master-plan issues from subplan issues when relevant;
- if a section has no findings, say `none`;
- do not hide a blocking issue inside a major-issues paragraph;
- if `BLOCKED`, say whether the blocker is:
  - upstream artifact insufficiency,
  - planning-structure defect,
  - or planning-unit ambiguity.
