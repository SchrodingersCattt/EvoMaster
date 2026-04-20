---
name: plan-checker
description: "Verifies PLAN.md and subplans will actually satisfy SPEC.md and ACCEPTANCE.md before review or execution handoff — goal-backward audit of coverage, dependencies, granularity, resumability, and boundary drift for materials-computation plans."
skill_type: operator
---

# PLAN Checker Skill

Verify that the current plan stack will actually achieve the outcome defined by `SPEC.md` and `ACCEPTANCE.md`. Do not ask whether the plan looks complete — ask whether it will work when executed.

See `reference/mandatory-artifact-read.md` for required prior reads, and `reference/traceability-contract.md` for `S#` / `A#` / `P#` / `T#` conventions.

## Checking method

Use **goal-backward verification**. Start from the intended deliverables, then from each critical `S#` item and each critical `A#` criterion, and ask: which concrete tasks, with which dependencies and which exit gates, actually deliver this? Do not start from the task list and ask whether it looks good — that direction misses absent tasks.

## Audit dimensions

| Dimension | Question | Typical failure in materials-computation |
|-----------|----------|------------------------------------------|
| Coverage | do major `S#` and `A#` items have concrete task support? | convergence streams (cutoff, k-mesh) promised in SPEC but absent in PLAN |
| Dependency | are dependencies coherent, complete, non-circular? | production SCF scheduled before the relaxed structure it consumes |
| Granularity | are tasks concrete enough to execute or resume? | a single task "run DFT" hides a convergence study, 4 sub-runs, and post-processing |
| Resumability | are current state, blockers, and resume-from explicit? | active queue missing; "in-progress" without an artifact path |
| Boundary | has PLAN drifted into SPEC or ACCEPTANCE territory? | xc functional choice re-debated inside PLAN; pass thresholds hardcoded into tasks without linking to `A#` |
| State | are approval state and work state internally consistent? | header "approved" but `[blocked]` items listed; subplan status contradicts master |
| Blocker | are critical blockers visible rather than hidden in prose? | "waiting on pseudopotential" buried mid-paragraph in the session log |

## Common materials-computation pitfalls to check

- **Spin / magnetism not propagated**: `SPEC` fixes a magnetic configuration but downstream tasks run non-spin-polarized by default (no `ISPIN=2`, no `MAGMOM`).
- **Inconsistent reference states**: adsorption or reaction energy tasks use a different xc, cutoff, or smearing than the isolated molecule / clean surface reference.
- **Missing convergence study for ML potentials**: training tasks present, but no validation / extrapolation task tied to an `A#`, no dataset version lock.
- **No checkpoint or restart protocol** for long MD / NEB / AIMD tasks.
- **Analysis tasks plot but do not assert**: plan produces figures but never evaluates results against `ACCEPTANCE` thresholds.
- **Deliverable tasks missing**: production runs planned but no final report / table / data-package task.
- **Same-system reuse not declared**: two tasks depend on the same relaxed structure but do not name the shared artifact.
- **Queue / wall-clock realism absent**: long tasks scheduled with no restart strategy, risking lost progress.

## Severity levels

- **blocking** — handoff must not proceed (uncovered `A#`, dependency inversion, missing resume point, inconsistent reference state).
- **major** — revision strongly required before review (granularity problem, duplicated scope between SPEC and PLAN, missing restart strategy).
- **minor** — quality issue but not a hard blocker (phrasing, missing notes, minor naming).

## Verdict options

- `PASS` — plan is ready for user review or handoff.
- `REVISE` — issues present; planner must revise before review.
- `BLOCKED` — upstream artifact (`SPEC.md` or `ACCEPTANCE.md`) is itself insufficient; escalate up-stack rather than patch the plan.

## Output contract

Return a concise check report with:

1. Verdict (`PASS` / `REVISE` / `BLOCKED`)
2. Blocking issues (one bullet each, with referenced `T#` / `P#` / `S#` / `A#`)
3. Major issues
4. Minor issues
5. Coverage gaps — list every `S#` or `A#` lacking adequate task support
6. Resumability gaps — anywhere current state, blockers, or resume-from is missing or contradictory
7. Required revisions before the plan can pass
