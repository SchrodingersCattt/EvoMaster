---
name: spec-writer
description: "Use when writes or updates <planning-unit>/SPEC.md — the stable problem contract that fixes objective, scope, system definition, assumptions, defaults, and evidence anchors for a materials-computation planning unit so later planning does not drift."
skill_type: operator
---

# SPEC Writer Skill

Produce or revise `<planning-unit>/SPEC.md` so a later planning or execution agent can continue the work from the artifact alone, without access to chat history.

`SPEC.md` is the stable semantics layer.
It defines **what the work is, why it exists, what is in scope, what is explicitly out of scope, what system/model/assumptions/constraints govern it, and which evidence anchors justify key decisions**.

It does **not** define task execution order, work status, blocker state, or acceptance thresholds.

## Planning-unit targeting and prior reads

Resolve the target planning unit in this order:

1. an explicit planning-unit path or artifact path from the caller or user;
2. `docs/WORKSPACE.md` → `Current Planning Unit`;
3. the parent directory of an existing `SPEC.md` being revised.

If the target planning unit cannot be determined safely, report uncertainty and do not write to an ambiguous location.

Before writing:

1. read `docs/WORKSPACE.md` if present;
2. read the existing `<planning-unit>/SPEC.md` if present;
3. read `<planning-unit>/ACCEPTANCE.md` and `<planning-unit>/PLAN.md` if present, only to preserve interface consistency and stable traceability;
4. read the user-provided brief, files, papers, prior runs, notes, or structures explicitly in scope.

Write only `<planning-unit>/SPEC.md`.
Do not create parallel copies such as `SPEC-v2.md`, `SPEC-new.md`, or `SPEC-copy.md`.

## What SPEC.md owns (and does not)

| Owns | Does not own |
|------|--------------|
| problem statement, objective, intended deliverables | task decomposition, queues, progress |
| scope and explicit non-scope | pass/fail thresholds, validation methods, waiver rules (→ `ACCEPTANCE.md`) |
| research system / model definition | blocker state, work status, active queue (→ `PLAN.md`) |
| assumptions, constraints, defaults, imported decisions | session transcript or narrative chat history |
| evidence anchors and canonical references | bulky catalogs that do not affect the next decision |
| stable `S#` identifiers | renumbering across revisions |
| open questions and research gaps | production execution instructions |

## Write discipline

`SPEC.md` is the research design contract for a planning unit.

When creating or revising it:

- state the problem and intended deliverables;
- record scope and explicit non-scope;
- define the research system / model clearly enough for downstream planning;
- compare meaningful alternatives only when the choice matters;
- record rationale for selected defaults;
- preserve canonical references to user-mentioned papers, files, prior runs, structures, docs, and constraints;
- record prior decisions reused from other planning units, with source;
- preserve Deferred Ideas without silently expanding current scope;
- support user review before downstream planning or execution.

Always prefer updating an existing `SPEC.md` over creating a parallel version.

Create a draft as soon as enough structure exists to preserve meaning.
Do not wait for full certainty.
Record uncertainty explicitly.

Do not turn `SPEC.md` into a task tracker, acceptance contract, or discussion log.

## Artifact Status block

Every `SPEC.md` should include:

```markdown
## Artifact Status

- Status: draft | review | approved | superseded
- Planning unit: <planning-unit-path>
- Source: discuss | user-provided brief | replan | revision
- Last updated: YYYY-MM-DD
```

Approval state is separate from plan work state.

## Materials-computation content checklist

Cover each item when it applies.
If a point is not applicable and omission could confuse downstream planning, say so explicitly and why.

- **System**: material / molecule / interface / defect / reaction name, formula, composition.
- **Structure context**: periodic vs non-periodic, phase / polymorph / surface termination / supercell size / defect site / adsorbate orientation.
- **Reference states and conventions**: clean surface, isolated molecule, bulk reference, chemical potential convention, reaction reference state.
- **Electronic state**: charge state, spin / magnetism, open vs closed shell, expected magnetic ordering.
- **Environment**: temperature, pressure, solvent, vacuum gap, electrochemistry conditions, boundary conditions.
- **Method tier and method details**: target accuracy tier, XC functional family, basis / pseudopotential family, dispersion treatment, `+U`, SOC, vdW treatment, other correction schemes.
- **Relaxation and correction policy**: ions-only vs cell+ions, fixed layers, symmetry handling, dipole correction, finite-size correction, charged-cell treatment.
- **Operator constraints**: software stack (VASP / QE / ABACUS / CP2K / LAMMPS / …), HPC partition, queue limits, wall-clock budgets, data storage, checkpoint / restart policy.
- **Evidence anchors**: experimental values, reference calculations, literature with DOI / arXiv / internal memo ID, prior group results.

## S# conventions

