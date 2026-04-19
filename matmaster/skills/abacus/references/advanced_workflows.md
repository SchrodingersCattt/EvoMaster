# ABACUS Advanced Workflows

Concrete workflow patterns for complex multi-step or multi-configuration ABACUS calculations.

---

## 1. Equation of State (EOS) — Birch-Murnaghan Fitting

Compute energy vs. volume at 5-7 different lattice constants to extract bulk modulus.

### Workflow
1. Start with the equilibrium structure (e.g., from cell-relax or literature).
2. Scale the lattice constant by factors: **0.96, 0.98, 1.00, 1.02, 1.04** (minimum 5 points).
3. For each scale factor, generate a separate STRU with scaled LATTICE_VECTORS.
4. Run SCF at each volume with **identical** INPUT parameters.
5. Collect total energies from `running_scf.log` → fit Birch-Murnaghan equation.

### File Structure
```
eos_calc/
├── INPUT          # Shared parameters (or INPUT_0.96, INPUT_0.98, etc.)
├── STRU_0.96      # Scaled lattice vectors (×0.96)
├── STRU_0.98
├── STRU_1.00      # Equilibrium
├── STRU_1.02
├── STRU_1.04
├── KPT            # Shared k-points (or use kspacing in INPUT)
├── *.upf, *.orb   # Pseudopotentials and orbitals
└── run_eos.sh     # Shell script to run all volumes
```

### INPUT (shared parameters — all volumes must be identical)
```
INPUT_PARAMETERS
calculation     scf
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-7
scf_nmax        100
smearing_method gauss
smearing_sigma  0.01
cal_stress      1
```
> Include `cal_stress 1` to get pressure data for EOS fitting.
> Use `kspacing` (e.g., `kspacing 0.10`) rather than a fixed KPT file, so k-mesh adapts to each cell size.

### run_eos.sh
```bash
#!/bin/bash
for scale in 0.96 0.98 1.00 1.02 1.04; do
    echo "=== Running scale=$scale ==="
    cp INPUT INPUT_bak
    # Add stru_file directive
    echo "stru_file STRU_${scale}" >> INPUT
    mkdir -p OUT_${scale}
    OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_${scale} 2>&1
    cp -r OUT.ABACUS OUT_${scale}/
    cp INPUT_bak INPUT
done
```

### Post-processing: Extract E(V) and Fit
```python
# Birch-Murnaghan 3rd-order EOS
import numpy as np
from scipy.optimize import curve_fit

def birch_murnaghan(V, E0, V0, B0, Bp):
    eta = (V0/V)**(2./3.)
    return E0 + 9.*V0*B0/16. * (
        (eta-1)**3 * Bp + (eta-1)**2 * (6. - 4.*eta)
    )

# volumes = [...], energies = [...]
popt, pcov = curve_fit(birch_murnaghan, volumes, energies, p0=[min(energies), volumes[len(volumes)//2], 100, 4])
E0, V0, B0_eV_A3, Bp = popt
B0_GPa = B0_eV_A3 * 160.2176634  # eV/ų → GPa
```

---

## 2. Convergence Testing (ecutwfc / kspacing)

### ecutwfc Convergence
Test at 3-5 values: e.g., **60, 80, 100, 120, 150** Ry.
- Criterion: total energy converges to < **1 meV/atom**.
- Standard ABACUS value: **100 Ry** (sufficient for most systems).

### kspacing Convergence
Test at 3-5 values: e.g., **0.20, 0.15, 0.12, 0.10, 0.08** Å⁻¹.
- Criterion: total energy converges to < **1 meV/atom**.
- Standard: **0.10** for metals, **0.12-0.15** for semiconductors/insulators.

### File Organization
Same approach as EOS: shared INPUT template, vary only the parameter under test.

---

## 3. Surface Energy Workflow

Calculate surface energy: γ = (E_slab − n·E_bulk) / (2·A).

### Steps
1. **Bulk cell-relax**: Optimize bulk unit cell → extract equilibrium lattice constant.
2. **Bulk SCF**: Calculate bulk energy per formula unit at optimized geometry.
3. **Slab construction**: Build slab from optimized bulk (5-layer, 7-layer, etc.). Add ≥15 Å vacuum.
4. **Slab relax**: Relax slab atomic positions (fix bottom 2 layers typically).
5. **Extract surface energy**: γ = (E_slab − n × E_bulk) / (2 × A_surface).

