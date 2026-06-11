---
name: stack-checker
description: "Use when audits the full artifact stack under <planning-unit> (SPEC.md, ACCEPTANCE.md, PLAN.md, and relevant subplans) for boundary drift, cross-file consistency, stale text, traceability integrity, and review readiness before user review or execution handoff."
---

# Stack Checker Skill

Audit the full artifact stack for a planning unit so the user receives a coherent, non-duplicative, review-ready set of documents.

This is a **cross-file consistency role**.
It is **not** a replacement for `plan-checker`.
It does **not** re-evaluate scientific correctness, re-run goal-backward plan adequacy, or invent missing scientific decisions.

Its job is to answer:

- do the artifacts agree with each other;
- does each artifact stay in its lane;
- are statuses, references, handoff entry points, and stale text coherent;
- and is the stack ready for user review or, conditionally, execution handoff.

## Planning-unit targeting and prior reads

Resolve the target planning unit in this order:

1. an explicit planning-unit path or artifact path from the caller or user;
2. `docs/WORKSPACE.md` → `Current Planning Unit`;
3. the parent directory of an existing `PLAN.md`, `SPEC.md`, or `ACCEPTANCE.md` being checked.

If the target planning unit cannot be determined safely, return `BLOCKED` and say why.

Before checking:

1. read `docs/WORKSPACE.md` if present;
2. read `<planning-unit>/SPEC.md`;
3. read `<planning-unit>/ACCEPTANCE.md`;
4. read `<planning-unit>/PLAN.md`;
5. read any active or referenced subplans under `<planning-unit>/plans/`;

A full stack audit requires the core stack:

- `<planning-unit>/SPEC.md`
- `<planning-unit>/ACCEPTANCE.md`
- `<planning-unit>/PLAN.md`

If any core artifact is missing, return `BLOCKED` and name the missing file(s).

## Operational fallback

When named helper routines are available, use them.

If they are unavailable:

- perform the equivalent manual cross-file audit,
- inspect traceability and state consistency directly,
- and preserve the same boundary rules and escalation rules.

Never block solely because a named helper is unavailable.

## Checker boundary

This skill may:

- inspect the planning-unit artifact stack;
- compare artifact boundaries and cross-links;
- detect stale text, dead paths, dangling IDs, duplicated content, and contradictory statuses;
- apply **low-risk inline fixes** when intent is unambiguous and no scientific meaning changes.

This skill must not:

- rewrite scientific scope, system definition, assumptions, thresholds, waiver logic, or plan structure;
- act as a replacement for `plan-checker`;
- invent new `S#`, `A#`, `P#`, or `T#`;
- approve execution handoff by silence when plan-quality review is still required.

## Stack boundary model

Use this ownership model when auditing boundary drift:

| Artifact | Owns | Must not become |
|----------|------|-----------------|
| `SPEC.md` | stable problem semantics: objective, scope, system/model, assumptions, defaults, evidence anchors, canonical references, `S#` items | task tracker, threshold contract, chat transcript |
| `ACCEPTANCE.md` | measurable completion logic: `A#` criteria, thresholds, verification methods, evidence requirements, severity, waivers | task queue, raw-results log, scope document |
| `PLAN.md` | top-level work state: streams, phases, dependencies, blockers, `ready-now`, `blocked-now`, `resume-from`, handoff entry | narrative replacement for `SPEC.md` or `ACCEPTANCE.md` |
| `plans/*.md` | stream-local or workstream-local execution state | duplicate master plan |

## Cross-file checking method

Audit pairwise and stack-wide consistency.

Check, at minimum:

- `SPEC.md` ↔ `ACCEPTANCE.md`
- `SPEC.md` ↔ `PLAN.md`
- `ACCEPTANCE.md` ↔ `PLAN.md`
- master `PLAN.md` ↔ active / referenced subplans

For each pair, ask:

- do they refer to the same system, method assumptions, and deliverable intent;
- do IDs and references resolve cleanly;
- does one artifact silently redefine what another already owns;
- do statuses and handoff signals make sense together;
- are stale, duplicated, or superseded statements still hanging around.

Do not re-run deep scientific judgment unless the inconsistency is explicit in the recorded artifacts.

## Audit dimensions

| Dimension | What to check |
|-----------|---------------|
| Boundary | `SPEC.md` owns semantics, `ACCEPTANCE.md` owns completion logic, `PLAN.md` owns work state; each stays in its lane |
| Traceability | `S#` / `A#` / `P#` / `T#` links exist where needed, resolve cleanly, and do not point to retired items without explanation |
| Duplication | bulky tables, parameter catalogs, literature lists, or repeated boilerplate are not duplicated in ways that will drift |
| State coherence | artifact status, work status, task status, `ready-now`, `blocked-now`, and active subplan pointers are mutually consistent |
| Stale text | obsolete assumptions, retired IDs, dead paths, superseded decisions, and old stream names are not left hanging |
| Handoff entry | `PLAN.md` and subplans agree on current stream / phase / task, active queue, resume point, and handoff notes |
| Review readiness | review / approval / handoff status is explicit and does not contradict unresolved issues |
| Execution-handoff advisory | from a stack-consistency perspective, execution could start without ambiguity; if plan-quality review is still required, say so explicitly |

## Artifact status consistency

Audit artifact status separately from work state.

### Artifact status

Use the artifact's own readiness state:

- `SPEC.md` / `ACCEPTANCE.md`: `draft` / `review` / `approved` / `superseded`
- `PLAN.md` / subplans: `draft` / `review` / `approved-for-handoff` / `needs-replan` / `superseded`

### Work state

Use execution-progress state where applicable:

- `not-started`
- `in-progress`
- `blocked`
- `completed`

### Task state

Where task tables exist:

