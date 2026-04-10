---
name: abacus
description: "ABACUS first-principles calculation: input preparation, parameter configuration, and Bohrium HPC submission. Supports both PW (plane wave) and LCAO (linear combination of atomic orbitals) basis types. Tasks include SCF, band structure, DOS, geometry relaxation, cell relaxation, MD, electric field, dipole correction, BSSE ghost-atom correction, and electrostatic potential analysis."
skill_type: operator
---

# ABACUS Skill

ABACUS (Atomic-orbital Based Ab-initio Computation at UStc) is an open-source DFT code supporting both plane-wave (PW) and numerical atomic orbital (LCAO) basis sets. LCAO mode enables linear-scaling DFT for large systems.

**Action rule**: when the user asks you to generate ABACUS input files (INPUT, STRU, KPT), **always use the Write tool** to create the files in the working directory. Read any provided STRU files first, then Write all requested output files. Do not stop after only reading files.

**Efficiency rule**: Be concise — write input files directly with minimal preamble. Do NOT explain each parameter line-by-line or repeat file contents in prose. After writing all files, a brief summary (2-4 sentences) of key settings is sufficient. Lengthy explanations waste tokens without adding value.

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

**INPUT** — computation parameters (use single space between keyword and value):
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
```
> **Formatting rule**: Write each parameter as `keyword value` with a SINGLE SPACE separator. Do NOT pad with extra spaces or tabs for alignment. ABACUS parses both formats identically, but single-space is the canonical form.

**STRU** — atomic structure (see detailed STRU format section below):
```
ATOMIC_SPECIES
Si  28.085  Si.upf

