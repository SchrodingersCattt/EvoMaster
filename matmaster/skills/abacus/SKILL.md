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

### Complete INPUT Examples for Electronic Property Calculations

Electronic property calculations (band structure, DOS) require a **two-step workflow**: SCF to converge charge density, then NSCF to compute the property on a different k-grid.

#### Step 1: SCF INPUT (charge density output enabled)
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
out_chg 1
```
> **`out_chg 1` is mandatory** for any SCF that feeds a subsequent NSCF step. It writes charge density files (`SPIN1_CHG.cube`) to `OUT.ABACUS/`. Without it, the NSCF step has no charge density to read.

SCF KPT — use uniform Monkhorst-Pack mesh:
```
K_POINTS
0
Gamma
8 8 8 0 0 0
```

#### Step 2a: NSCF Band Structure INPUT
```
INPUT_PARAMETERS
calculation nscf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 300
init_chg file
out_band 1
nbands 40
symmetry 0
smearing_method gauss
smearing_sigma 0.01
```

**Required NSCF parameters** (the agent MUST include all of these):
| Parameter | Value | Purpose |
|-----------|-------|---------|
| `init_chg` | `file` | Read converged charge density from SCF `OUT.ABACUS/SPIN1_CHG.cube`. **Without this, NSCF re-runs SCF from scratch.** |
| `out_band` | `1` | Write band eigenvalues to `OUT.ABACUS/BANDS_1.dat` |
| `nbands` | integer | Number of bands to compute. Set to `total_electrons/2 + 20` (insulator) or `total_electrons/2 × 1.5` (metal). Must exceed occupied band count. |
| `symmetry` | `0` | **Mandatory for line-mode k-paths.** Symmetry reduction folds/reorders k-points and breaks the band path. |

Band structure KPT (line mode, example FCC: Γ→X→M→Γ):
```
K_POINTS
4
Line
0.000  0.000  0.000  40  // Gamma
0.500  0.000  0.000  40  // X
0.500  0.500  0.000  40  // M
0.000  0.000  0.000  1   // Gamma
```

#### Step 2b: NSCF Density of States INPUT
```
INPUT_PARAMETERS
calculation nscf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 300
init_chg file
out_dos 1
dos_edelta_ev 0.01
dos_sigma 0.07
dos_nche 100
nbands 40
symmetry 0
smearing_method gauss
smearing_sigma 0.01
```

**DOS-specific parameters**:
| Parameter | Value | Purpose |
|-----------|-------|---------|
| `out_dos` | `1` | Write DOS data to `OUT.ABACUS/DOS1_smearing.dat` |
| `dos_edelta_ev` | `0.01` | Energy grid spacing in eV (finer = higher resolution) |
| `dos_sigma` | `0.07` | Gaussian smearing width for DOS (eV) |
| `dos_nche` | `100` | Chebyshev expansion order for LCAO DOS (higher = more accurate) |

DOS KPT — use **dense uniform mesh** (NOT line-mode):
```
K_POINTS
0
Gamma
12 12 12 0 0 0
```

#### Two-Step Workflow: File Management on Bohrium

Since Bohrium runs jobs as a single command, use a shell script to chain SCF and NSCF:

```bash
#!/bin/bash
# Step 1: SCF (produces charge density)
cp INPUT_scf INPUT
cp KPT_scf KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus
# Step 2: NSCF (reads charge density, computes band/DOS)
cp INPUT_nscf INPUT
cp KPT_nscf KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus
```

**Input directory must contain**: `INPUT_scf`, `INPUT_nscf`, `KPT_scf`, `KPT_nscf`, `STRU`, `.upf`, `.orb`, and the shell script (`run.sh`).
**Submit**: `--cmd "bash run.sh > log 2>&1"`

> **Critical file dependency**: NSCF reads `OUT.ABACUS/SPIN1_CHG.cube` produced by SCF. Both steps must run in the same directory. Do NOT delete `OUT.ABACUS/` between steps.

### ABACUS Output Files Reference

After a successful run, results are in `OUT.ABACUS/`:

| File | Produced by | Contains |
|------|-------------|----------|
| `running_scf.log` | SCF | Total energy, Fermi energy, convergence, forces, stress |
| `running_nscf.log` | NSCF | Fermi energy, eigenvalue info |
| `SPIN1_CHG.cube` | SCF with `out_chg 1` | Charge density (cube format) |
| `BANDS_1.dat` | NSCF with `out_band 1` | Band eigenvalues along k-path |
| `DOS1_smearing.dat` | NSCF with `out_dos 1` | Density of states |
| `ElecStaticPot.cube` | `out_pot 2` | Electrostatic potential (work function) |
| `STRU_ION*_D` | Relax | Relaxed structure at each ionic step |

**Reading results from logs**: Key patterns to grep:
- Total energy: `!FINAL_ETOT_IS <energy> eV`
- Fermi energy: `EFERMI = <energy> eV`
- Convergence: `charge density convergence is achieved`
- Forces: lines after `TOTAL-FORCE (eV/Angstrom)` header
- Stress: lines after `TOTAL-STRESS (KBAR)` header

## STRU File Format (Detailed)

The STRU file has five sections, in order:

### ATOMIC_SPECIES
```
ATOMIC_SPECIES
<Label> <Mass> <PseudopotentialFile>
```
One line per species. `Label` is the species name used later in ATOMIC_POSITIONS. For ghost/empty atoms, create a separate species entry (e.g. `Fe_empty`) with the same PP and orbital files but treat it as a distinct `ntype`.

### NUMERICAL_ORBITAL (LCAO only)
```
NUMERICAL_ORBITAL
<OrbitalFile_for_species1>
<OrbitalFile_for_species2>
```
One `.orb` file per species, in the same order as ATOMIC_SPECIES. Required when `basis_type lcao`. Omit entirely for `basis_type pw`.

### LATTICE_CONSTANT and LATTICE_VECTORS
```
LATTICE_CONSTANT
1.8897259886  // 1 Angstrom in Bohr

