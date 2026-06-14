---
name: abacus
description: "Use to PREPARE or RUN ABACUS calculations: SCF, relax/cell-relax, band/DOS, MD, surfaces, defects, DFT+U, phonon, EOS. Covers input prep (even if not submitting), Bohrium submit, parsing, parameter guards, K-points, validation. Do NOT use for ABACUS literature search or generic DFT theory."
---

# ABACUS Skill

PW and LCAO basis; produces correct, runnable files and avoids silent-failure configurations.

## Gate — STOP Conditions

- **PP/orbital unresolvable**: If an element's PP/orbital is neither found in the APNS lists (`${SKILL_DIR}/references/apns_pseudopotentials_v1.list` / `${SKILL_DIR}/references/apns_orbitals_efficiency_v1.list`) NOR provided as a file in the workspace, STOP and report. Do not guess filenames.
- **ASE Calculator mode**: If task uses ASE to drive geometry optimization (ASE Optimizer, NEB, vibrations) AND INPUT has `calculation relax|cell-relax|md` → STOP. ASE-driven workflows require `calculation scf` in INPUT.
- **Not ABACUS**: Task asks for QE/VASP/CP2K/LAMMPS → this skill does not apply.

## Reference Routing — Read BEFORE Writing

| Task involves | Read |
|---------------|------|
| **Any INPUT generation** | `${SKILL_DIR}/references/input_examples.md` ← always |
| Multi-element (>=3 species) | `${SKILL_DIR}/references/stru_multispecies.md` |
| Writing/editing STRU | `${SKILL_DIR}/references/stru_format.md` |
| Molecular dynamics | `${SKILL_DIR}/references/input_md.md` |
| Hybrid functional (HSE/PBE0) | `${SKILL_DIR}/references/input_hybrid.md` |
| DeePKS | `${SKILL_DIR}/references/input_deepks.md` |
| RT-TDDFT | `${SKILL_DIR}/references/input_rt_tddft.md` |
| Stochastic DFT | `${SKILL_DIR}/references/input_sdft.md` |
| LR-TDDFT | `${SKILL_DIR}/references/input_lr_tddft.md` |
| van der Waals (D2/D3) | `${SKILL_DIR}/references/input_vdw.md` |
| Surface/slab + dipole/E-field/gate | `${SKILL_DIR}/references/electric_field.md` |
| Bader / wavefunction / get_wf | `${SKILL_DIR}/references/advanced_tasks.md` |
| DFT+U / phonon / EOS / surface energy | `${SKILL_DIR}/references/advanced_tasks.md` |
| H/S matrix output (`out_mat_hs`, `out_mat_hs2`, `get_S`) | `${SKILL_DIR}/references/output_params.md` |
| Extracting results from output | `${SKILL_DIR}/references/output_params.md` |
| Troubleshooting | `${SKILL_DIR}/references/troubleshooting.md` |

## Pre-conditions — Internalize Before Writing

- **AFM-prone oxides** (NiO, FeO, MnO under DFT+U): do not default to FM. Split magnetic species in STRU (e.g. Ni_up +2.0, Ni_down −2.0) — see `${SKILL_DIR}/references/stru_format.md` for per-species moment syntax.
- **DFT+U multi-species**: `orbital_corr` and `hubbard_u` must cover ALL correlated species (same order as ATOMIC_SPECIES).
- **CIF/POSCAR → STRU**: convert lattice and positions faithfully before applying task-specific parameters.
- **ecutwfc defaults**: `100` (LCAO), `50` (PW). Task requirements override.
- **smearing_sigma default**: `0.015` unless task specifies otherwise.
- **pseudo_dir / orbital_dir**: always present in INPUT — never omit.
- **Slab k-points**: if smearing is `gauss`/`mp`/`fd` (metallic), in-plane mesh ≥ 12×12 (hard floor). Vacuum direction always `1`. Validator enforces this post-hoc but getting it right avoids a fix cycle.

## Workflow

1. **Read provided STRU** — determine basis_type from presence of `NUMERICAL_ORBITAL`.
2. **Read references** per Routing table above (always read `${SKILL_DIR}/references/input_examples.md`; additionally read method-specific file if applicable).
3. **Resolve PP/orbital filenames** — read `${SKILL_DIR}/references/apns_pseudopotentials_v1.list` and `${SKILL_DIR}/references/apns_orbitals_efficiency_v1.list` for LCAO (do NOT grep `/root/apns-*` on the filesystem):
   - PP/orbital filename in APNS list → ensure STRU uses exact APNS filename, set `pseudo_dir /root/apns-pseudopotentials-v1/`, `orbital_dir /root/apns-orbitals-efficiency-v1/`.
   - PP/orbital filename NOT in APNS list but file exists in workspace → keep STRU as-is, set `pseudo_dir ./`, `orbital_dir ./`.
   - Neither in APNS nor in workspace → STOP (Gate rule).

   > **⚠ `/root/apns-*` are Bohrium runtime paths** (pre-installed in the Docker image). They will NOT exist in the local workspace — do not fallback to `./` because the directory is absent locally.
4. **Write INPUT + KPT** following examples and mandatory-parameter tables from step 2.
5. **Run validator**: `python ${SKILL_DIR}/scripts/validate_input.py --dir <dir>`. Fix all FAIL items before proceeding.
6. **Submit to Bohrium** (if task requires execution) — see defaults below.

## Bohrium Submission Defaults

| Item | Default |
|------|---------|
| image | `registry.dp.tech/dptech/matmaster:abacus-1.0.3-1778742780` |
| machine | `c32_m128_cpu` |
| cmd | `OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1` |
