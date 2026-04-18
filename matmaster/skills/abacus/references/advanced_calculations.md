# ABACUS Advanced Calculation Patterns

## EOS (Equation of State) Calculations

Generate multiple STRU files at different volumes and run SCF at each:

1. Scale the equilibrium lattice vectors by factors [0.95, 0.96, 0.97, 0.98, 0.99, 1.00, 1.01, 1.02, 1.03, 1.04, 1.05].
2. Write one STRU file per volume point (e.g., `eos_0.95.stru`, `eos_1.00.stru`, etc.).
3. Write one INPUT per point, each with `stru_file <name>`.
4. Use `kspacing` to ensure consistent k-point density across different cell sizes.
5. Write a `run.sh` that runs SCF at each volume:

```bash
#!/bin/bash
for scale in 0.95 0.96 0.97 0.98 0.99 1.00 1.01 1.02 1.03 1.04 1.05; do
  mkdir -p eos_${scale}
  cp INPUT_eos eos_${scale}/INPUT
  cp eos_${scale}.stru eos_${scale}/STRU
  cp *.upf *.orb eos_${scale}/
  cd eos_${scale}
  OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1
  cd ..
done
```

6. After download, extract total energy from each `running_scf.log` using: `grep "!FINAL_ETOT_IS" */OUT.ABACUS/running_scf.log`
7. Fit to Birch-Murnaghan equation: E(V) = E₀ + (9V₀B₀/16){[(V₀/V)^(2/3) − 1]³B₀' + [(V₀/V)^(2/3) − 1]²[6 − 4(V₀/V)^(2/3)]}

**INPUT template** for each volume point:
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
cal_stress 1
kspacing 0.10
```

## Surface Energy Calculations

**Required calculations**: bulk cell-relax + slab relax

### Bulk reference
```
INPUT_PARAMETERS
calculation cell-relax
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
cal_force 1
cal_stress 1
force_thr_ev 0.01
stress_thr 0.5
relax_nmax 100
kspacing 0.10
stru_file bulk.stru
```

### Slab calculation
```
INPUT_PARAMETERS
calculation relax
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
cal_force 1
force_thr_ev 0.01
relax_nmax 100
kspacing 0.10 0.10 1.00
stru_file slab.stru
```

**Critical consistency rule**: `ecutwfc`, `scf_thr`, `smearing_method`, `smearing_sigma` must be identical across bulk and slab calculations.

### Surface energy formula
```
E_surf = (E_slab - n * E_bulk_per_atom * N_slab_atoms) / (2 * A)
```
where A = surface area (in-plane cell area), factor 2 = two surfaces.

## Magnetic System Calculations

For systems with magnetic elements (Fe, Co, Ni, Mn, Cr):

```
INPUT_PARAMETERS
nspin 2
mixing_beta 0.1
mixing_ndim 20
mixing_gg0 1.5
```

### Setting initial magnetic moments in STRU

```
ATOMIC_POSITIONS
Cartesian_angstrom
Fe
2.0           ; Initial magnetic moment (Bohr magnetons)
4
0.000 0.000 0.000 1 1 1
...
O
0.0           ; Non-magnetic species
6
...
```

**Tips**:
- Use `2.0` for Fe, `1.5` for Co, `0.6` for Ni as initial moments.
- Non-magnetic species: set moment to `0.0`.
- AFM ordering: set opposite signs for different sublattices (e.g., `2.0` and `-2.0`).
- `mixing_beta 0.1` (smaller) helps convergence for magnetic systems; default `0.7` often diverges.

## Gate Field / Charged Slab Calculations

For electric field, gating, or charged slab studies, see `references/electric_field.md` for detailed INPUT parameters.

Key formula: 1 a.u. of electric field = 51.4 V/Å. Typical applied fields: ~0.001 a.u.

## BSSE (Basis Set Superposition Error) Correction with Ghost Atoms

**When to apply**: Any LCAO calculation where comparing energies of sub-systems within the same basis (vacancy formation, adsorption energy, surface energy with terminated slabs).

**How**: Add ghost atoms (empty atoms that provide basis functions but no electrons):

1. Create a new species entry in STRU: `Fe_empty 55.845 Fe.upf` (same PP)
2. Add corresponding orbital in `NUMERICAL_ORBITAL`: same `.orb` file
3. Set `ntype` in INPUT to include ghost species
4. Place ghost atoms at positions where atoms were removed
5. Set ghost atom magnetic moment to `0.0` and mobility to `0 0 0`

See `references/stru_format.md` for detailed ghost atom STRU examples.

## Heterostructure / Interface Calculations

1. **Build each component slab separately** — save as individual STRU/CIF files immediately.
2. **Lattice matching**: compute mismatch. If > 5%, create commensurate supercells.
3. **Stack**: adjust c-axis to create interface with appropriate spacing (2–3 Å for vdW, 1.5–2.5 Å for covalent).
4. **Verify**: total atom count = sum of both slabs; minimum interatomic distance > 0.5 Å.
5. **Calculate**: use `kspacing` for the combined supercell; dipole correction if asymmetric.

## Batch Submission Strategy

For multi-configuration studies (EOS, surface energy, parameter scan):

**Option A: All in one job** (preferred for Bohrium)
- Write a `run.sh` that loops over configurations
- Each configuration in its own subdirectory
- Submit once, download all results

**Option B: Separate jobs** (when configurations are very different)
- Submit each configuration as a separate Bohrium job
- Poll all in parallel
- Download and aggregate results

## Post-Processing Patterns

### Extract total energy from ABACUS log
```bash
grep "!FINAL_ETOT_IS" OUT.ABACUS/running_scf.log
# Output: !FINAL_ETOT_IS -3456.789012 eV
```

### Extract Fermi energy
```bash
grep "EFERMI" OUT.ABACUS/running_scf.log
# Output: EFERMI = -4.567 eV
```

### Extract forces
```bash
# Forces appear after "TOTAL-FORCE (eV/Angstrom)" in running_scf.log
```

### Extract band gap from BANDS_1.dat
Use `parse_abacus.py --type band --dir OUT.ABACUS/` or manually:
- Read BANDS_1.dat
- Find highest occupied eigenvalue (VBM) and lowest unoccupied (CBM)
- Band gap = CBM − VBM

### Verify SCF convergence
```bash
grep "charge density convergence is achieved" OUT.ABACUS/running_scf.log
```
If not found, SCF did not converge — increase `scf_nmax` or adjust `mixing_beta`.
