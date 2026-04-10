# ABACUS Complete INPUT Examples

## Two-Step Electronic Property Workflow

Electronic property calculations (band structure, DOS) require: SCF → NSCF.

### Step 1: SCF INPUT (charge density output)
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
> `out_chg 1` is **mandatory** — writes `SPIN1_CHG.cube` to `OUT.ABACUS/`. Without it, NSCF has no charge density to read.

SCF KPT (uniform Monkhorst-Pack):
```
K_POINTS
0
Gamma
8 8 8 0 0 0
```

### Step 2a: NSCF Band Structure INPUT
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

**Required NSCF parameters** (ALL must be included):

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `init_chg` | `file` | Read converged charge from SCF. **Without this, NSCF re-runs SCF from scratch.** |
| `out_band` | `1` | Write band eigenvalues to `BANDS_1.dat` |
| `nbands` | integer | Bands to compute: `total_electrons/2 + 20` (insulator) or `×1.5` (metal) |
| `symmetry` | `0` | **Mandatory for line-mode k-paths.** Symmetry folds/reorders k-points. |

Band structure KPT (line mode, example FCC):
```
K_POINTS
4
Line
0.000  0.000  0.000  40  // Gamma
0.500  0.000  0.000  40  // X
0.500  0.500  0.000  40  // M
0.000  0.000  0.000  1   // Gamma
```

### Step 2b: NSCF DOS INPUT
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

DOS-specific parameters:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `out_dos` | `1` | Write DOS to `DOS1_smearing.dat` |
| `dos_edelta_ev` | `0.01` | Energy grid spacing (eV) |
| `dos_sigma` | `0.07` | Gaussian smearing width (eV) |
| `dos_nche` | `100` | Chebyshev expansion order for LCAO DOS |

DOS KPT — **dense uniform mesh** (NOT line-mode):
```
K_POINTS
0
Gamma
12 12 12 0 0 0
```

### Two-Step File Management on Bohrium

Chain SCF and NSCF in a shell script:
```bash
#!/bin/bash
cp INPUT_scf INPUT
cp KPT_scf KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus
cp INPUT_nscf INPUT
cp KPT_nscf KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus
```

**Input directory must contain**: `INPUT_scf`, `INPUT_nscf`, `KPT_scf`, `KPT_nscf`, `STRU`, `.upf`, `.orb`, `run.sh`.
**Submit**: `--cmd "bash run.sh > log 2>&1"`
> **Critical**: NSCF reads `OUT.ABACUS/SPIN1_CHG.cube` from SCF. Both steps must share the same directory.

---

## Band Structure for 1D/2D Systems

**1D nanoribbon** (periodic along y):
```
K_POINTS
2
Line
0.000  0.000  0.000  100  // Gamma
0.000  0.500  0.000  1    // Y
```
Dense interpolation (80-120 pts/segment). K-path follows periodic direction only.

**2D materials** (graphene, MoS2, hexagonal BZ):
```
K_POINTS
4
Line
0.000  0.000  0.000  40  // Gamma
0.500  0.000  0.000  40  // M
0.333  0.333  0.000  40  // K
0.000  0.000  0.000  1   // Gamma
```

---

## Multi-File Generation for Comparative Studies

### Surface Energy (bulk + slab)
- `INPUT_bulk_relax`: `calculation cell-relax` — equilibrium bulk energy
- `INPUT_slab5`, `INPUT_slab7`: `calculation relax` — different slab thicknesses
- `KPT_bulk`: dense 3D (e.g. `20 20 20 0 0 0`)
- `KPT_slab`: dense in-plane, 1 in vacuum (e.g. `20 20 1 0 0 0`)
- Keep `basis_type`, `ecutwfc`, `smearing_method`, `smearing_sigma` consistent across all.

### Vacancy Formation Energy
- `INPUT_bulk`: reference bulk energy
- `INPUT_slab_clean`: pristine surface
- `INPUT_slab_vac`: surface with vacancy
- Use consistent parameters across all.

### KPT for Slab Calculations
- In-plane: dense mesh. **Min `12 12` for metals**; `20 20` for accurate surface energy.
- Vacuum direction: **always `1`**. Never more than 1 k-point.
- `kspacing` mode: `kspacing 0.10 0.10 1.00` (slab, z=vacuum). Bulk: `kspacing 0.10`.

---

## Electrostatic Potential / Work Function

For work function or electrostatic potential analysis on a slab:
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 200
smearing_method gauss
smearing_sigma 0.01
out_pot 2
efield_flag 1
dip_cor_flag 1
efield_dir 2
efield_pos_max 0.0
efield_pos_dec 0.1
efield_amp 0.0
```

> `out_pot 2` → `ElecStaticPot.cube` (total Hartree + local potential).
> Dipole correction (`efield_flag 1` + `dip_cor_flag 1` + `efield_amp 0.0`) removes spurious field across vacuum for asymmetric slabs.
> `efield_dir`: 0=x, 1=y, 2=z — set to the vacuum direction.

KPT for slab:
```
K_POINTS
0
Gamma
12 12 1 0 0 0
```
> Always `1` in the vacuum direction.

---

## Relaxation

```
INPUT_PARAMETERS
calculation relax
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
cal_force 1
force_thr_ev 0.01
relax_nmax 100
smearing_method gauss
smearing_sigma 0.01
```
> `cal_force 1` is mandatory for relaxation. `force_thr_ev 0.01` sets convergence threshold (eV/Å).

---

## Cell Relaxation

```
INPUT_PARAMETERS
calculation cell-relax
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
cal_force 1
cal_stress 1
stress_thr 5.0
force_thr_ev 0.01
relax_nmax 100
smearing_method gauss
smearing_sigma 0.01
```
> Both `cal_force 1` and `cal_stress 1` mandatory. `stress_thr 5.0` is the stress convergence threshold (KBAR).
