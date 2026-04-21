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
electric-field/dipole, vacancy/defect/supercell, surface/work-function, BSSE,
**hybrid DFT (HSE06/PBE0), DFT+U, EOS, phonon, SOC/noncollinear, Berry phase,
projected band structure, vdW-corrected, and multi-step complex workflows.**

## Minimum Workflow

1. Read provided `STRU` first and reuse filenames exactly (PP/orbital/structure).
2. Generate `INPUT` (and `KPT` when needed).
3. For uncertain params/workflows, check local `references/*` first.
4. If references are insufficient or ambiguous, use official ABACUS docs on web as fallback.

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
- Distinguish `LCAO` vs `PW` parameter semantics and sensitivity; do not directly copy basis-specific settings across modes.
- For critical parameters, verify intent and physical meaning before finalizing.
- Typical production defaults are acceptable, but task requirements override defaults.
- If the task specifies cutoffs or convergence policy, follow the task first.
- Keep multi-file studies (EOS/surface/vacancy comparisons) consistent on core numerics.

## Task-Specific Deltas

- `relax`: include `force_thr_ev` and `relax_nmax`.
- `cell-relax`: include `force_thr_ev`, `stress_thr`, and `relax_nmax`.
- Work function / slab potential: `out_pot 2`; add dipole correction when needed.
- Spin/noncollinear/SOC: keep `nspin`, `noncolin`, and `lspinorb` consistent.
- **Hybrid functionals (HSE06/PBE0)**: MUST use `basis_type pw` (not LCAO); set `dft_functional hse` or `pbe0`; no `.orb` files or `NUMERICAL_ORBITAL` in STRU. See `references/advanced_tasks.md`.
- **DFT+U**: set `lda_plus_u 1`, `hubbard_u`, `orbital_corr` (one value per species in ATOMIC_SPECIES order); requires `nspin 2`.
- **Phonon (finite displacement)**: use Phonopy to generate displaced supercells, run SCF with `cal_force 1` + `scf_thr 1e-8` on each. See `references/advanced_tasks.md`.
- **EOS / bulk modulus**: generate multiple STRU files at different volumes; keep all INPUT parameters identical except `stru_file`. See `references/advanced_tasks.md`.
- **Projected band structure**: add `out_proj_band 1` alongside `out_band 1` in NSCF step.
- **vdW correction**: add `vdw_method d3_bj` (recommended for PBE) or `d2`.
- **Berry phase (polarization)**: add `berry_phase 1`, `gdir <1|2|3>`.
- **Multi-step run.sh**: for workflows with 3+ steps (relax→SCF→NSCF), write a `run.sh` script and submit with `cmd "bash run.sh > log 2>&1"`. See `references/advanced_tasks.md` for patterns.

## Bohrium Submission Defaults

This section is the **single source of truth** for ABACUS `image` / `machine` / `cmd` on Bohrium. Other skills (for example `matmaster/skills/playground-skills/input-manual-helper`) refer here instead of copying the values.

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

Reference-first policy:
- Prefer local references below for stable and task-aligned guidance.
- Use official ABACUS/Bohrium web documentation as fallback when local references are insufficient.

- Input templates and multi-step examples: `references/input_examples.md`
- **Advanced tasks (hybrid DFT, DFT+U, EOS, phonon, SOC, Berry phase, vdW, projected band, multi-step run.sh)**: `references/advanced_tasks.md`
- STRU format basics: `references/stru_format.md`
- Multi-species STRU examples: `references/stru_multispecies.md`
- Electric field and dipole notes: `references/electric_field.md`
- Troubleshooting: `references/troubleshooting.md`
- Output parameter guide (files, grep patterns): `references/output_params.md`
- Parsed results and plots after the run: `matmaster/skills/playground-skills/result-analysis` (`parse_abacus.py`, etc.)
