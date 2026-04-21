# ABACUS Advanced Task Types — Reference

Complete INPUT examples and workflow guidance for advanced ABACUS calculations
beyond basic SCF / relax / cell-relax / band / DOS.

---

## Hybrid Functional (HSE06 / PBE0)

ABACUS supports hybrid functionals via `dft_functional` keyword.
**Constraint**: Hybrid functionals in ABACUS require `basis_type pw` (plane-wave),
NOT `lcao`. They are significantly more expensive; use coarser k-mesh or
smaller systems where possible.

### HSE06 SCF INPUT
```
INPUT_PARAMETERS
calculation             scf
basis_type              pw
ecutwfc                 100
dft_functional          hse
exx_hybrid_alpha        0.25
exx_hse_omega           0.11
scf_thr                 1e-7
scf_nmax                200
smearing_method         gauss
smearing_sigma          0.01
out_chg                 1
```

### PBE0 SCF INPUT
```
INPUT_PARAMETERS
calculation             scf
basis_type              pw
ecutwfc                 100
dft_functional          pbe0
exx_hybrid_alpha        0.25
scf_thr                 1e-7
scf_nmax                200
smearing_method         gauss
smearing_sigma          0.01
out_chg                 1
```

### Hybrid Functional Rules
- **MUST** use `basis_type pw` — LCAO hybrid is not supported in standard ABACUS.
- Remove `orbital_dir`, `pseudo_dir` references to `.orb` files; PW needs only `.upf`.
- Do **not** include a `NUMERICAL_ORBITAL` section in STRU for PW calculations.
- K-mesh: typically coarser than PBE (e.g. `4 4 4` for bulk, `4 4 1` for slab).
- SCF convergence is harder — use `scf_nmax 200`, possibly `mixing_beta 0.3`.
- Band structure with hybrid: do SCF first (hybrid), then NSCF with `init_chg file`.

---

## DFT+U (Hubbard Correction)

For transition metal oxides, strongly correlated systems.

### DFT+U SCF INPUT (e.g. NiO)
```
INPUT_PARAMETERS
calculation             scf
basis_type              lcao
ecutwfc                 100
scf_thr                 1e-7
scf_nmax                200
smearing_method         gauss
smearing_sigma          0.01
nspin                   2
lda_plus_u              1
hubbard_u               6.0 0.0
orbital_corr            2 -1
mixing_type             broyden
mixing_beta             0.1
mixing_ndim             20
mixing_gg0              1.5
out_chg                 1
```

### DFT+U Rules
- `hubbard_u`: one value per species in ATOMIC_SPECIES order. Set `0.0` for
  species without U correction.
- `orbital_corr`: angular momentum for U correction per species. Use `2` for
  d-orbitals (transition metals), `3` for f-orbitals (lanthanides/actinides),
  `-1` for no correction.
- **MUST** set `nspin 2` for DFT+U (spin-polarized).
- Tight mixing (`mixing_beta 0.1`) essential for convergence.
- `ntype` must match total species count in STRU.

---

## Equation of State (EOS) / Bulk Modulus

Generate multiple INPUT files for different volumes, fit E(V) curve.

### Workflow
1. Start from equilibrium structure (cell-relaxed or experimental).
2. Generate 5–7 STRU files with scaled lattice vectors (±3–5% volume).
3. Run SCF at each volume (same `ecutwfc`, `smearing_sigma`, `scf_thr`).
4. Fit E(V) to Birch-Murnaghan or Murnaghan equation.

### EOS INPUT (one per volume point)
```
INPUT_PARAMETERS
calculation             scf
basis_type              lcao
ecutwfc                 100
scf_thr                 1e-7
scf_nmax                100
smearing_method         gauss
smearing_sigma          0.01
cal_stress              1
out_chg                 0
stru_file               STRU_v095
kpoint_file             KPT
```

### EOS Rules
- **ALL INPUT files MUST share**: `basis_type`, `ecutwfc`, `smearing_method`,
  `smearing_sigma`, `scf_thr`, `kspacing` (or equivalent KPT density).
- Each INPUT must have `stru_file` pointing to its specific STRU.
- Set `cal_stress 1` so stress tensor is available for fitting validation.
- Use python/ASE to generate the scaled structures:
  ```python
  from pymatgen.core import Structure
  s = Structure.from_file("relaxed.cif")
  for scale in [0.95, 0.97, 0.99, 1.00, 1.01, 1.03, 1.05]:
      s_copy = s.copy()
      s_copy.scale_lattice(s.volume * scale)
      s_copy.to(filename=f"STRU_v{int(scale*100):03d}", fmt="abacus/stru")
  ```

---

## Phonon Calculation (Finite Displacement)

ABACUS does not have built-in DFPT. Use finite displacement method with Phonopy.

### Workflow
1. Relax the structure (`calculation cell-relax`).
2. Generate displaced supercells with Phonopy.
3. Run SCF + force calculation on each displacement.
4. Collect forces and compute phonon dispersion with Phonopy.

### Phonon SCF INPUT (for each displaced supercell)
```
INPUT_PARAMETERS
calculation             scf
basis_type              lcao
ecutwfc                 100
scf_thr                 1e-8
scf_nmax                100
smearing_method         gauss
smearing_sigma          0.01
cal_force               1
kspacing                0.10
out_chg                 0
```

