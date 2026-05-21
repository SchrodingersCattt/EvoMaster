---
name: abacus
description: "Use to RUN ABACUS calculations: SCF, relax/cell-relax, band/DOS, MD, surfaces, defects, DFT+U, phonon, EOS. Covers input prep, Bohrium submit, parsing, parameter guards, K-points, validation. Do NOT use for ABACUS literature search, generic DFT theory, or input-only handoff."
skill_type: operator
---

# ABACUS Skill

PW and LCAO basis; produces correct, runnable files and avoids silent-failure configurations.

## Gate — STOP Conditions

- **PP/orbital unresolvable**: If an element's PP/orbital is neither found in the APNS lists (`references/apns_pseudopotentials_v1.list` / `references/apns_orbitals_efficiency_v1.list`) NOR provided as a file in the workspace, STOP and report. Do not guess filenames.
- **ASE Calculator mode**: If task uses ASE to drive geometry optimization (ASE Optimizer, NEB, vibrations) AND INPUT has `calculation relax|cell-relax|md` → STOP. ASE-driven workflows require `calculation scf` in INPUT.
- **Not ABACUS**: Task asks for QE/VASP/CP2K/LAMMPS → this skill does not apply.

## Reference Routing — Read BEFORE Writing

| Task involves | Read |
|---------------|------|
| **Any INPUT generation** | `references/input_examples.md` ← always |
| Multi-element (>=3 species) | `references/stru_multispecies.md` |
| Writing/editing STRU | `references/stru_format.md` |
| Molecular dynamics | `references/input_md.md` |
| Hybrid functional (HSE/PBE0) | `references/input_hybrid.md` |
| DeePKS | `references/input_deepks.md` |
| RT-TDDFT | `references/input_rt_tddft.md` |
| Stochastic DFT | `references/input_sdft.md` |
| LR-TDDFT | `references/input_lr_tddft.md` |
| van der Waals (D2/D3) | `references/input_vdw.md` |
| Surface/slab + dipole/E-field/gate | `references/electric_field.md` |
| Bader / wavefunction / get_wf | `references/advanced_tasks.md` |
| DFT+U / phonon / EOS / surface energy | `references/advanced_tasks.md` |
| H/S matrix output (`out_mat_hs`, `out_mat_hs2`, `get_S`) | `references/output_params.md` |
| Extracting results from output | `references/output_params.md` |
| Troubleshooting | `references/troubleshooting.md` |

## Pre-conditions — Internalize Before Writing

- **AFM-prone oxides** (NiO, FeO, MnO under DFT+U): do not default to FM. Split magnetic species in STRU (e.g. Ni_up +2.0, Ni_down −2.0) — see `references/stru_format.md` for per-species moment syntax.
- **DFT+U multi-species**: `orbital_corr` and `hubbard_u` must cover ALL correlated species (same order as ATOMIC_SPECIES).
- **CIF/POSCAR → STRU**: convert lattice and positions faithfully before applying task-specific parameters.
- **ecutwfc defaults**: `100` (LCAO), `50` (PW). Task requirements override.
- **smearing_sigma default**: `0.015` unless task specifies otherwise.
- **pseudo_dir / orbital_dir**: always present in INPUT — never omit.

## Workflow

1. **Read provided STRU** — determine basis_type from presence of `NUMERICAL_ORBITAL`.
2. **Read references** per Routing table above (always read `input_examples.md`; additionally read method-specific file if applicable).
3. **Resolve PP/orbital filenames** (check both `apns_pseudopotentials_v1.list` and `apns_orbitals_efficiency_v1.list` for LCAO):
   - PP/orbital filename in APNS list → ensure STRU uses exact APNS filename, set `pseudo_dir /root/apns-pseudopotentials-v1/`, `orbital_dir /root/apns-orbitals-efficiency-v1/`.
   - PP/orbital filename NOT in APNS list but file exists in workspace → keep STRU as-is, set `pseudo_dir ./`, `orbital_dir ./`.
   - Neither in APNS nor in workspace → STOP (Gate rule).
4. **Write INPUT + KPT** following examples and mandatory-parameter tables from step 2.
5. **Run validator**: `python ${SKILL_DIR}/scripts/validate_input.py --dir <dir>`. Fix all FAIL items before proceeding.
6. **Submit to Bohrium** (if task requires execution) — see defaults below.

## Bohrium Submission Defaults

| Item | Default |
|------|---------|
| image | `registry.dp.tech/dptech/dp/native/hub/mrdic2/abacusp:1.0.3-1778742780` |
| machine | `c32_m128_cpu` |
| cmd | `OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1` |