Every important requirement, assumption, constraint, or decision should have a stable `S#` identifier.

Use this template:

```markdown
### S3 — Target accuracy tier
- Statement: lattice parameter within 1% of experimental value; forces converged to 0.01 eV/Å.
- Status: [specified]
- Source: user decision | planner default | reused from docs/oxide-bulk/SPEC.md#S6
- Rationale: consistent with the group baseline for oxide bulk phases; tighter than screening tier.
- Evidence: Chem. Mater. 2021, 33, 1234 (DOI: 10.xxx); internal memo 2024-07.
- Impacts: informs downstream acceptance thresholds and convergence planning.
```

Live decision status markers:

- `[specified]` — committed, backed by evidence or explicit user decision
- `[defaulted]` — chosen by the planner under delegation; user can override
- `[to-research]` — known unknown, pending literature review or a supporting study
- `[blocked]` — depends on an external answer or prerequisite not yet available

Retirement rule:

- `[retired]` — formerly active `S#`; keep the identifier, add a brief rationale, and do not reuse the number

Stable ID rules:

- keep existing `S#` numbers when meaning is unchanged;
- never renumber reviewed items;
- when meaning changes materially, revise the existing item if it is truly the same decision, otherwise add a new `S#`;
- when retiring an item, preserve the old identifier and explain what replaced it, if anything.

## Materials-specific evidence anchor patterns

Use evidence anchors that are specific enough to support downstream choices.

- **Literature**: author, year, DOI or arXiv ID, and the specific value, figure, benchmark, or claim being cited.
- **Prior calculation**: project path or artifact path plus which deliverable justified the choice.
- **Experiment**: sample ID, technique, measured value with uncertainty.
- **Heuristic / rule-of-thumb**: name the rule and why it applies here.
- **Imported context from another planning unit**: source planning unit and, when possible, the exact `S#` or artifact section reused.

Prefer planning-unit-relative file paths for local artifacts when practical.

## Interfaces to downstream artifacts

`SPEC.md` must prepare downstream artifacts without replacing them.

The `Interfaces to ACCEPTANCE.md and PLAN.md` section should name:

- which `S#` items require quantitative thresholds, validation methods, or evidence in `ACCEPTANCE.md`;
- which `S#` items imply streams, dependencies, decompositions, blockers, or special handling in `PLAN.md`;
- which open questions are non-blocking vs likely to block downstream progression.

Do not write acceptance thresholds or task queues into `SPEC.md`.

Do not modify `ACCEPTANCE.md` or `PLAN.md` in this skill.
If they now need revision because `SPEC.md` changed, report that explicitly in the output summary.

## Preferred structure

1. Artifact Status
2. Problem Statement
3. Objective and Intended Deliverables
4. Scope
5. Explicit Non-Scope
6. Research System / Model Definition
7. Constraints, Assumptions, and Defaulted Decisions
8. Evidence Anchors and Canonical References
9. Major Decisions and Rationale (`S#` items)
10. Open Questions and Research Gaps
11. Deferred Ideas
12. Interfaces to `ACCEPTANCE.md` and `PLAN.md`

You may add short appendices when they materially support the stable semantics, but do not turn `SPEC.md` into a bulky background memo.

## Material update rule

A material update changes one or more of:

- objective or intended deliverables,
- scope or explicit non-scope,
- research system / model definition,
- major assumptions, defaults, or constraints,
- the evidence basis for a key decision,
- the meaning, lifecycle, or downstream impact of an `S#` item.

Typos, wording-only edits, and formatting cleanup do not count as material updates.

## Recommended output path

Primary path:

```text
<planning-unit>/SPEC.md
```

When creating a new planning unit under the current workspace convention, the default is:

```text
docs/<topic-slug>/SPEC.md
```

## Output contract

- Create or update `<planning-unit>/SPEC.md`.
- Include an `Artifact Status` section with planning unit, source, status, and update date.
- Treat `SPEC.md` as durable downstream context once reviewed; do not rely on chat history.
- Preserve canonical references and imported decisions with source.
- Record branch ideas under `Deferred Ideas` instead of silently expanding scope.
- Keep existing `S#` IDs stable where meaning is unchanged; mark retired IDs explicitly and do not reuse their numbers.
- Do not modify `ACCEPTANCE.md` or `PLAN.md` here; report downstream revision needs instead.

After any create or material update, return a short checkpoint that includes:

- artifact path;
- whether it was created or revised;
- sections materially changed;
- `S#` items added, updated, or retired;
- what remains `[to-research]` or `[blocked]`;
- which downstream artifacts (`ACCEPTANCE.md`, `PLAN.md`) should now be revised;
- one review prompt: `approve` / `revise` / `provide missing input`.

A checkpoint is satisfied when the user approves the artifact, requests revisions, provides missing input, or explicitly defers unresolved non-blocking items.