### File Structure
```
surface_energy/
├── INPUT_bulk_relax   # cell-relax: cal_force 1, cal_stress 1
├── STRU_bulk          # Bulk structure
├── KPT_bulk           # Dense 3D mesh (e.g., 20 20 20 0 0 0)
├── INPUT_slab_relax   # relax: cal_force 1, force_thr_ev 0.01
├── STRU_slab          # Slab with vacuum
├── KPT_slab           # Dense in-plane, 1 in vacuum (e.g., 20 20 1 0 0 0)
├── *.upf, *.orb
└── run.sh
```

### Consistency Requirements
- **All INPUTs** must share: `basis_type`, `ecutwfc 100`, `smearing_method gauss`, `smearing_sigma 0.01`, `scf_thr 1.0e-7`.
- Bulk INPUT: `stru_file STRU_bulk`, `kpoint_file KPT_bulk`.
- Slab INPUT: `stru_file STRU_slab`, `kpoint_file KPT_slab`.
- Each INPUT must explicitly include its task-specific parameters (`cal_force 1`, etc.).

---

## 4. Vacancy Formation Energy

E_vac = E(slab_with_vacancy) + E(bulk_atom) − E(slab_pristine).

### BSSE Correction (LCAO)
For LCAO basis, removing an atom removes basis functions → BSSE. Use ghost atoms:
1. In the vacancy STRU, place ghost atoms (`Fe_empty`) at the removed atom's position.
2. Ghost species: same PP and orbital as real species, but different label.
3. Ghost atoms: magnetic moment `0.0`, mobility `0 0 0`.
4. INPUT: `ntype` must count ghost species. Use `kspacing` (not fixed KPT).

---

## 5. Partial DOS (PDOS) Workflow

For atom-projected or orbital-projected DOS:

### NSCF INPUT additions
```
out_dos         1
dos_edelta_ev   0.01
dos_sigma       0.07
dos_nche        100
```

### KPT for DOS
Use a **dense uniform mesh** (NOT line-mode):
```
K_POINTS
0
Gamma
12 12 12 0 0 0
```
> DOS requires uniform sampling over the BZ. Line-mode k-paths produce incorrect DOS.

---

## 6. Spin-Orbit Coupling (SOC)

For heavy elements (Bi, Pb, Te, etc.) or topological materials:
```
INPUT_PARAMETERS
noncolin        1
lspinorb        1
```
> SOC doubles the number of bands. Set `nbands` accordingly.
> SOC requires fully-relativistic pseudopotentials.

---

## 7. DFT+U for Correlated Systems

For transition metal oxides (Fe, Co, Ni, Mn, Cu, V, Ti d-electrons):
```
INPUT_PARAMETERS
dft_plus_u      1
orbital_corr    2 -1    # d-orbital for first species, none for second
hubbard_u       5.0 0.0  # U value for first species
```
> Standard U values: Fe₂O₃ ~4-5 eV, NiO ~6 eV, MnO ~4 eV.
> Always test U-dependence: try 3, 4, 5, 6 eV and check physical properties.

---

## 8. Multi-Job Bohrium Submission

For workflows requiring sequential jobs (relax → SCF → NSCF), use a master `run.sh`:

```bash
#!/bin/bash
set -e

echo "=== Step 1: Cell Relaxation ==="
cp INPUT_relax INPUT
cp KPT_relax KPT
cp STRU_init STRU
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_relax 2>&1

echo "=== Step 2: SCF ==="
cp INPUT_scf INPUT
cp KPT_scf KPT
# Use relaxed structure from step 1
cp OUT.ABACUS/STRU_ION_D STRU
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_scf 2>&1

echo "=== Step 3: NSCF Band ==="
cp INPUT_nscf INPUT
cp KPT_band KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_nscf 2>&1
```

Submit with: `cmd="bash run.sh > log 2>&1"`.

---

## Common Checklist for All Advanced Workflows

Before submitting any multi-file ABACUS calculation:

- [ ] All INPUT files share identical `basis_type`, `ecutwfc`, `smearing_*`, `scf_thr`
- [ ] Each INPUT has explicit `stru_file` and `kpoint_file` directives
- [ ] All referenced files (STRU, KPT, PP, orbitals) exist in the workspace
- [ ] `ntype` in every INPUT matches the species count in its corresponding STRU
- [ ] Task-specific mandatory params are present (cal_force, cal_stress, out_chg, etc.)
- [ ] Shell script (`run.sh`) uses `set -e` to stop on first error
- [ ] `ls -la` the input directory to verify all files are present before submission
