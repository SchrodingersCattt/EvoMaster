---
name: abacus
description: "Use to RUN ABACUS calculations: SCF, relax/cell-relax, band/DOS, MD, surfaces, defects, DFT+U, phonon, EOS. Covers input prep, Bohrium submit, parsing, parameter guards, K-points, validation. Do NOT use for ABACUS literature search, generic DFT theory, or input-only handoff."
skill_type: operator
---

# ABACUS Skill

PW and LCAO basis; produces correct, runnable files and avoids silent-failure configurations.

## Gate — STOP Conditions

- **PP/orbital unresolvable**: If any element in STRU cannot be found in `references/apns_pseudopotentials_v1.list` (or orbital in `references/apns_orbitals_efficiency_v1.list` for LCAO), STOP and report. Do not guess filenames.
- **ASE Calculator mode**: When ABACUS is used as an ASE Calculator (ASE-driven relax, NEB, vibrations), INPUT must use `calculation scf` only. STOP if task implies internal ABACUS optimizer + ASE control simultaneously.
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
| Extracting results from output | `references/output_params.md` |
| Troubleshooting | `references/troubleshooting.md` |

PP/orbital filename lookup:
- `references/apns_pseudopotentials_v1.list`
- `references/apns_orbitals_efficiency_v1.list`

## Pre-conditions — Internalize Before Writing

- **AFM-prone oxides** (NiO, FeO, MnO under DFT+U): do not default to FM. Use antiparallel initialization by splitting magnetic species in STRU.
- **DFT+U multi-species**: `orbital_corr` and `hubbard_u` must cover ALL correlated species (same order as ATOMIC_SPECIES).
- **CIF/POSCAR → STRU**: convert lattice and positions faithfully before applying task-specific parameters.
- **ecutwfc defaults**: `100` (LCAO), `50` (PW). Task requirements override.
- **smearing_sigma default**: `0.015` unless task specifies otherwise.

## Workflow

1. **Read provided STRU** — determine basis_type from presence of `NUMERICAL_ORBITAL`. Reuse PP/orbital filenames exactly.
2. **Read references** per Routing table above (always read `input_examples.md`; additionally read method-specific file if applicable).
3. **Resolve PP/orbital** for Bohrium:
   - Look up each element in `references/apns_pseudopotentials_v1.list`.
   - For LCAO: also look up `references/apns_orbitals_efficiency_v1.list`.
   - Write resolved filenames into STRU. If unresolvable → STOP (Gate rule).
4. **Write INPUT + KPT** following examples and mandatory-parameter tables from step 2.
5. **Run validator**: `python ${SKILL_DIR}/scripts/validate_input.py --dir <dir>`. Fix all FAIL items before proceeding.
6. **Submit to Bohrium** (if task requires execution) — see defaults below.

## Bohrium Submission Defaults

| Item | Default |
|------|---------|
| image | `registry.dp.tech/dptech/dp/native/hub/mrdic2/abacusp:1.0.3-1778742780` |
| machine | `c32_m128_cpu` |
| cmd | `OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1` |

PP path: `/root/apns-pseudopotentials-v1/`. Orbital path: `/root/apns-orbitals-efficiency-v1/`.