NUMERICAL_ORBITAL
Si_gga_7au_100Ry_2s2p1d.orb

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
4 4 4 0 0 0
```
> KPT files MUST start with `K_POINTS` as the first line. Use single spaces in the k-mesh line (no double spaces).

### Ready-to-run input files

If the user provides complete INPUT + STRU + KPT files with pseudopotentials and orbitals, skip preparation and submit directly.

## Task Types

| Task | `calculation` value (write exactly) | Template | Description |
|------|---------------------|----------|-------------|
| scf | `calculation scf` | `task_scf.INPUT` | Single-point energy |
| band | `calculation nscf` | `task_band.INPUT` | Band structure (needs prior SCF charge density) |
| dos | `calculation nscf` | `task_dos.INPUT` | Density of states |
| relax | `calculation relax` | `task_relax.INPUT` | Atomic position relaxation |
| cell_relax | `calculation cell-relax` | `task_cell_relax.INPUT` | Full cell + position relaxation |
| md | `calculation md` | `task_md_nvt.INPUT` | NVT molecular dynamics |

> When generating INPUT files, write the calculation keyword exactly as shown: `calculation scf`, `calculation cell-relax`, etc. with single space. Include `efield_flag 1` and `dip_cor_flag 1` when dipole correction is needed.

### Electronic Property Calculations (Band Structure / DOS)

Require a **two-step workflow**: SCF → NSCF. Full INPUT examples, KPT templates, and Bohrium file management in **`references/input_examples.md`**.

**SCF step must include** `out_chg 1` (writes charge density for NSCF to read).

**NSCF must include ALL of**: `init_chg file`, `symmetry 0`, explicit `nbands`, and either `out_band 1` (band) or `out_dos 1` + `dos_edelta_ev` + `dos_sigma` + `dos_nche` (DOS).

**Two-step Bohrium submission**: write `run.sh` that copies `INPUT_scf`→`INPUT`, runs ABACUS, then copies `INPUT_nscf`→`INPUT`, runs again. Submit with `--cmd "bash run.sh > log 2>&1"`.

**Output files** and **grep patterns** for extracting results: see **`references/output_params.md`**.

## STRU File Format

The STRU file has five sections (in order): `ATOMIC_SPECIES`, `NUMERICAL_ORBITAL` (LCAO only), `LATTICE_CONSTANT`, `LATTICE_VECTORS`, `ATOMIC_POSITIONS`. See **`references/stru_format.md`** for detailed format, multi-species examples, and ghost/empty atom setup for BSSE correction.

Key reminders:
- `LATTICE_CONSTANT 1.8897259886` (1 A in Bohr) → vectors effectively in Angstrom
- `ATOMIC_POSITIONS`: coordinate type (`Direct`/`Cartesian_angstrom`), then per-species: label → magnetic moment → atom count → coordinates with mobility flags (`1 1 1` = free, `0 0 0` = frozen)
- Multiple species: repeat label/moment/count/coords block, **same order as ATOMIC_SPECIES**
- **BSSE ghost atoms** (LCAO vacancies/surfaces): same `.upf`/`.orb`, zero moment, frozen. Set `ntype` = real + ghost count.

## Electric Field, Dipole Correction, and Electrostatic Potential

Consult **`references/electric_field.md`** for complete INPUT examples (dipole correction, finite field, gate field, electrostatic potential).

Key rules:
- **Dipole correction**: `efield_flag 1` + `dip_cor_flag 1` + `efield_amp 0.0`. Set `efield_dir` to vacuum direction.
- **Gate field**: always include `out_pot 2` for potential analysis. **Never copy `nelec`/`zgate`/`block_*` values blindly** — derive from your system's geometry and electron count.
- **Work function**: `out_pot 2` → `ElecStaticPot.cube`. Average along slab normal; compare vacuum level to Fermi energy.

## Spin-Polarized Calculations

For magnetic systems (transition metals, etc.):
```
INPUT_PARAMETERS
nspin 2
```
- Set initial magnetic moments per species in the STRU file's ATOMIC_POSITIONS block (e.g. `2.0` for Fe, `0.0` for non-magnetic species).
- For magnetic metals, **always** set these three mixing parameters together (they are NOT optional for magnetic systems):
  ```
  mixing_beta 0.1
  mixing_ndim 20
  mixing_gg0 1.5
  ```
  `mixing_beta 0.1`–`0.4` prevents charge oscillation; `mixing_ndim 20` stores more history; `mixing_gg0 1.5` enhances long-wavelength mixing critical for magnetic metals. Omitting these risks SCF non-convergence.
- `smearing_method gauss` or `smearing_method gaussian` (both accepted) with `smearing_sigma 0.01` is typical for metals.

## Band Structure for 1D/2D Systems

For nanoribbons, nanotubes, or 2D materials, see KPT examples in **`references/input_examples.md`**.
- 1D: k-path along periodic direction only (e.g., Gamma→Y for y-periodic nanoribbon), 80-120 pts/segment.
- 2D hexagonal: Gamma→M→K→Gamma path.
- Two-step workflow: SCF (`out_chg 1`) → NSCF (`init_chg file`, `out_band 1`, `nbands`, `symmetry 0`).

## Multi-File Generation for Comparative Studies

For surface energy, vacancy formation, EOS, etc.: **always create ALL requested files** with consistent settings across systems. Consult **`references/input_examples.md`** for templates (surface energy, vacancy, slab KPT with `kspacing`).

Key slab KPT rule: **always use `1` in the vacuum direction** (e.g. `20 20 1 0 0 0`). For `kspacing` mode: `kspacing 0.10 0.10 1.00`.

## Output Control Parameters

Consult **`references/output_params.md`** for the full parameter table, output file list, and grep patterns. Key reminders:
- **SCF→NSCF**: SCF must have `out_chg 1`; NSCF must have `init_chg file`, `symmetry 0`, explicit `nbands`
- **Band structure**: `out_band 1` in NSCF; **DOS**: `out_dos 1` in NSCF
- **Work function**: `out_pot 2` for electrostatic potential
- **Relax/cell-relax**: `cal_force 1`, `cal_stress 1`

## Per-Task Parameter Checklist

Before writing INPUT, verify these parameters are present. Missing any → ABACUS may run incorrectly or produce no useful output.

**Every task**: `ntype` (= species count in STRU), `pseudo_dir ./`, `orbital_dir ./` (LCAO only), `ecutwfc`, `basis_type`, `scf_thr 1.0e-7`, `scf_nmax 100`

| Task | Additional REQUIRED parameters |
|------|-------------------------------|
| SCF (metals) | `smearing_method gauss`, `smearing_sigma 0.01` |
| Relax | `cal_force 1`, `force_thr_ev 0.01` |
| Cell-relax | `cal_force 1`, `cal_stress 1`, `stress_thr 5.0` |
| Band (NSCF) | `init_chg file`, `symmetry 0`, `nbands`, `out_band 1` + line-mode KPT |
| DOS (NSCF) | `init_chg file`, `symmetry 0`, `nbands`, `out_dos 1`, `dos_edelta_ev`, `dos_sigma`, `dos_nche` + dense KPT |
| Electrostatic pot | `out_pot 2`; for slabs add `efield_flag 1`, `dip_cor_flag 1`, `efield_amp 0.0` |
| MD | `md_type 1` (NVT), `md_nstep`, `md_dt`, `md_tfirst` |

**Common mistakes**: (1) `ntype` not matching species count in STRU → crash; (2) missing `pseudo_dir`/`orbital_dir` → files not found; (3) LCAO without `NUMERICAL_ORBITAL` section in STRU → crash; (4) `out_chg 1` forgotten in SCF before NSCF → NSCF recomputes from scratch; (5) `out_pot 2` omitted for work function task → no `ElecStaticPot.cube` output.

## Required Files

- **INPUT**: computation parameters (generated or user-provided)
- **STRU**: atomic structure file; must reference pseudopotential and orbital files
- **KPT**: k-point specification
- **Pseudopotentials** (`.upf`) and **Orbital files** (`.orb`, LCAO only): one per element.
  **Download source: AIS Square (mandatory).** Do NOT use GitHub or any other source.
  ```bash
  wget -q "https://store.aissquare.com/datasets/dc875646-a526-41f1-a180-d54b218fc80a/ABACUS-APNS-PPORBs-v1.zip" && unzip -qo ABACUS-APNS-PPORBs-v1.zip
  # The zip extracts to three directories:
  #   apns-pseudopotentials-v1/<Element>.upf
  #   apns-orbitals-efficiency-v1/<Element>_gga_*au_100Ry_*.orb  (efficiency set)
  #   apns-orbitals-precision-v1/<Element>_gga_*au_100Ry_*.orb   (precision set)
  # Copy needed element files into the working directory, e.g. for Si:
  cp apns-pseudopotentials-v1/Si.upf .
  cp apns-orbitals-efficiency-v1/Si_gga_7au_100Ry_2s2p1d.orb .
  ```
  This zip contains matched `.upf` + `.orb` pairs for all elements. If the download fails, retry with `wget --retry-connrefused --tries=3`.
  Note: pseudopotential filename is `<Element>.upf` (e.g. `Si.upf`), not `Si_ONCV_PBE-1.0.upf`.

## Physical Checks

- **basis_type**: `lcao` for most tasks (efficient for medium-large systems); `pw` for benchmarks or GPU acceleration
- **ecutwfc**: for PW, typically 60-100 Ry; for LCAO, this controls auxiliary grid (50-100 Ry usually sufficient)
- **K-points**: consistent with cell size; denser for metals
- **scf_thr**: typically 1.0e-7 or tighter for production
- **Pseudopotential + orbital consistency**: PP and orbital files must match (same element, same exchange-correlation type)
- **LCAO orbital quality**: choose orbital radius and completeness appropriate for accuracy needs (e.g. `DZP` for production, `SZV` for testing)
- **MPI processes**: use half the core count for ABACUS (`-np 16` on 32-core machine)
- **Band structure**: requires prior SCF to produce charge density files; k-path should match crystal symmetry
- **Slab calculations**: always use `1` in the vacuum k-direction; include dipole correction for asymmetric slabs
- **BSSE (LCAO)**: consider ghost atoms at vacancies or surface boundaries to reduce basis set superposition error

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
