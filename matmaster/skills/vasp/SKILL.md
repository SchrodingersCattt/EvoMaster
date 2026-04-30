---
name: vasp
description: "Use this skill for any VASP-related task: running VASP examples, input file generation (INCAR, KPOINTS, POSCAR), submitting VASP jobs to Bohrium, and VASP workflow guidance. Covers SCF, relaxation, band structure, DOS, MD, hybrid DFT, SOC, magnetism, DFT+U, optical, NEB, and more."
skill_type: operator
---

# VASP Skill

VASP (Vienna Ab initio Simulation Package) is a plane-wave DFT code. This skill
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
- **Relaxation tasks MUST set**:
  - `IBRION = 2` (CG) or `1` (quasi-Newton), and `NSW >= 100`.
  - `ISIF = 2` for ionic-only, `ISIF = 3` for full cell+ionic relaxation.
  - `EDIFFG` negative for force convergence (e.g., `EDIFFG = -0.01` eV/A).
- **Band structure / DOS must be two-step**: SCF first (uniform k-mesh), then
  NSCF with `ICHARG = 11` (band) or `ICHARG = 11` + dense mesh (DOS).
- **Spin-polarized**: `ISPIN = 2`; set `MAGMOM` per atom for magnetic systems.
- **SOC**: `LSORBIT = .TRUE.` requires `ISPIN = 2` and `LNONCOLLINEAR = .TRUE.`
  (implicit).
- **Hybrid DFT (HSE06)**: `LHFCALC = .TRUE.`, `HFSCREEN = 0.2`,
  `ALGO = Damped` or `All`, `TIME = 0.4`.
- **DFT+U**: `LDAU = .TRUE.`, `LDAUTYPE = 2` (Dudarev), `LDAUL`, `LDAUU`,
  `LDAUJ` arrays matching species order in POSCAR.
- **Meta-GGA (SCAN/R2SCAN)**: `LASPH = .TRUE.` is required.
- **POTCAR must exist before submission.** VASP cannot run without POTCAR.
  Before submitting a Bohrium job, verify POTCAR is present in the input
  directory. If the user has not provided POTCAR, do NOT submit — instead
  inform them that POTCAR is required (license-restricted, cannot be
  auto-generated). State recommended PAW pseudopotentials (e.g.,
  `Al`, `Si`, `O`, `Fe_pv`, `Ti_sv`) so they know what to prepare.

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

No default image is provided. Use Bohrium's `list image` to find an available
VASP image; if none exists, do not submit the task.

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
