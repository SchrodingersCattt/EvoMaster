---
name: vasp
description: "Use to RUN VASP calculations: SCF, relaxation, band/DOS, MD, hybrid DFT, SOC, magnetism, DFT+U, optical, NEB. Covers INCAR/KPOINTS/POSCAR generation, Bohrium submission, parsing. Do NOT use for VASP literature search or plotting precomputed band/DOS arrays."
skill_type: operator
---

# VASP Skill

VASP (Vienna Ab initio Simulation Package) is a widely-used plane-wave DFT code. This skill
covers **input file generation only** — writing INCAR, KPOINTS, POSCAR. Running
the VASP binary locally is not allowed (commercial license); use remote
submission via Bohrium or equivalent.

## Complete Input Set

A runnable VASP calculation requires **INCAR + KPOINTS + POSCAR + POTCAR**.
When a task asks to "generate input files" or "prepare inputs", always produce
**INCAR + KPOINTS + POSCAR** (all three). POTCAR is license-restricted; note
recommended pseudopotentials but do not generate the file.

## Minimum Workflow

1. Read the task spec / JSON config to determine calculation type and system.
2. Construct POSCAR from provided structure info (formula, space group, lattice
   parameters). Use pymatgen or ASE.
3. Generate KPOINTS appropriate for the calculation type (see K-point Rules).
   Script: `scripts/generate_kpoints.py`.
4. Write INCAR with all required parameters for the calculation type (see
   `references/incar_tags.md` for the full tag reference).
5. Validate consistency: INCAR tags match the calculation type, KPOINTS match
   the system dimensionality, POSCAR atom count matches.

## Hard Guards

- **ENCUT must be set explicitly** — never rely on VASP's POTCAR default.
  Minimum: 1.3x max ENMAX from the relevant pseudopotentials. Typical: 400-520 eV.
- **ISMEAR must match the system**:
  - Metals: `ISMEAR = 1` or `2` (Methfessel-Paxton) with `SIGMA = 0.1-0.2`.
  - Semiconductors/insulators: `ISMEAR = 0` (Gaussian) with `SIGMA = 0.05`.
  - DOS / accurate total energy: `ISMEAR = -5` (tetrahedron with Blochl corrections).
  - Single atom / molecule (Gamma-only): `ISMEAR = 0`, `SIGMA = 0.01`.
- **Dispersion correction is required for weak-interaction systems unless explicitly disabled**:
  for layered materials, MOF/organic systems, weakly bound interfaces, H-passivated models, and slabs/surfaces, include `IVDW` (e.g., `11` or `12`).
  For complex dispersion setups, first consult `https://vasp.at/wiki/IVDW`.
- **Relaxation tasks MUST set**:
  - `IBRION = 2` (CG) or `1` (quasi-Newton), and `NSW >= 100`.
  - `ISIF = 2` for ionic-only, `ISIF = 3` for full cell+ionic relaxation.
  - `EDIFFG` negative for force convergence (e.g., `EDIFFG = -0.01` eV/A).
- **Static fixed-cell slab/surface setups**: if the prompt says fixed cell or fixed cell shape, set `ISIF = 2` explicitly.
- **Band structure / DOS must be two-step**: SCF first (uniform k-mesh), then
  NSCF with `ICHARG = 11` (band) or `ICHARG = 11` + dense mesh (DOS).
- **Projected band / PDOS / DOS / magnetic-moment analysis tasks**: set
  `LORBIT = 11` unless the task explicitly requests another projection mode;
  this also applies to spin-polarized projected outputs.
- **Spin-polarized**: `ISPIN = 2`; set `MAGMOM` per atom for magnetic systems.
- **Finite-difference elastic tensor**: set `IBRION = 6`, `ISIF = 3`,
  `POTIM` to the requested displacement amplitude, and `NFREE = 2` unless a
  higher-order finite-difference stencil is explicitly requested.
- **SOC/heavy-element calculations**: set `LSORBIT = .TRUE.`, `ISPIN = 2`,
  `LNONCOLLINEAR = .TRUE.` (implicit), and `ISYM = 0`; set `LMAXMIX = 4`
  unless f-electrons require `LMAXMIX = 6`.
- **Hybrid DFT (HSE06)**: `LHFCALC = .TRUE.`, `HFSCREEN = 0.2`,
  `ALGO = Damped` or `All`, `TIME = 0.4`.
- **DFT+U**: `LDAU = .TRUE.`, `LDAUTYPE = 2` (Dudarev), `LDAUL`, `LDAUU`,
  `LDAUJ` arrays matching species order in POSCAR; set `LMAXMIX = 4` for
  d-electron systems and `LMAXMIX = 6` for f-electron systems.
- **Meta-GGA (SCAN/R2SCAN)**: `LASPH = .TRUE.` is required.
- **POTCAR must be resolved before submission.** VASP cannot run without
  POTCAR. Before submitting, use AskQuestion to ask the user where POTCAR
  is located. Options:
  - "VASP 镜像中已内置 POTCAR（如 `/opt/vasp/potcar/PBE/`）"
  - "Bohrium 节点上的某个目录（请填路径）"
  - "我没有 POTCAR"
  If the user has no POTCAR, do NOT submit — inform them POTCAR is
  license-restricted and cannot be auto-generated, then stop.
  If POTCAR is in the image or on a node path, write a `run.sh` that
  copies/concatenates POTCAR from that path before running VASP.
  State recommended PAW pseudopotentials (e.g., `Al`, `Si`, `O`,
  `Fe_pv`, `Ti_sv`) so they know what elements to prepare.

## K-point Rules

- Bulk SCF/relaxation: Gamma-centered Monkhorst-Pack. Density: ~30-40 A
  per reciprocal lattice vector (e.g., a=4 A -> k ~ 8-10 per direction).
- Slab/surface: dense in-plane, `1` in vacuum direction.
- Band structure: line-mode along high-symmetry path (use pymatgen
  `HighSymmKpath` or `scripts/generate_kpoints.py --mode line`).
- DOS: dense uniform mesh (>= 2x SCF mesh density). Use `ISMEAR = -5`.
- Molecular/isolated: Gamma-only (`1 1 1`).
- Convergence test: keep KPOINTS fixed when sweeping ENCUT, and vice versa.

## Bohrium Submission

| Item | Default Value |
|------|---------------|
| image | Use Bohrium `list_images` with keyword `vasp`. If none exists, do not submit. |
| machine | `c32_m128_cpu` |
| cmd | `OMP_NUM_THREADS=1 mpirun -np 16 vasp_std > log 2>&1` |

Notes:
- `-np` is typically half of CPU cores (32 virtual cores → 16 physical cores on Bohrium).
- Use `vasp_gam` for Gamma-only, `vasp_ncl` for SOC/noncollinear calculations.

Notes:
- Use `vasp_gam` for Gamma-only, `vasp_ncl` for SOC/noncollinear.
- POTCAR must be present in the run directory (user responsibility).

## Scripts

- `scripts/get_potcar.py --elements "Fe,O"` — recommend pseudopotentials, compute ENCUT from ENMAX
- `scripts/validate_incar.py -f INCAR -t relax` — check tag conflicts, missing required tags, ENCUT reasonableness
- `scripts/generate_kpoints.py` — generate KPOINTS from structure (auto mesh, line-mode, slab detection)

## References (read on demand)

- `references/incar_tags.md` — INCAR tag reference organized by calculation type
- `references/input_examples.md` — worked examples for common simulation types