- `not-started`
- `in-progress`
- `blocked`
- `completed`
- `superseded`

Rules:

- differing artifact statuses across files can be normal while the stack is being built;
- treat them as issues only when the claimed readiness contradicts the content, downstream requirements, or handoff posture;
- `ready-now` and `blocked-now` are derived queue fields, not artifact statuses.

Common status failures:

- `PLAN.md` is `approved-for-handoff`, but `ACCEPTANCE.md` still contains blocking criteria marked `[to-research]` or `[blocked]`.
- `PLAN.md` is `approved-for-handoff`, but `State Snapshot` lacks a usable current entry, `ready-now`, or `resume-from`.
- `PLAN.md` is `needs-replan`, but no `REPLAN-REQUEST.md` or structural defect is recorded.
- a subplan is marked effectively closed or superseded, but the master plan still lists it as active.
- `SPEC.md` or `ACCEPTANCE.md` is superseded, but `PLAN.md` still points to it as the active upstream contract.
- work is marked `completed`, but expected deliverable or evidence paths are missing or clearly stale.

## Typical materials-computation drift cases

- `SPEC.md` says PBE+D3, but `PLAN.md` tasks or handoff notes assume plain PBE.
- `SPEC.md` defines the system as Pt(111)+OH, but `PLAN.md` only schedules bare Pt(111) work.
- `ACCEPTANCE.md` requires k-convergence to 1 meV/atom, but `PLAN.md` or subplans contain no corresponding convergence support.
- the same DFT parameter table appears in both `SPEC.md` and `PLAN.md`, creating drift risk.
- `S3` is retired in `SPEC.md`, but `A5` or `T12` still reference it as active.
- a reference-state consistency criterion in `ACCEPTANCE.md` is not reflected in the plan's named dependencies or handoff assumptions.
- `ACCEPTANCE.md` requires archived input files and provenance metadata, but `PLAN.md` has no archival / evidence support.
- `SPEC.md` commits to a specific software stack or container, but `PLAN.md` assumes a different execution environment.
- a subplan marks the convergence stream done, but the master plan still treats convergence as active or blocking.
- `approved-for-handoff` appears while unresolved `[blocked]` items remain listed in the same stack.

## Fix policy

This skill is primarily a checker, but **low-risk inline fixes are allowed** when the intended correction is unambiguous and non-semantic.

### Low-risk inline fixes allowed

Document every inline fix.

Examples:

- correct stale paths when the intended target is obvious and exists;
- remove duplicated short boilerplate or repeated one-line metadata that drifted;
- add a missing cross-link to an existing `S#`, `A#`, `P#`, or `T#` when the intended target is unambiguous;
- sync clearly stale master ↔ subplan path references when only one valid target exists;
- retire references to an already-retired upstream ID when the replacement or retirement status is explicit;
- fix obvious formatting or section-label inconsistencies that do not change meaning.

### Escalate instead of fixing when

Do **not** fix inline when:

- the correction would change project scope, system definition, method assumptions, or scientific meaning;
- the correction would change thresholds, severity, waiver logic, or acceptance semantics;
- the correction would change phase / task structure, dependencies, exit gates, or execution order;
- multiple plausible fixes exist;
- the correct resolution depends on user intent rather than artifact drift.

If the correct fix is structural or semantic, return `REVISE` or `BLOCKED` instead of editing.

## Verdict semantics

Use exactly one verdict:

- `READY-FOR-REVIEW` — the stack is cross-file coherent and suitable for user review.
- `REVISE` — one or more cross-file inconsistencies, stale references, or readiness issues remain; one or more writers should revise before review or handoff.
- `BLOCKED` — the stack cannot be checked coherently because of planning-unit ambiguity, missing core artifacts, or unresolved upstream ambiguity that only the user or upstream writer can resolve.

Interpretation:

- `READY-FOR-REVIEW` does **not** replace `plan-checker`.
  It means the artifact stack is coherent enough for review.
- execution handoff is appropriate only if:
  - stack verdict is `READY-FOR-REVIEW`, and
  - plan-quality review is already `PASS` or no plan-quality blockers are known.
- if the stack is coherent but plan-quality review is still missing, say that handoff is **not yet confirmed**, not that it is automatically ready.

## Output contract

Return:

1. `Planning Unit`
2. `Artifacts Checked`
3. `Stack Verdict` (`READY-FOR-REVIEW` / `REVISE` / `BLOCKED`)
4. `Inline Fixes Applied`
   - per file
   - with a short rationale for each fix
   - say `none` if no inline fixes were made
5. `Remaining Cross-File Issues`
   - grouped by pair:
     - `SPEC.md ↔ ACCEPTANCE.md`
     - `SPEC.md ↔ PLAN.md`
     - `ACCEPTANCE.md ↔ PLAN.md`
     - master `PLAN.md` ↔ subplans
6. `Unresolved Traceability Gaps`
   - dangling or stale `S#` / `A#` / `P#` / `T#` references
   - say `none` if none remain
7. `State and Handoff Consistency Issues`
   - readiness contradictions, stale status labels, missing handoff entry, dead subplan pointers, or stale blocker / replan state
8. `Recommended Revisions Before Review`
9. `Handoff Advisory`
   - whether user review is appropriate right now;
   - whether execution handoff is appropriate right now, conditionally appropriate, or not appropriate;
   - if not appropriate, say whether the blocker is:
     - stack inconsistency,
     - missing plan-quality confirmation,
     - or upstream ambiguity.

Reporting rules:

- reference relevant `S#`, `A#`, `P#`, and `T#` whenever possible;
- distinguish between issues you fixed inline and issues that remain;
- if a section has no findings, say `none`;
- do not hide a blocking issue inside a general paragraph;
- do not imply scientific adequacy unless that conclusion comes from another checker or is explicitly out of scope here.