### Phonon Rules
- Tight `scf_thr 1e-8` for accurate forces.
- `cal_force 1` is **MANDATORY** — forces are the key output.
- Use `kspacing` (not fixed KPT) for supercells of varying size.
- Supercell typically 2×2×2 for bulk, 2×2×1 for surfaces.

### Phonopy Integration
```bash
# Generate displaced structures (ABACUS format)
phonopy -d --dim 2 2 2 --abacus
# → creates supercell-001.stru, supercell-002.stru, etc.

# After all SCF+force calculations:
phonopy --abacus -f disp-001/running_scf.log disp-002/running_scf.log ...
phonopy --dim 2 2 2 --band "0 0 0  0.5 0 0  0.5 0.5 0  0 0 0"
```

---

## SOC / Noncollinear Magnetism

For heavy elements (Bi, Pb, W, rare earths) or topological materials.

### SOC INPUT
```
INPUT_PARAMETERS
calculation             scf
basis_type              lcao
ecutwfc                 100
scf_thr                 1e-7
scf_nmax                200
smearing_method         gauss
smearing_sigma          0.01
nspin                   4
noncolin                1
lspinorb                1
mixing_type             broyden
mixing_beta             0.1
mixing_ndim             20
out_chg                 1
```

### SOC Rules
- `nspin 4` + `noncolin 1` + `lspinorb 1` must ALL be set together.
- Band count approximately doubles — set `nbands` accordingly for NSCF.
- PP must be fully relativistic (j-dependent) for proper SOC. Standard
  NC PPs may give qualitative but not quantitative SOC effects.
- SCF convergence is harder: use tight mixing, higher `scf_nmax`.

---

## Projected Band Structure / Partial DOS

### Projected Band INPUT (NSCF step)
```
INPUT_PARAMETERS
calculation             nscf
basis_type              lcao
ecutwfc                 100
scf_thr                 1e-7
scf_nmax                300
init_chg                file
out_band                1
out_proj_band           1
nbands                  60
symmetry                0
smearing_method         gauss
smearing_sigma          0.01
```

- `out_proj_band 1`: writes orbital-projected band eigenvalues.
- Requires `out_band 1` simultaneously.
- Same two-step workflow as regular band: SCF (out_chg 1) → NSCF.

---

## vdW Corrections

For layered materials, molecular crystals, adsorption on surfaces.

### DFT-D3 INPUT additions
```
vdw_method              d3_bj
```

### DFT-D2 INPUT additions
```
vdw_method              d2
vdw_s6                  0.75
```

### Rules
- `d3_bj` (DFT-D3 with Becke-Johnson damping) is recommended for PBE.
- `d2` is older but sometimes required for benchmarking.
- vdW corrections apply to all calculation types (SCF, relax, cell-relax, MD).

---

## Fixed-Spin-Moment (Constrained Magnetization)

### INPUT additions
```
nspin                   2
nupdown                 4.0
```

- `nupdown`: difference between up and down electrons. Set to desired
  total magnetic moment in μ_B.
- Useful for AFM configurations or specific spin states.

---

## Berry Phase (Polarization)

For ferroelectric materials, polarization calculations.

### INPUT
```
INPUT_PARAMETERS
calculation             scf
basis_type              lcao
ecutwfc                 100
scf_thr                 1e-7
scf_nmax                100
smearing_method         gauss
smearing_sigma          0.01
berry_phase             1
gdir                    3
```

- `berry_phase 1`: compute Berry phase along `gdir` direction (1=x, 2=y, 3=z).
- Run for both centrosymmetric reference and polar structure; difference
  gives spontaneous polarization.

---

## Wannier Function Interface

### INPUT additions
```
towannier90             1
wannier_spin            up
out_wfc_lcao            1
```

- Generates `.amn`, `.mmn`, `.eig` files for Wannier90 post-processing.
- `wannier_spin`: `up` or `down` for spin-polarized, omit for non-spin.
- Useful for tight-binding model construction, transport calculations.

---

## Multi-Step Complex Workflows — run.sh Patterns

### SCF → Band + DOS (three-step)
```bash
#!/bin/bash
set -e
# Step 1: SCF
cp INPUT_scf INPUT && cp KPT_scf KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_scf 2>&1

# Step 2: NSCF band
cp INPUT_band INPUT && cp KPT_band KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_band 2>&1

# Step 3: NSCF DOS
cp INPUT_dos INPUT && cp KPT_dos KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_dos 2>&1
```

### Relax → Band (two-step)
```bash
#!/bin/bash
set -e
# Step 1: Relaxation
cp INPUT_relax INPUT && cp KPT_relax KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_relax 2>&1

# Step 2: SCF for charge (using relaxed structure)
# Note: ABACUS writes STRU_ION_D after relax; copy it
cp OUT.ABACUS/STRU_ION_D STRU
cp INPUT_scf INPUT && cp KPT_scf KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_scf 2>&1

# Step 3: NSCF band
cp INPUT_band INPUT && cp KPT_band KPT
OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_band 2>&1
```

### EOS Multi-Volume (loop)
```bash
#!/bin/bash
set -e
for vol in v095 v097 v099 v100 v101 v103 v105; do
  echo "=== Volume: $vol ==="
  cp INPUT_eos INPUT
  cp STRU_${vol} STRU
  OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_${vol} 2>&1
  mkdir -p results_${vol}
  cp -r OUT.ABACUS results_${vol}/
done
```
