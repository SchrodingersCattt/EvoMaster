---
name: abacus
description: "MUST use this skill for ANY task involving ABACUS (SCF, relax, cell-relax, band structure, DOS, MD, work function, BSSE, vacancy, surface energy, DFT+U, phonon, EOS). Contains hard guards on parameters, low-cost benchmark ranges, K-point rules, and validation scripts that prevent silent failures."
skill_type: operator
---

# ABACUS Skill

ABACUS supports PW and LCAO basis sets. This skill should focus on producing
correct, runnable files and avoiding silent-failure configurations.

## Minimum Workflow

1. Read provided `STRU` first and reuse filenames exactly (PP/orbital/structure).
   Pseudopotentials (`.upf`) default path: `/root/apns-pseudopotentials-v1/`.
   Orbitals (`.orb`) default path: `/root/apns-orbitals-efficiency-v1/`.
2. Generate `INPUT` (and `KPT` when needed).
3. For Bohrium jobs:
   - Set `pseudo_dir` and `orbital_dir` explicitly in `INPUT`.
   - Ensure filenames in `STRU` exactly match files in those directories.
   - Pseudopotential lookup order:
     `references/apns_pseudopotentials_v1.list` -> `references/stru_multispecies.md`.
   - Orbital lookup: `references/apns_orbitals_efficiency_v1.list`.
4. For uncertain params/workflows, check local `references/*` first.
5. If references are insufficient or ambiguous, use official ABACUS docs on web as fallback.
6. For complex tasks, do not rely only on pretrained priors; gather relevant knowledge from multiple sources to enrich context before finalizing inputs.

## Hard Guards (Must Pass)

- `ntype` in `INPUT` must equal species count in `STRU` `ATOMIC_SPECIES`.
- For Bohrium jobs, `INPUT` must include explicit `pseudo_dir` and `orbital_dir`, and PP/orbital filenames in `STRU` must exist in those directories.
- For `relax`/`cell-relax`/`md`, set `cal_force 1` explicitly.
- For `cell-relax`, also set `cal_stress 1` explicitly.
- For SCF -> NSCF workflows:
  - SCF: `out_chg 1`
  - NSCF: `init_chg file`, `symmetry 0`, `nbands <N>`, plus `out_band 1` or `out_dos 1`
- If file names are not defaults, set `stru_file` and `kpoint_file` to real names.
- Every referenced file must exist either in workspace or in the configured runtime directories.

## K-point Rules

- Supercell/vacancy/defect/BSSE: prefer `kspacing` in `INPUT` (avoid brittle manual meshes).
- Band structure: use dedicated line-mode KPT for NSCF step.
- SCF and NSCF should not share a single KPT by default in band/DOS workflows.
- Metal slab calculations: **minimum 12×12 in-plane** k-points (or equivalent `kspacing ≤ 0.10` in-plane). Do not use less.

## Parameter Baseline (Use Judgment, Not Blind Fixed Values)

- Use physically reasonable `ecutwfc`, `smearing`, and SCF thresholds for system and PP quality.
- `ecutwfc` (Type: Real): energy cutoff for plane-wave functions. Unit is `Ry`. Even under `basis_type lcao`, `ecutwfc` is still required because local pseudopotential parts and related forces are evaluated in plane-wave representation. Default baseline: `50` (PW), `100` (LCAO), unless task requirements override.
- In low-cost or benchmark settings, `ecutwfc` can be set below the default baseline if task intent prioritizes speed over accuracy.
- For fast tasks with lower accuracy requirements, choose lower-precision settings (including lower `ecutwfc`) to prioritize turnaround time.
- Distinguish `LCAO` vs `PW` parameter semantics and sensitivity; do not directly copy basis-specific settings across modes.
- Use `smearing_sigma 0.015` as the default starting point unless the task specifies otherwise.
- For critical parameters, verify intent and physical meaning before finalizing.
- If a parameter meaning is unclear, check the official ABACUS input reference:
  `http://abacus.deepmodeling.com/en/latest/advanced/input_files/input-main.html`.
- Typical production defaults are acceptable, but task requirements override defaults.
- If the task specifies cutoffs or convergence policy, follow the task first.
- **Low-cost / benchmark mode**: when the task requests "low-cost", "benchmark", or "minimal cost" parameters, significantly reduce `ecutwfc` from production defaults. See `references/input_examples.md` for guidance.
- Keep multi-file studies (EOS/surface/vacancy comparisons) consistent on core numerics.

## Task-Specific Deltas

- `relax`: include `force_thr_ev` and `relax_nmax`.
- `cell-relax`: include `force_thr_ev`, `stress_thr`, and `relax_nmax`.
- Work function / slab potential: `out_pot 2`; add dipole correction when needed.
- Spin/noncollinear/SOC: keep `nspin`, `noncolin`, and `lspinorb` consistent.

## Bohrium Submission Defaults

This section is the **single source of truth** for ABACUS `image` / `machine` / `cmd` on Bohrium. Other skills (for example `matmaster/skills/input-manual-helper`) refer here instead of copying the values.

Keep the previous default profile unless task/environment explicitly overrides it.

| Item | Default |
|------|---------|
| image | `registry.dp.tech/dptech/dp/native/hub/mrdic2/abacusp:1.0.1-1778080680` |
| machine | `c32_m128_cpu` |
| cmd | `OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1` |

