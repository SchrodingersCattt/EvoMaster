# ABACUS Advanced Workflows & Automation Tools

## Workflow Automation Scripts

The `input-manual-helper` skill provides powerful automation scripts for ABACUS. **Use these instead of writing INPUT files manually for complex workflows.**

### render_abacus_workflow.py — Multi-Step Workflow Generator

Generates complete workspace (INPUT, STRU, KPT, run.sh) for complex workflows in one call:

```bash
# Band structure (SCF → NSCF, two-step)
uv run python ${INPUT_MANUAL_HELPER}/scripts/render_abacus_workflow.py \
  --workflow scf_band --output-dir ./band/ --stru existing.STRU

# DOS calculation (SCF → NSCF dense k-mesh)
uv run python ${INPUT_MANUAL_HELPER}/scripts/render_abacus_workflow.py \
  --workflow scf_dos --output-dir ./dos/ --stru existing.STRU

# Cell relaxation
uv run python ${INPUT_MANUAL_HELPER}/scripts/render_abacus_workflow.py \
  --workflow cell_relax --output-dir ./cellopt/ --stru existing.STRU --param ntype=3

# Vacancy / BSSE (auto uses kspacing)
uv run python ${INPUT_MANUAL_HELPER}/scripts/render_abacus_workflow.py \
  --workflow vacancy --output-dir ./vac/ --stru supercell.STRU --param nspin=2

# Work function / slab with dipole correction
uv run python ${INPUT_MANUAL_HELPER}/scripts/render_abacus_workflow.py \
  --workflow workfunction --output-dir ./wf/ --stru slab.STRU

# DFT+U
uv run python ${INPUT_MANUAL_HELPER}/scripts/render_abacus_workflow.py \
  --workflow dftu --output-dir ./dftu/ --stru existing.STRU --param nspin=2

# Molecular dynamics
uv run python ${INPUT_MANUAL_HELPER}/scripts/render_abacus_workflow.py \
  --workflow md --output-dir ./md/ --stru existing.STRU

# Spin-polarized SCF
uv run python ${INPUT_MANUAL_HELPER}/scripts/render_abacus_workflow.py \
  --workflow magnetic --output-dir ./mag/ --stru existing.STRU
```

**`--stru <path>`** auto-detects `ntype` from the STRU file. **`--param KEY=VALUE`** overrides any parameter (repeatable).

Supported workflows: `scf_band`, `scf_dos`, `scf`, `relax`, `cell_relax`, `vacancy`/`defect`/`bsse`, `workfunction`, `dftu`, `md`, `magnetic`.

### preflight_abacus.py — Comprehensive Pre-Submission Validator

Run this on **every** ABACUS workspace before submitting. Catches ALL common errors in one call:

```bash
# Validate (report only)
uv run python ${INPUT_MANUAL_HELPER}/scripts/preflight_abacus.py --dir ./workspace/

# Validate + auto-fix
uv run python ${INPUT_MANUAL_HELPER}/scripts/preflight_abacus.py --dir ./workspace/ --fix

# JSON output (for parsing)
uv run python ${INPUT_MANUAL_HELPER}/scripts/preflight_abacus.py --dir ./workspace/ --format json
```

Checks include:
- INPUT parameter validation (ecutwfc, calculation type, mixing, etc.)
- STRU format and species count vs `ntype`
- KPT existence, `stru_file`/`kpoint_file` cross-references
- Task-specific mandatory params (relax→cal_force, cell-relax→cal_stress, etc.)
- Slab detection → dipole correction check
- Two-step workflow (SCF+NSCF) file-pair validation
- DFT+U parameter completeness

**`--fix` generates corrected INPUT files** when fixable errors are found.

### workspace_review.py — Full Review + Grade

```bash
uv run python ${INPUT_MANUAL_HELPER}/scripts/workspace_review.py --dir ./workspace/ --software abacus
```

Combines preflight + best-practice evaluation in one call.

---

## Equation of State (EOS) / Birch-Murnaghan Workflow

