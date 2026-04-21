---
name: abacus
description: "Use this skill for ABACUS tasks. Keep it decision-focused: enforce hard guards, generate valid INPUT/STRU/KPT, and delegate detailed templates/troubleshooting to references."
skill_type: operator
---

# ABACUS Skill

ABACUS supports PW and LCAO basis sets. This skill should focus on producing
correct, runnable files and avoiding silent-failure configurations.

## When to Use

Use this skill for any ABACUS task: SCF, band/DOS, relax/cell-relax, MD,
electric-field/dipole, vacancy/defect/supercell, surface/work-function, BSSE.

## Minimum Workflow

1. Read provided `STRU` first and reuse filenames exactly (PP/orbital/structure).
2. Generate `INPUT` (and `KPT` when needed).
3. For uncertain params/workflows, check official ABACUS docs on web first.
4. Use local `references/*` as fallback when web docs are unavailable.
5. Run single-file diagnosis with `diagnose_input.py` (`--software abacus`).
6. Run workspace preflight with `preflight_abacus.py` (`INPUT` + `STRU` + `KPT` cross-check).
7. If needed, use `diagnose_input.py --fix`, then re-check.

## Hard Guards (Must Pass)

- `ntype` in `INPUT` must equal species count in `STRU` `ATOMIC_SPECIES`.
- For `relax`/`cell-relax`/`md`, set `cal_force 1` explicitly.
- For `cell-relax`, also set `cal_stress 1` explicitly.
- For SCF -> NSCF workflows:
  - SCF: `out_chg 1`
  - NSCF: `init_chg file`, `symmetry 0`, `nbands <N>`, plus `out_band 1` or `out_dos 1`
- If file names are not defaults, set `stru_file` and `kpoint_file` to real names.
- Every referenced file must exist in workspace.

## K-point Rules

- Supercell/vacancy/defect/BSSE: prefer `kspacing` in `INPUT` (avoid brittle manual meshes).
- Band structure: use dedicated line-mode KPT for NSCF step.
- SCF and NSCF should not share a single KPT by default in band/DOS workflows.

## Parameter Baseline (Use Judgment, Not Blind Fixed Values)

- Use physically reasonable `ecutwfc`, `smearing`, and SCF thresholds for system and PP quality.
- Typical production defaults are acceptable, but task requirements override defaults.
- If the task specifies cutoffs or convergence policy, follow the task first.
- Keep multi-file studies (EOS/surface/vacancy comparisons) consistent on core numerics.

## Task-Specific Deltas

- `relax`: include `force_thr_ev` and `relax_nmax`.
- `cell-relax`: include `force_thr_ev`, `stress_thr`, and `relax_nmax`.
- Work function / slab potential: `out_pot 2`; add dipole correction when needed.
- Spin/noncollinear/SOC: keep `nspin`, `noncolin`, and `lspinorb` consistent.

## Bohrium Submission Defaults

Keep the previous default profile unless task/environment explicitly overrides it.

| Item | Default |
|------|---------|
| image | `registry.dp.tech/dptech/abacus:LTSv3.10.1` |
| machine | `c32_m128_cpu` |
| cmd | `OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1` |

Notes:
- `-np` is typically half of CPU cores for this profile (32 -> 16).
- For GPU tasks, use environment-approved GPU machine profiles with `basis_type pw`.

## References

Web-first policy:
- Prefer official ABACUS/Bohrium web documentation for up-to-date behavior.
- Use local references below for fallback and quick lookup.

- Input templates and multi-step examples: `references/input_examples.md`
- STRU format basics: `references/stru_format.md`
- Multi-species STRU examples: `references/stru_multispecies.md`
- Electric field and dipole notes: `references/electric_field.md`
- Troubleshooting: `references/troubleshooting.md`
- Output parameter guide: `references/output_params.md`
