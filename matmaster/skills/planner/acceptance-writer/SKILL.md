---
name: acceptance-writer
description: "Use when writes or updates <planning-unit>/ACCEPTANCE.md — the measurable completion contract that turns vague success language into checkable criteria with verification methods, thresholds, evidence requirements, severity, and waiver rules for a materials-computation planning unit."
skill_type: operator
---

# ACCEPTANCE Writer Skill

Produce or revise `<planning-unit>/ACCEPTANCE.md` so a reviewer can decide whether the work is done from the artifact alone.

`ACCEPTANCE.md` is the completion contract.
It answers **what counts as done, how it will be checked, what evidence proves it, and what happens if a criterion is unmet or waived**.

It is **not** a task sequence, a progress tracker, a background memo, or a raw-results log.

## Planning-unit targeting and prior reads

Resolve the target planning unit in this order:

1. an explicit planning-unit path or artifact path from the caller or user;
2. `.planning/WORKSPACE.md` → `Current Planning Unit`;
3. the parent directory of an existing `ACCEPTANCE.md` being revised.

If the target planning unit cannot be determined safely, report uncertainty and do not write to an ambiguous location.

Before writing:

1. read `docs/WORKSPACE.md` if present;
2. read `<planning-unit>/SPEC.md`;
3. read the existing `<planning-unit>/ACCEPTANCE.md` if present;
4. read `<planning-unit>/PLAN.md` if present, only to preserve interface consistency and stable traceability;
5. read user-provided briefs, papers, prior runs, reference values, standards, or notes explicitly in scope.

Write only `<planning-unit>/ACCEPTANCE.md`.
Do not create parallel copies such as `ACCEPTANCE-v2.md`, `ACCEPTANCE-new.md`, or `ACCEPTANCE-copy.md`.

## What ACCEPTANCE.md owns (and does not)

| Owns | Does not own |
|------|--------------|
| measurable criteria with stable `A#` identifiers | problem scope or system definition (→ `SPEC.md`) |
| verification method per criterion | task list, queue, progress, blockers (→ `PLAN.md`) |
| numeric thresholds or explicit threshold-resolution status | raw results, long narrative logs |
| evidence requirements | silent qualitative judgments such as "reasonable" or "good enough" |
| severity if unmet (`blocking` / `major` / `minor`) | hidden scope reduction |
| waiver rules and waiver conditions | scientific assumptions that belong in `SPEC.md` |
| gate semantics (`PASS` / `FAIL` / `PARTIAL` / `WAIVED`) | execution instructions or job orchestration |

## Write discipline

`ACCEPTANCE.md` is the scientific gate contract for a planning unit.

When creating or revising it:

- turn vague success language into measurable criteria;
- attach a verification method and evidence requirement to each meaningful `A#`;
- make blocking, major, and minor severity explicit;
- define waiver rules visibly;
- make it possible for `plan-checker` to verify that every blocking `A#` has plan support;
- require review before execution handoff;
- preserve stable `A#` identifiers where meaning is unchanged;
- keep threshold sources explicit: user decision, group standard, literature anchor, benchmark, or planned convergence study;
- record uncertainty explicitly instead of pretending a threshold is settled.

Do not bury critical criteria in prose.
Do not turn `ACCEPTANCE.md` into a task list.
Do not silently redefine scope from `SPEC.md`.
Do not silently invent scientific assumptions that belong upstream.

## Artifact Status block

Every `ACCEPTANCE.md` should include:

```markdown
## Artifact Status

- Status: draft | review | approved | superseded
- Planning unit: <planning-unit-path>
- Related SPEC: <planning-unit>/SPEC.md
- Source: discuss | research | revision | replan
- Last updated: YYYY-MM-DD
```

Artifact status is separate from criterion verdicts.

## Gate model and criterion lifecycle

Use verdict language consistently where criteria or gates need a result:

- `PASS` — criterion satisfied with the required evidence
- `FAIL` — criterion checked and not satisfied
- `PARTIAL` — evidence is incomplete or only partial support exists; not enough to call `PASS`
- `WAIVED` — criterion not met, but explicitly waived under allowed conditions with recorded rationale

Use severity consistently:

