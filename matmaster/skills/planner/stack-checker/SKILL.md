---
name: stack-checker
description: "Audits the full artifact stack (SPEC.md, ACCEPTANCE.md, PLAN.md, subplans) for boundary drift, cross-file consistency, stale text, and review readiness before a user-facing review or execution handoff."
skill_type: operator
---

# Stack Checker Skill

Audit the full artifact stack so the user receives a coherent, non-duplicative, review-ready set of documents. This is a **cross-file consistency role** — it is not a replacement for the plan checker, and does not re-evaluate scientific decisions.

See `reference/mandatory-artifact-read.md` for required prior reads, and `reference/traceability-contract.md` for cross-file ID conventions.

## Audit dimensions

| Dimension | What to check |
|-----------|---------------|
| Boundary | SPEC owns problem semantics, ACCEPTANCE owns completion logic, PLAN owns work state. Each file stays in its lane. |
| Traceability | `S#` / `A#` / `P#` / `T#` links are present where needed, and none are dangling. |
| Duplication | bulky tables, parameter catalogs, or literature lists are not repeated across files. |
| State consistency | document-status and work-status agree across artifacts and between master and subplans. |
| Stale text | obsolete assumptions, retired IDs, dead paths, or superseded decisions are not left hanging. |
| Handoff readiness | review / approval / handoff status is explicit and mutually consistent. |

## Typical materials-computation drift cases

- `SPEC` says PBE+D3, `PLAN` tasks use PBE without D3 (or the reverse).
- `SPEC` system is "Pt(111) with OH adsorbed", but `PLAN` tasks run bare Pt(111) only.
- `ACCEPTANCE` requires k-convergence to 1 meV/atom, `PLAN` has no corresponding `T#`.
- DFT parameter table pasted into both `SPEC` and `PLAN` — two copies will drift.
- `S3` retired in `SPEC`, but `A5` and `T12` still reference `S3`.
- Subplan marks its stream closed, master `PLAN` still shows it active.
- "Approved for handoff" in the state snapshot while `[blocked]` items remain listed.
- `ACCEPTANCE` demands archived input files + commit hash, `PLAN` has no archive / deliverable task.
- `SPEC` commits to a specific software version, `PLAN` assumes a different container image.
- Reference-state criterion in `ACCEPTANCE` (e.g. "same xc across compared structures") not reflected as an explicit dependency in `PLAN`.

## Fix policy

**Low-risk inline fixes allowed** (document what was changed):
- obvious status mismatches (master vs subplan, document vs work status).
- stale path corrections (renamed run folders, retired artifacts).
- removing duplicated short boilerplate.
- adding a missing cross-link when the intent is unambiguous.
- retiring references to an `S#` / `A#` already marked retired upstream.

**Escalate instead of fixing when**:
- the correct fix would change project scope;
- the correct fix would change scientific meaning (method, system, thresholds);
- the correct fix would change acceptance logic;
- multiple plausible resolutions exist and no single one is obviously right.

## Verdict options

- `READY-FOR-REVIEW` — stack is coherent; user can review.
- `REVISE` — one or more writers must revise before review.
- `BLOCKED` — the inconsistency stems from an upstream ambiguity only the user can resolve.

## Output contract

Return:

1. Stack verdict (`READY-FOR-REVIEW` / `REVISE` / `BLOCKED`).
2. What was fixed inline, per file, with the diff rationale.
3. Remaining cross-file issues, grouped by pair: `SPEC` ↔ `ACCEPTANCE`, `SPEC` ↔ `PLAN`, `ACCEPTANCE` ↔ `PLAN`, master ↔ subplan.
4. Unresolved traceability gaps (`S#` / `A#` / `P#` / `T#` without proper linkage).
5. Whether user review or execution handoff is appropriate right now.