LATTICE_VECTORS
a1x  a1y  a1z
a2x  a2y  a2z
a3x  a3y  a3z
```
Lattice vectors are in units of LATTICE_CONSTANT. Using `1.8897259886` (1 Angstrom in Bohr) means vectors are effectively in Angstrom.

### ATOMIC_POSITIONS
```
ATOMIC_POSITIONS
<CoordinateType>
<Label>
<InitialMagneticMoment>
<NumberOfAtoms>
x1 y1 z1  m mx my mz    // or: x1 y1 z1  0 0 0 (fixed) / 1 1 1 (free)
```
- `CoordinateType`: `Direct` (fractional), `Cartesian_angstrom`, `Cartesian_au`, `Cartesian`
- `m mx my mz`: mobility flags; `1 1 1` = free to relax, `0 0 0` = frozen. Can also use `m 1 1 1` prefix style.
- `InitialMagneticMoment`: in Bohr magnetons. Use `0.0` for non-magnetic, set per-species for spin-polarized (e.g. `2.0` for Fe).
- For **multiple species**, repeat the `<Label> / <Moment> / <NumAtoms> / <coordinates>` block for each species, in the same order as ATOMIC_SPECIES.

**Multi-species example** (Fe + Fe_empty ghost atoms):
```
ATOMIC_POSITIONS
Cartesian_angstrom
Fe
2.0
4
0.000  0.000  0.000  1 1 1
1.435  1.435  1.435  1 1 1
0.000  2.870  0.000  1 1 1
2.870  0.000  0.000  1 1 1
Fe_empty
0.0
2
0.000  0.000  4.300  0 0 0
1.435  1.435  5.735  0 0 0
```

## Ghost/Empty Atoms for BSSE Correction (LCAO)

When using LCAO basis (`basis_type lcao`), numerical atomic orbitals (NAO) are atom-centered. Removing an atom (vacancy) or being near a surface boundary removes the basis functions that were centered on that site, causing **basis set superposition error (BSSE)**.

**Fix**: place "ghost" atoms (also called "empty atoms") at those sites. A ghost atom contributes its basis functions (pseudopotential + orbital) but carries **zero valence charge** and **zero magnetic moment**:

1. In `ATOMIC_SPECIES`, add a new species with a distinct label (e.g. `Fe_empty`) referencing the **same** `.upf` and `.orb` files as the real element.
2. In `NUMERICAL_ORBITAL`, add the same `.orb` file for the ghost species.
3. In `INPUT`, set `ntype` to the total number of species (real + ghost).
4. In `ATOMIC_POSITIONS`, place ghost atoms at the desired sites with magnetic moment `0.0` and mobility flags `0 0 0` (frozen).
5. ABACUS treats ghost atoms identically to regular atoms except the charge is zeroed internally when the user designates them as empty.

**Vacancy BSSE example**: bcc Fe with one vacancy. Place `Fe_empty` at the vacancy site to restore the missing NAO basis completeness:
```
ATOMIC_SPECIES
Fe      55.845  Fe.upf
Fe_empty 55.845  Fe.upf