Generate multiple INPUT+STRU pairs at different volumes for EOS fitting:

### Strategy
1. Start with an optimized (relaxed) structure.
2. Scale lattice vectors by factors: 0.96, 0.98, 1.00, 1.02, 1.04 (5–7 points).
3. For each volume: create a separate directory with INPUT + STRU + KPT.
4. Run SCF at each volume point.
5. Collect energies → fit Birch-Murnaghan equation.

### Step-by-Step

```bash
# 1. Generate base workspace (cell-relax to get optimized structure)
uv run python ${INPUT_MANUAL_HELPER}/scripts/render_abacus_workflow.py \
  --workflow cell_relax --output-dir ./eos_base/ --stru original.STRU

# 2. For each scale factor, create a scaled STRU:
#    - Read the LATTICE_VECTORS from relaxed STRU
#    - Multiply all vector components by scale_factor^(1/3)
#    - Write new STRU to eos_0.96/, eos_0.98/, etc.

# 3. In each directory, use the SAME INPUT (SCF only, no relax):
#    - calculation  scf
#    - All other params consistent across all points
#    - Use kspacing for automatic k-mesh adaptation

# 4. Submit all jobs, collect total energies from running_scf.log
#    grep "!FINAL_ETOT_IS" OUT.ABACUS/running_scf.log
```

### Critical EOS Rules
- **Consistent parameters**: ALL volume points must use identical `ecutwfc`, `smearing_method`, `smearing_sigma`, `scf_thr`, `basis_type`.
- **Use `kspacing`** (not fixed KPT mesh) so k-point density adapts to cell volume.
- **5–7 volume points** around equilibrium: too few → poor fit; too many → wasted computation.
- Scale range: typically ±4–6% of equilibrium volume.

---

## DFT+U Complete Example

For transition-metal oxides with strongly correlated d/f electrons:

### INPUT
```
INPUT_PARAMETERS
calculation             scf
basis_type              lcao
ecutwfc                 100
scf_thr                 1.0e-7
scf_nmax                200
smearing_method         gauss
smearing_sigma          0.01
nspin                   2
mixing_beta             0.1
mixing_ndim             20
mixing_gg0              1.5
lda_plus_u              1
hubbard_u               5.0 0.0
orbital_corr            2 -1
```

**Parameter explanation:**
- `hubbard_u`: One value per species (same order as ATOMIC_SPECIES in STRU). Set U for d/f species, 0.0 for others.
- `orbital_corr`: Angular momentum to apply U. `2` = d orbitals, `3` = f orbitals, `-1` = skip.
- `nspin 2`: DFT+U almost always requires spin polarization.
- Tight mixing (`mixing_beta 0.1`) for convergence.

### Example: FeO (Fe d-electrons, U=5.0 eV)
```
ATOMIC_SPECIES
Fe 55.845 Fe.upf
O  15.999 O.upf

NUMERICAL_ORBITAL
Fe_gga_9au_100Ry_4s2p2d1f.orb
O_gga_7au_100Ry_2s2p1d.orb
```

INPUT must have:
```
ntype         2
hubbard_u     5.0 0.0     # Fe=5.0, O=0.0
orbital_corr  2 -1        # Fe=d(2), O=skip(-1)
```

---

## Spin-Orbit Coupling (SOC) / Noncollinear

### INPUT additions for SOC
```
noncolin                1
lspinorb                1
nspin                   4
```

**Critical SOC rules:**
- `nspin 4` (not 2) when `noncolin 1`.
- SOC doubles the number of bands (each state splits into spin-up/down).
- Set `nbands` accordingly for NSCF: `≥ total_electrons + 20`.
- Heavier elements (5d, 4f) have stronger SOC — essential for topological materials, heavy-metal compounds.

---

## Molecular Dynamics (MD) — Advanced Parameters

