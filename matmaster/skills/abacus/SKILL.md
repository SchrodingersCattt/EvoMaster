---
name: abacus
description: "ABACUS first-principles calculation: input preparation, parameter configuration, and Bohrium HPC submission. Supports both PW (plane wave) and LCAO (linear combination of atomic orbitals) basis types. Tasks include SCF, band structure, DOS, geometry relaxation, cell relaxation, and MD."
skill_type: operator
---

# ABACUS Skill

ABACUS (Atomic-orbital Based Ab-initio Computation at UStc) is an open-source DFT code supporting both plane-wave (PW) and numerical atomic orbital (LCAO) basis sets. LCAO mode enables linear-scaling DFT for large systems.

## Bohrium Submission Config

| Item | Default Value |
|------|---------------|
| image | `registry.dp.tech/dptech/abacus:LTSv3.10.1` |
| machine | `c32_m128_cpu` (32 cores, 128 GB RAM) |
| cmd | `OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1` |

> **Important**: `-np` = **half the CPU core count** of the chosen machine (32 cores → 16 processes). This is ABACUS-specific; do not use the full core count.
> For GPU-accelerated runs: `machine="c8_m60_1 * NVIDIA 4090"` with `basis_type pw`.
> For different versions: `Bohrium(action="list_images", keyword="abacus")`.

## Input Preparation

ABACUS uses **three mandatory input files**: `INPUT`, `STRU`, `KPT`.

### Using render_input.py (recommended)

```bash
# Generate INPUT file
uv run python scripts/render_input.py --software abacus --task scf --output INPUT

# Validate
uv run python scripts/diagnose_input.py --software abacus --input INPUT
```

### Input File Descriptions

**INPUT** — computation parameters:
```
INPUT_PARAMETERS
calculation   scf
basis_type    lcao          # or pw
ecutwfc       100           # Ry (PW); for LCAO, this is auxiliary grid cutoff
scf_thr       1.0e-7
scf_nmax      100
smearing_method  gauss
smearing_sigma   0.01
```

**STRU** — atomic structure:
```
ATOMIC_SPECIES
Si  28.085  Si_ONCV_PBE-1.0.upf

NUMERICAL_ORBITAL
Si_gga_8au_100Ry_2s2p1d.orb

LATTICE_CONSTANT
1.8897259886    # Bohr

LATTICE_VECTORS
0.0  2.715  2.715
2.715  0.0  2.715
2.715  2.715  0.0

ATOMIC_POSITIONS
Direct
Si
0.0
2
0.00  0.00  0.00  0 0 0
0.25  0.25  0.25  0 0 0
```

**KPT** — k-point sampling:
```
K_POINTS
0
Gamma
4 4 4  0 0 0
```

### Ready-to-run input files

If the user provides complete INPUT + STRU + KPT files with pseudopotentials and orbitals, skip preparation and submit directly.

## Task Types

| Task | `calculation` value | Template | Description |
|------|---------------------|----------|-------------|
| scf | `scf` | `task_scf.INPUT` | Single-point energy |
| band | `nscf` | `task_band.INPUT` | Band structure (needs prior SCF charge density) |
| dos | `nscf` | `task_dos.INPUT` | Density of states |
| relax | `relax` | `task_relax.INPUT` | Atomic position relaxation |
| cell_relax | `cell-relax` | `task_cell_relax.INPUT` | Full cell + position relaxation |
| md | `md` | `task_md_nvt.INPUT` | NVT molecular dynamics |

## Required Files

- **INPUT**: computation parameters (generated or user-provided)
- **STRU**: atomic structure file; must reference pseudopotential and orbital files
- **KPT**: k-point specification
- **Pseudopotentials** (`.upf`): one per element
  - Download from [AIS Square ABACUS-APNS-PPORBs-v1](https://www.aissquare.com/datasets/detail?pageType=datasets&name=ABACUS-APNS-PPORBs-v1&id=326)
  - Fallback: [GitHub PP_ORB](https://github.com/deepmodeling/abacus-develop/tree/develop/tests/PP_ORB)
- **Orbital files** (`.orb`, LCAO only): numerical atomic orbital basis
  - Download from [ABACUS orbital repository](http://abacus.deepmodeling.com/orbitals/)

## Physical Checks

- **basis_type**: `lcao` for most tasks (efficient for medium-large systems); `pw` for benchmarks or GPU acceleration
- **ecutwfc**: for PW, typically 60-100 Ry; for LCAO, this controls auxiliary grid (50-100 Ry usually sufficient)
- **K-points**: consistent with cell size; denser for metals
- **scf_thr**: typically 1.0e-7 or tighter for production
- **Pseudopotential + orbital consistency**: PP and orbital files must match (same element, same exchange-correlation type)
- **LCAO orbital quality**: choose orbital radius and completeness appropriate for accuracy needs (e.g. `DZP` for production, `SZV` for testing)
- **MPI processes**: use half the core count for ABACUS (`-np 16` on 32-core machine)
- **Band structure**: requires prior SCF to produce charge density files; k-path should match crystal symmetry

## Submission Workflow

1. Prepare structure file (CIF/POSCAR for conversion, or write STRU directly)
2. Download pseudopotentials and orbital files for all elements
3. Generate INPUT: `render_input.py --software abacus --task scf --output INPUT`
4. Prepare KPT (Monkhorst-Pack or k-path for band structure)
5. Diagnose: `diagnose_input.py --software abacus --input INPUT`
6. Place all files in one directory (INPUT, STRU, KPT, .upf files, .orb files)
7. Submit: `Bohrium(action="submit", input_dir="<dir>", image="registry.dp.tech/dptech/abacus:LTSv3.10.1", cmd="OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1")`
8. Poll: `Bohrium(action="poll", job_id=<id>)`

## Reference

Official documentation: `site:abacus.deepmodeling.com`