- `blocking` — if unmet and not waived, handoff or completion must not proceed at the gate this criterion governs
- `major` — significant issue requiring revision before final acceptance; may or may not block intermediate planning depending on phase context
- `minor` — quality issue that should be addressed, but is not by itself a hard gate

Use criterion lifecycle markers where needed:

- `[specified]` — criterion and threshold are committed
- `[defaulted]` — threshold or criterion shape was chosen under delegation and may be overridden
- `[to-research]` — threshold, reference value, or verification basis is still unresolved
- `[blocked]` — criterion cannot yet be finalized because an upstream prerequisite is missing
- `[retired]` — keep the `A#`, explain why it is no longer active, and do not reuse the number

Rules:

- A blocking criterion marked `[to-research]` or `[blocked]` does **not** permit `approved-for-handoff`.
- A blocking criterion that is `FAIL` or `PARTIAL` and not `WAIVED` blocks completion at the gate it governs.
- Waivers must never be implicit.

## Criterion families for materials-computation projects

Use these families as a checklist.
Omit any that do not apply, and say so when omission could confuse downstream planning.

- **Parameter convergence**: cutoff energy, k-point density, smearing width, SCF tolerance, integration grids; each criterion names the convergence metric and threshold.
- **Literature / experimental anchor agreement**: lattice constant, bulk modulus, band gap, formation energy, reaction enthalpy, adsorption energy; each against a named reference within a stated tolerance.
- **Physical sanity**: bond lengths / angles in expected ranges, magnetic moments consistent with the intended configuration, total charge accounting, no unexpected imaginary phonons, stable relaxation behavior.
- **Reference-state consistency**: same XC, same basis / pseudopotential family, same correction schemes, same relevant grid / cutoff conventions across structures being compared.
- **Data completeness**: required snapshots, trajectories, checkpoints, intermediate logs, metadata, and provenance records are preserved.
- **Reproducibility / traceability**: input files archived, software version recorded, environment documented, commit hash or container hash captured, submission scripts preserved when relevant.
- **Model-quality thresholds (if ML is involved)**: validation MAE / RMSE, split policy, extrapolation behavior, uncertainty calibration, dataset version lock.
- **Deliverables**: required figures, tables, summary report, supplementary data package.
- **Handoff readiness**: artifact locations stable, cross-links to `SPEC.md` / `PLAN.md` resolved, evidence paths named, reviewer-facing outputs locatable.

## A# conventions and criterion template

Every meaningful criterion should have a stable `A#` identifier.

Use this template:

```markdown
### A5 — k-point convergence of cohesive energy
- Status: [specified]
- Type: scientific
- Related spec: S3, S6
- Applies to: bulk baseline calculations
- Requirement: cohesive energy converged to within 1 meV/atom with respect to k-point density.
- Verification: run a k-mesh series {4×4×4, 6×6×6, 8×8×8, 10×10×10}; compare cohesive energy across refinements; record the chosen converged mesh.
- Pass threshold: |E(N) − E(N−1)| ≤ 1 meV/atom for two consecutive refinements.
- Evidence required: convergence CSV, convergence plot, chosen k-mesh recorded in the archived input / run metadata.
- Threshold source: group standard for oxide bulk baselines; to be confirmed against literature anchor if needed.
- Severity if unmet: blocking
- Waiver allowed: no
- Waiver conditions: —
- Expected plan support: convergence-series tasks, evidence aggregation, and parameter-selection handoff into production tasks.
- Notes: if wall-clock budget becomes binding, any relaxation of this threshold requires explicit revision and approval.
```

Stable ID rules:

- keep existing `A#` numbers when meaning is unchanged;
- never renumber reviewed items;
- if meaning changes materially, revise the existing `A#` only when it is still the same criterion, otherwise add a new `A#`;
- if a criterion is retired, preserve the old identifier, mark it `[retired]`, explain the rationale, and do not reuse the number.

## Materials-specific threshold patterns

Prefer concrete, domain-defensible thresholds.
State the source of every threshold: user decision, group standard, specific paper, benchmark dataset, or planned convergence study.

Typical examples:

- lattice constants: ≤ 1% deviation from experiment, or within experimental uncertainty if uncertainty is reported;
- bulk modulus: ≤ 10% deviation versus experiment or higher-level reference;
- formation or reaction energies: ≤ 50 meV/atom or per formula unit versus benchmark, unless a different property-specific tolerance is justified;
- SCF convergence: energy change ≤ 1e-6 eV; tighten for phonons, NEB, or other sensitivity-critical workflows when justified;
- ionic relaxation: max force ≤ 0.01 eV/Å for production, looser only when the project is explicitly screening-tier;
- band-gap comparison: cite the benchmark source and state known functional bias explicitly;
- phonon sanity: no unexpected imaginary frequencies at Γ except explicitly expected soft modes;
- ML potential regression: validation MAE / RMSE below a stated threshold on a stated split policy, with dataset version lock.

If a threshold is not yet defensible, mark the criterion `[to-research]` and say what will resolve it.

## Interfaces to PLAN.md and execution

`ACCEPTANCE.md` must support downstream planning without becoming a plan.

The `Interfaces to PLAN.md and execution` section should make clear:

- which blocking `A#` items require dedicated streams, tasks, or evidence-collection steps in `PLAN.md`;
- which `A#` items are phase-gating rather than final-deliverable-only;
- which `A#` items remain provisional and therefore block `approved-for-handoff`;
- which evidence paths or artifact classes execution must eventually produce.

Do not write task tables or queue state in `ACCEPTANCE.md`.

Do not modify `SPEC.md` or `PLAN.md` in this skill.
If they now need revision because acceptance semantics changed, report that explicitly in the output summary.

If `PLAN.md` already exists, reference current plan coverage where helpful.
If `PLAN.md` does not yet exist, describe the required future plan support rather than inventing `T#` identifiers.

## Preferred structure

1. Artifact Status
2. Acceptance Scope
3. Gate Model
4. Global Completion Gates
5. Scientific Acceptance Criteria
6. Engineering / Reproducibility Acceptance Criteria
7. Deliverable Acceptance Criteria
8. Waivers and Exceptions
9. Evidence Requirements
10. Interfaces to `PLAN.md` and execution
11. Review Record

You may add short appendices when they materially support acceptance semantics, but do not turn `ACCEPTANCE.md` into a background review memo.

## Material update rule

A material update changes one or more of:

- a criterion's requirement or pass threshold;
- a verification method or evidence requirement;
- severity if unmet;
- waiver allowance or waiver conditions;
- gate semantics or completion logic;
- the meaning, lifecycle, or downstream role of an `A#`;
- the required plan support for a blocking criterion.

Typos, wording-only edits, formatting cleanup, and citation-style normalization do not count as material updates.

## Recommended output path

Primary path:

```text
<planning-unit>/ACCEPTANCE.md
```

When creating a new planning unit under the current workspace convention, the default is:

```text
docs/<topic-slug>/ACCEPTANCE.md
```

## Output contract

- Create or update `<planning-unit>/ACCEPTANCE.md`.
- Include an `Artifact Status` section with status, planning unit, related SPEC, source, and update date.
- Include a `Gate Model` section that explains severity, verdict language, waiver behavior, and blocking behavior.
- Use `PASS` / `FAIL` / `PARTIAL` / `WAIVED` consistently.
- Make blocking criteria impossible to bypass silently.
- Keep existing `A#` IDs stable where meaning is unchanged.
- Mark retired criteria explicitly and do not reuse their numbers.
- Do not modify `SPEC.md` or `PLAN.md` here; report downstream revision needs instead.
- Treat a blocking criterion left `[to-research]` or `[blocked]` as incompatible with `approved-for-handoff`.

After any create or material update, return a short checkpoint that includes:

- artifact path;
- whether it was created or revised;
- sections materially changed;
- `A#` items added, updated, or retired;
- which criteria are blocking vs major vs minor;
- which criteria remain provisional (`[to-research]` or `[blocked]`);
- if `PLAN.md` exists, which current `P#` / `T#` items support each blocking criterion and which blocking criteria still lack coverage;
- if `PLAN.md` does not yet exist, what future plan support each blocking criterion will require;
- whether `SPEC.md` or `PLAN.md` should now be revised;
- one review prompt: `approve` / `revise` / `provide missing input`.

A checkpoint is satisfied when the user approves the artifact, requests revisions, provides missing input, or explicitly defers unresolved non-blocking items.