### NVT with Nosé-Hoover
```
calculation             md
md_type                 nhc
md_nstep                5000
md_dt                   1.0
md_tfirst               300
md_tfreq                0.04
md_dumpfreq             10
md_restartfreq          100
cal_force               1
```

### NPT (Parrinello-Rahman barostat)
```
calculation             md
md_type                 npt
md_nstep                5000
md_dt                   1.0
md_tfirst               300
md_tfreq                0.04
md_pfirst               0.0
md_pfreq                0.02
md_dumpfreq             10
md_restartfreq          100
cal_force               1
cal_stress              1
```

**NPT requires both `cal_force 1` AND `cal_stress 1`** (stress needed for pressure coupling).

### Heating / temperature ramp
```
md_tfirst               300
md_tlast                1000
```
Linearly ramps from `md_tfirst` to `md_tlast` over `md_nstep` steps.

---

## Multi-Step Combined Workflow: Band + DOS

When a task asks for BOTH band structure AND DOS from a single material:

### File layout
```
workspace/
  INPUT_scf          # SCF with out_chg 1
  INPUT_band         # NSCF band: init_chg file, out_band 1, symmetry 0
  INPUT_dos          # NSCF DOS: init_chg file, out_dos 1, symmetry 0
  KPT_scf            # Uniform mesh (8 8 8)
  KPT_band           # Line-mode high-symmetry path
  KPT_dos            # Dense uniform mesh (12 12 12)
  STRU               # Shared structure
  *.upf, *.orb       # PP and orbital files
  run.sh             # Three-step chain
```

### run.sh
```bash
#!/bin/bash
set -e
# Step 1: SCF
cp INPUT_scf INPUT && cp KPT_scf KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_scf 2>&1
# Step 2: Band NSCF
cp INPUT_band INPUT && cp KPT_band KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_band 2>&1
# Step 3: DOS NSCF
cp INPUT_dos INPUT && cp KPT_dos KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_dos 2>&1
```

Submit: `--cmd "bash run.sh > log 2>&1"`

**All three INPUTs share**: `basis_type`, `ecutwfc`, `smearing_method`, `smearing_sigma`, `scf_thr`, `ntype`. Each references its own KPT via `kpoint_file`.

---

## Surface Energy Workflow (Bulk + Multiple Slabs)

### Directory layout
```
surface_study/
  bulk_relax/    INPUT, STRU, KPT  → cell-relax of bulk
  slab_5L/       INPUT, STRU, KPT  → relax of 5-layer slab
  slab_7L/       INPUT, STRU, KPT  → relax of 7-layer slab
```

### Rules (from input_examples.md — reinforced here)
1. ALL INPUTs share: `basis_type`, `ecutwfc`, `smearing_method`, `smearing_sigma`, `scf_thr`.
2. Bulk: `calculation cell-relax`, `cal_force 1`, `cal_stress 1`, dense 3D KPT (`kspacing 0.10`).
3. Slabs: `calculation relax`, `cal_force 1`, slab KPT with 1 in vacuum direction (`kspacing 0.10 0.10 1.00`).
4. **Every INPUT must have** `stru_file <name>` and `kpoint_file <name>` pointing to actual files.
5. Run `preflight_abacus.py --dir <each_dir>` before submission.

### Surface energy formula
E_surf = (E_slab − N × E_bulk) / (2 × A)

Where N = number of bulk formula units in slab, A = surface area, factor 2 for two surfaces.

---

## Quick Decision Map: Which Automation Tool?

| Scenario | Tool |
|----------|------|
| Single-task INPUT (SCF, relax) | Write manually using templates in `input_examples.md` |
| Multi-step workflow (band, DOS, EOS) | `render_abacus_workflow.py` |
| Validate before submission | `preflight_abacus.py` (ALWAYS run this) |
| Full review + grade | `workspace_review.py` |
| Debug failing INPUT | `diagnose_input.py --software abacus --input INPUT` |

**Best practice: For any complex ABACUS task, use `render_abacus_workflow.py` to generate the initial workspace, then customize, then validate with `preflight_abacus.py`.**