NUMERICAL_ORBITAL
Fe_gga_9au_100Ry_4s2p2d1f.orb
Fe_gga_9au_100Ry_4s2p2d1f.orb
```
INPUT must have `ntype 2` and typically `nspin 2` for magnetic Fe.

**Surface slab BSSE example**: place empty atoms on both sides of the slab in the vacuum region (~2.0 A from the outermost real atoms), arranged to match the surface periodicity.

## Electric Field and Dipole Correction

ABACUS supports electric fields and dipole corrections for slab/surface calculations:

### Dipole Correction Only (no finite field)
```
INPUT_PARAMETERS
efield_flag 1
dip_cor_flag 1
efield_dir 2
efield_pos_max 0.0
efield_pos_dec 0.1
efield_amp 0.0
```
- `efield_flag 1` + `dip_cor_flag 1` + `efield_amp 0.0` = pure dipole correction (no external field).
- `efield_dir`: 0=x, 1=y, 2=z. Set to the vacuum direction of your slab.
- `efield_pos_max`, `efield_pos_dec`: position and decay width (fractional coords) of the sawtooth correction potential in the vacuum region.

### Finite External Electric Field
```
INPUT_PARAMETERS
efield_flag 1
efield_dir 2
efield_amp 0.0019440124
efield_pos_max 0.95
efield_pos_dec 0.10
```
- `efield_amp`: field strength in a.u. (1 a.u. = 51.4 V/A). Typical small field: ~1e-3 a.u.
- Combine with `dip_cor_flag 1` to also correct the dipole artifact when applying a finite field.

### Gate Field

A complete gate-field INPUT should include **both** the standard SCF parameters and the gate/dipole/barrier block. Write them together in one file (do not split into separate steps):

```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 200
smearing_method gauss
smearing_sigma 0.01
mixing_type broyden
mixing_beta 0.4
out_pot 2
efield_flag 1
dip_cor_flag 1
efield_dir 2
efield_pos_max 0.95
efield_pos_dec 0.10
efield_amp 0.0
gate_flag 1
zgate 0.7
nelec 8
block 1
block_down 0.45
block_up 0.55
block_height 0.1
```
- Include `out_pot 2` to output the electrostatic potential — essential for analyzing the gate effect on the potential profile.
- `mixing_type broyden` with `mixing_beta 0.4`: recommended for slab/surface systems with vacuum (improves SCF convergence).
- `gate_flag 1`: place compensating charge sheet at fractional z = `zgate` (in vacuum).
- `nelec`: **Always count electrons from your actual system first.** Set to the system's neutral electron count by default (e.g. 8 for H₂O). Only change from neutral to simulate a charged system (e.g. 9 = adding one electron). **Never copy example values blindly — always derive from your structure.**
- `block 1` + `block_down/block_up/block_height`: potential barrier in vacuum to prevent electron spillage.
- `zgate`, `block_down`, `block_up`: fractional z-coordinates — **adjust based on your slab geometry**. Place gate and barrier in the vacuum region away from atoms. Do not copy example values without verifying they suit your system's atom positions.

## Electrostatic Potential Output

To analyze work function, surface dipole, or electrostatic potential profile:
```
INPUT_PARAMETERS
out_pot 2
```
- `out_pot 0`: no output (default).
- `out_pot 1`: write the local ionic potential.
- `out_pot 2`: write the total (Hartree + local) electrostatic potential to `OUT.ABACUS/ElecStaticPot.cube`. This is needed for work function calculations (average along the slab normal, compare vacuum level to Fermi energy).
- Typically combined with `basis_type pw` for accurate potential, and sufficient `nbands` for metals.

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

For nanoribbons, nanotubes, or 2D materials:

**KPT for 1D system (nanoribbon along y-axis)**:
```
K_POINTS
2
Line
0.000  0.000  0.000  100  // Gamma
0.000  0.500  0.000  1    // Y (endpoint)
```
Use `Line` mode with dense interpolation (80-120 points per segment). The k-path follows the periodic direction only. For a nanoribbon periodic along y: Gamma (0,0,0) → Y (0,0.5,0). For periodic along x: Gamma → X (0.5,0,0). For periodic along z: Gamma → Z (0,0,0.5).

**KPT for 2D materials** (graphene, MoS₂, hexagonal BZ):
```
K_POINTS
4
Line
0.000  0.000  0.000  40  // Gamma
0.500  0.000  0.000  40  // M
0.333  0.333  0.000  40  // K
0.000  0.000  0.000  1   // Gamma (endpoint)
```

**Band structure workflow** (two-step — see "Complete INPUT Examples for Electronic Property Calculations" above for full INPUT files):
1. **SCF step**: `calculation scf` with uniform KPT mesh. **Must include `out_chg 1`** to save charge density.
2. **NSCF step**: `calculation nscf` with **all of**: `init_chg file`, `out_band 1`, `nbands <N>`, `symmetry 0`. Use line-mode KPT along the high-symmetry path. Keep all other settings from SCF (basis_type, ecutwfc, nspin, efield, smearing, etc.).

## Multi-File Generation for Comparative Studies

When generating input files for comparative calculations (surface energy, vacancy formation, equation of state), **always create ALL requested files**. Common patterns:

### Surface Energy (bulk + slab)
Generate separate INPUT and KPT for each system:
- `INPUT_bulk_relax`: `calculation cell-relax` for equilibrium bulk energy.
- `INPUT_slab5`, `INPUT_slab7`: `calculation relax` for different slab thicknesses.
- `KPT_bulk`: dense 3D grid (e.g. `20 20 20 0 0 0`).
- `KPT_slab`: dense in-plane, sparse normal (e.g. `20 20 1 0 0 0`).
- Key: `basis_type`, `ecutwfc`, `smearing_method`, `smearing_sigma` should be consistent across all calculations for cancellation of systematic errors.

### Vacancy Formation Energy
Generate INPUT for each component:
- `INPUT_bulk`: `calculation cell-relax` or `calculation scf` for reference bulk energy.
- `INPUT_slab_clean`: `calculation scf` for pristine surface.
- `INPUT_slab_vac`: `calculation scf` for surface with vacancy.
- `KPT_gamma`: Gamma-point only (`1 1 1 0 0 0`) for quick benchmarks, or denser for production.
- Use consistent `basis_type pw`, `ecutwfc`, `smearing_method`, `smearing_sigma` across all.

### KPT for Slab Calculations
- In-plane directions: use dense mesh matching periodicity. **Minimum `12 12` for metals**; `20 20` for accurate surface energy.
- Vacuum direction: **always `1`** (single k-point; no periodicity). This is critical — never use more than 1 k-point in the vacuum direction.
- Example KPT file: `20 20 1 0 0 0` for an fcc(100) slab.
- For `kspacing` mode: ABACUS accepts **three separate values** (`kspacing kx ky kz`). For slabs, use normal kspacing in periodic directions and a **large value (≥ 0.5)** in the vacuum direction:
  ```
  kspacing 0.10 0.10 1.00    # slab with vacuum along z
  ```
  For uniform bulk: `kspacing 0.10` (single value applies to all three directions equally).
- For bulk vacancy supercells: use uniform kspacing, e.g. `kspacing 0.10` or equivalent Monkhorst-Pack grid matching the supercell size.

## Output Control Parameters

These parameters control what ABACUS writes to `OUT.ABACUS/`. Include them in INPUT as needed.

| Parameter | Values | Default | Purpose |
|-----------|--------|---------|---------|
| `out_chg` | `0`/`1` | `0` | Write charge density to `SPIN1_CHG.cube`. **Required for SCF→NSCF workflows.** |
| `out_band` | `0`/`1` | `0` | Write band eigenvalues to `BANDS_1.dat`. For NSCF band structure. |
| `out_dos` | `0`/`1` | `0` | Write DOS to `DOS1_smearing.dat`. For NSCF DOS. |
| `out_pot` | `0`/`1`/`2` | `0` | Write electrostatic potential. `2` = total Hartree+local → `ElecStaticPot.cube`. |
| `out_stru` | `0`/`1` | `0` | Write relaxed structure files (`STRU_ION*_D`). For relax/cell-relax. |
| `out_wfc_lcao` | `0`/`1` | `0` | Write LCAO wavefunction coefficients. For post-processing (PyATB, Wannier). |
| `cal_force` | `0`/`1` | `0` | Calculate and print atomic forces. Set `1` for relax or force analysis. |
| `cal_stress` | `0`/`1` | `0` | Calculate and print stress tensor. Set `1` for cell-relax or EOS. |
| `init_chg` | `atomic`/`file` | `atomic` | Charge density init. `file` = read from prior SCF. **Required for NSCF.** |
| `nbands` | integer | auto | Number of bands. Must be set explicitly for NSCF (> occupied bands). |
| `symmetry` | `0`/`1` | `1` | `0` = disable symmetry. **Mandatory for NSCF with line-mode k-paths.** |

> **Common mistake**: forgetting `out_chg 1` in the SCF step, then the NSCF step with `init_chg file` fails silently or recomputes SCF.
> **Common mistake**: leaving `symmetry 1` (default) in NSCF band structure — the k-path gets folded/reordered, producing wrong band plots.

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
