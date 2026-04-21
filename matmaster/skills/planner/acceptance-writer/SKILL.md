---
name: acceptance-writer
description: "Writes or updates ACCEPTANCE.md — the measurable completion contract that turns vague success language into checkable pass/fail criteria with verification methods, thresholds, and evidence requirements for a materials-computation project."
skill_type: operator
---

# ACCEPTANCE Writer Skill

Produce or revise `ACCEPTANCE.md` so a reviewer can decide whether the work is done, using the artifact alone. ACCEPTANCE is the completion contract. It answers **what counts as done, how we will check it, and what evidence proves it**. It is not a task sequence and not a background document.

## What ACCEPTANCE.md owns (and does not)

| Owns | Does not own |
|------|--------------|
| measurable pass/fail criteria with `A#` tags | problem scope or system definition (→ `SPEC.md`) |
| verification method per criterion | task list, queue, progress (→ `PLAN.md`) |
| numeric thresholds and evidence requirements | raw results or narrative logs |
| severity classification (blocking / major / minor) | hidden qualitative judgments like "reasonable" |
| waiver rules and waiver conditions | silent scope reduction |

See `reference/mandatory-artifact-read.md` for required prior reads, and `reference/traceability-contract.md` for `A#` / `S#` linking conventions.

## Artifact-level adaptation

`ACCEPTANCE.md` is the scientific gate contract for a planning unit.

It borrows from GSD by making completion gate-oriented:

- criteria have pass/fail semantics;
- blocking, major, and minor severity are explicit;
- verification methods and evidence requirements are attached to each `A#`;
- waiver rules are visible;
- plan checking can verify that every blocking `A#` has task support.

It borrows from Superpowers by making done concrete before implementation:

- write success criteria before execution handoff;
- convert vague language into measurable checks;
- require review before implementation;
- self-review for placeholders, ambiguity, and unverifiable standards.

Every `ACCEPTANCE.md` should include:

```markdown
## Artifact Status

- Status: draft | review | locked | superseded
- Related SPEC: SPEC.md
- Last updated: YYYY-MM-DD
```

Use gate verdict language consistently:

- `PASS`
- `FAIL`
- `PARTIAL`
- `WAIVED`

If a blocking criterion is unmet and not waived, handoff must not proceed.

## Criterion families for materials-computation projects

Use these families as a checklist. Omit ones that do not apply and say so.

- **Parameter convergence**: cutoff energy, k-point density, smearing width, SCF tolerance, integration grids — each criterion names the convergence metric and its threshold.
- **Literature / experimental anchor agreement**: lattice constant, bulk modulus, band gap, formation energy, reaction enthalpy, adsorption energy — each vs a named reference within a stated tolerance.
- **Physical sanity**: bond lengths / angles within expected ranges, magnetic moments match expected configuration, total charge sums correctly, no unexpected imaginary phonons, energy monotonicity in relaxation.
- **Reference-state consistency**: same xc, same basis / pseudopotential family, same corrections, same FFT / integration grids across the structures being compared.
- **Data completeness**: all required snapshots, trajectories, checkpoints, or intermediate logs saved; metadata (xc, k-mesh, software version, commit hash) recorded alongside.
- **Reproducibility / traceability**: input files archived, commit hash recorded, environment (software version, image, container hash) documented, submission scripts preserved.
- **Model-quality thresholds (if ML is involved)**: validation MAE / RMSE, test-set stratification, extrapolation behavior, uncertainty calibration, dataset version lock.
- **Deliverables**: required figures, tables, summary report, supplementary data package.
- **Handoff readiness**: artifact locations stable, cross-links to SPEC / PLAN resolved, reviewer sign-off captured.

## Criterion template

Every meaningful criterion follows this shape:

```markdown
### A5 — k-point convergence of cohesive energy
- Type: scientific
- Related spec: S3 (accuracy tier), S6 (xc functional)
- Applies to: bulk baseline calculations (P2)
- Requirement: cohesive energy converged to within 1 meV/atom w.r.t. k-point density.
- Verification: run k-mesh series {4×4×4, 6×6×6, 8×8×8, 10×10×10}; plot cohesive energy vs k-density; report the chosen converged mesh.
- Pass threshold: |E(N) − E(N−1)| ≤ 1 meV/atom for two consecutive refinements.
- Evidence required: convergence CSV + plot + chosen k-mesh logged in the input record.
- Severity if unmet: blocking
- Waiver allowed: no
- Waiver conditions: —
- Notes: coarsen only if the wall-clock budget (S9) becomes binding; requires explicit user sign-off.
```

## Materials-specific threshold patterns

Prefer concrete, domain-defensible thresholds. State the source of every threshold (user decision, group standard, specific paper, convergence study).

- Lattice constants: ≤ 1% deviation from experiment, within experimental uncertainty.
- Bulk modulus: ≤ 10% deviation vs experiment or higher-level reference.
- Formation / reaction energies: ≤ 50 meV per atom (or per formula unit) vs benchmark.
- SCF convergence: energy change ≤ 1e-6 eV; tighten to ≤ 1e-8 eV for phonons and NEB.
- Ionic relaxation: max force ≤ 0.01 eV/Å for production; ≤ 0.05 eV/Å acceptable for screening tier only.
- Band gaps: cite benchmark (experiment / GW) and state the known functional bias (e.g. PBE underestimates).
- Phonon sanity: no imaginary frequencies at Γ except explicitly expected soft modes.
- ML potential regression: validation MAE below X meV/atom on held-out split Y; state the split policy.

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
10. Review Record

## Output contract

- Create or update `ACCEPTANCE.md` at `docs/<topic-slug>/ACCEPTANCE.md`.
- Include an `Artifact Status` section with status, related SPEC, and update date.
- Include a `Gate Model` section that explains blocking, major, minor, waiver, and handoff behavior.
- Use `PASS / FAIL / PARTIAL / WAIVED` language where criteria or gates need verdicts.
- Make blocking criteria impossible to bypass silently; handoff must not proceed while unresolved blocking criteria remain.
- Keep existing `A#` IDs stable where meaning is unchanged.
- Return a short summary that names:
  - new or changed criteria;
  - which are blocking vs major vs minor;
  - which criteria are still provisional (`[to-research]`, pending a convergence study or a literature value);
  - which `PLAN` tasks (`T#`) must support each blocking criterion, so the plan checker can verify coverage.