Notes:
- `-np` is typically half of CPU cores for this profile (32 -> 16).
- For GPU tasks, use environment-approved GPU machine profiles with `basis_type pw`.

## Pre-Submission Validation

After generating all INPUT/STRU/KPT files, run the validation script before Bohrium submission:
```bash
python ${SKILL_DIR}/scripts/validate_input.py --dir <input_dir>
```
It catches the most common silent failures: ntype mismatch, missing cal_force/cal_stress, missing out_chg for SCF→NSCF, wrong basis_type, missing PP/orbital files, and stru_file/kpoint_file reference errors. Fix any FAIL items before submitting.

## References

Reference-first policy:
- Prefer local references below for stable and task-aligned guidance.
- Use official ABACUS/Bohrium web documentation as fallback when local references are insufficient.

Each entry below ends with an `*Applies to*` line that lists the **material systems and task scenarios actually covered inside that file**. Use it to locate the right reference from the task description — do not rely on filenames alone.

- **Pre-flight validator**: `scripts/validate_input.py` — run before every Bohrium submit.
  - *Applies to*: every ABACUS submission. Universal pre-flight catch for `ntype` mismatch, missing `cal_force`/`cal_stress`, missing `out_chg` for SCF→NSCF, wrong `basis_type`, missing PP/orbital files, and `stru_file`/`kpoint_file` reference errors.

- **Input templates and multi-step examples**: `references/input_examples.md`
  - *Applies to*: SCF / relax / cell-relax of bulk crystals; SCF→NSCF two-step workflows for **band structure and DOS** (including line-mode KPT for **1D nanoribbons** and **2D materials** such as graphene / MoS₂); **BSSE ghost-atom calculations** (bulk and slab); **work function / electrostatic potential of slabs**; multi-file comparative studies (**surface energy**, **vacancy formation energy**); **low-cost / benchmark mode** parameter reduction.

- **APNS pseudopotential list**: `references/apns_pseudopotentials_v1.list`
  - *Applies to*: Bohrium ABACUS jobs that need exact `.upf` filenames under `/root/apns-pseudopotentials-v1/`; use before submit when STRU species names must match the runtime pseudopotential inventory.

- **APNS orbital list**: `references/apns_orbitals_efficiency_v1.list`
  - *Applies to*: LCAO Bohrium ABACUS jobs that need exact `.orb` filenames under `/root/apns-orbitals-efficiency-v1/`; use with `basis_type lcao` before submit when STRU orbital entries must match the runtime inventory.

- **STRU format basics**: `references/stru_format.md`
  - *Applies to*: any task that writes or edits a STRU file; **LCAO BSSE ghost-atom syntax** (empty species, zero moment, frozen mobility); structures with **initial magnetic moments** or **selective dynamics** (partially frozen atoms, e.g. fixed-bottom-layer slabs).

- **Multi-species STRU examples**: `references/stru_multispecies.md`
  - *Applies to*: **binary III-V / II-VI semiconductors** (GaAs-style zinc blende); **ternary perovskite oxides** (BaTiO₃-style ABO₃); **semiconductor slab models with vacuum** (Si(100)-style); **spin-polarized magnetic metals** (bcc Fe with `nspin 2` + mixing tuning); converting CIF / POSCAR structures into STRU; element-to-orbital filename lookup for common elements (H, C, N, O, Si, Fe, Cu, Mo, Ti, Ba, Ga, As, Zn, S, Al).

- **Electric field and dipole notes**: `references/electric_field.md`
  - *Applies to*: **work function and surface electrostatic potential** (asymmetric slabs needing dipole correction); **finite external electric field** for polarization / Stark / field-induced phenomena; **gate-field calculations on 2D materials and slabs** (full INPUT with `gate_flag`, `zgate`, `block_*`, `nelec`); pure dipole correction without applied field.

- **Troubleshooting**: `references/troubleshooting.md`
  - *Applies to*: every task — top-10 silent-failure catalog, decision tree for picking `calculation` from task description, and pre-flight checklists for general / relaxation / two-step (band-DOS) / supercell-vacancy-BSSE / slab calculations.

- **Output parameter guide (files, grep patterns)**: `references/output_params.md`
  - *Applies to*: any task that needs to extract results from ABACUS output — total energy, Fermi level, forces, stress, charge density cube, band eigenvalues, DOS, electrostatic potential cube, relaxed structures. Canonical mapping of INPUT keywords (`out_chg` / `out_band` / `out_dos` / `out_pot` / `cal_force` / `cal_stress` / …) to produced files and grep patterns.

- **Advanced tasks**: `references/advanced_tasks.md`
  - *Applies to*: **surface / interface energy** (bulk + slab workflow); **point defects** (vacancy formation energy with LCAO BSSE); **EOS / bulk modulus** (Birch-Murnaghan fit across scaled volumes); **transition-metal oxides and strongly-correlated systems** (DFT+U Dudarev, worked Fe₂O₃ example with `orbital_corr` / `hubbard_u` / tight SCF / `nspin 2` mixing); **phonon dispersion** via Phonopy finite-displacement; automatic basis-type detection from a provided STRU.

- **Post-run parsing & plots**: `matmaster/skills/playground-skills/result-analysis` (`parse_abacus.py`, etc.)
  - *Applies to*: automated extraction of energies / forces / band data and publication-quality plotting **after** the ABACUS run finishes on Bohrium.
