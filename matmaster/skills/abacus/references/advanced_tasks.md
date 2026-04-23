# ABACUS Advanced Task Types — INPUT Templates & Workflows

Quick-reference for ABACUS tasks beyond basic SCF/relax. Each section provides a
ready-to-use INPUT template and critical notes. See also `input_examples.md` for
the standard SCF/relax/band/DOS workflows.

---

## Equation of State (EOS) / Bulk Modulus

**Workflow**: Generate 5–7 structures at ±5% volume → SCF each → fit E(V) to
Birch-Murnaghan.

### INPUT (each volume point — SCF)
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
stru_file STRU_v1
kpoint_file KPT
```

### Critical rules
- `cal_stress 1` for each point — needed for P(V) fitting.
- Use `kspacing` instead of KPT file for consistency across different cell sizes.
- Generate STRU files by uniformly scaling lattice vectors; keep fractional
  coordinates fixed. Use `convert_format.py` or pymatgen to rescale.
- **All points must share identical**: `ecutwfc`, `smearing_method`, `smearing_sigma`,
  `scf_thr`, `basis_type`.
- Fitting: Birch-Murnaghan 3rd-order: `E(V) = E₀ + (9/16)B₀V₀ × [...]`.
  Or use `scipy.optimize.curve_fit` with the standard BM3 formula.

### run.sh (multi-volume loop)
```bash
#!/bin/bash
for i in 1 2 3 4 5 6 7; do
  cp INPUT_scf INPUT
  sed -i "s/STRU_v1/STRU_v${i}/" INPUT
  OMP_NUM_THREADS=1 mpirun -np 16 abacus > log_v${i} 2>&1
  mv OUT.ABACUS OUT_v${i}
done
```

---

## DFT+U (Hubbard U correction)

### INPUT
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 200
smearing_method gauss
smearing_sigma 0.01
nspin 2
mixing_beta 0.1
mixing_ndim 20
mixing_gg0 1.5
dft_plus_u 1
orbital_corr 2 -1
hubbard_u 4.0 0.0
```

### Critical rules
- **`nspin 2` is MANDATORY** for DFT+U — needed for spin-dependent U correction.
- `orbital_corr` and `hubbard_u` have ONE entry per species (same order as
  `ATOMIC_SPECIES`). `-1` means no U applied; `2` = d-orbital (l=2).
- Common U values: Fe d→4.0–5.0 eV; Ti d→3.0–4.0 eV; Mn d→3.9 eV;
  Co d→3.3 eV; Ni d→5.3–6.4 eV; Cu d→4.0–5.0 eV.
- For O (no U): `orbital_corr ... -1`, `hubbard_u ... 0.0`.
- Tight mixing (`mixing_beta 0.1`) essential for convergence.

---

## Hybrid DFT (HSE06 / PBE0)

### INPUT
```
INPUT_PARAMETERS
calculation scf
basis_type pw
ecutwfc 60
scf_thr 1.0e-7
scf_nmax 200
smearing_method gauss
smearing_sigma 0.01
dft_functional hse
exx_hybrid_alpha 0.25
exx_hse_omega 0.11
exx_real_number 1
exx_pca_threshold 1e-4
```

### Critical rules
- **`basis_type pw` is MANDATORY** for hybrid functionals in ABACUS.
- HSE06: `dft_functional hse`, `exx_hybrid_alpha 0.25`, `exx_hse_omega 0.11`.
- PBE0: `dft_functional pbe0`, `exx_hybrid_alpha 0.25`.
- `ecutwfc 60` is a reasonable starting point for PW (vs 100 for LCAO).
- Hybrid calculations are ~10–100× more expensive than GGA.
- Use `exx_pca_threshold` to control accuracy/speed tradeoff.

---

## Phonon Calculation (via Phonopy finite displacement)

### Step 1: Tight SCF → force accuracy
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-8
scf_nmax 100
smearing_method gauss
smearing_sigma 0.005
cal_force 1
out_force 1
```

### Workflow
1. Create supercell with Phonopy: `phonopy -d --dim 2 2 2 --abacus`
2. Run ABACUS SCF on each displaced supercell (use `kspacing` for adaptive k-mesh).
3. Collect forces: `phonopy -f OUT_*/running_scf.log --abacus`
4. Post-process: `phonopy -p band.conf`

### Critical rules
- **`scf_thr 1.0e-8`** (tighter than standard) — phonon forces need higher accuracy.
- **`cal_force 1`** is MANDATORY.
- Supercell should be ≥ 10 Å in each direction for accurate phonons.
- Use `kspacing` in INPUT for the displaced supercells (not a fixed KPT mesh).

---

## Spin-Orbit Coupling (SOC) / Noncollinear Magnetism

### INPUT
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 200
smearing_method gauss
smearing_sigma 0.01
noncolin 1
lspinorb 1
mixing_beta 0.1
mixing_ndim 20
```

### Critical rules
- `noncolin 1` enables noncollinear magnetism.
- `lspinorb 1` adds spin-orbit coupling on top.
- Number of bands doubles (spinor wavefunctions).
- Initial magnetic moments in STRU: per-atom `mx my mz` (3-component vector).
- SOC requires fully relativistic pseudopotentials (FR-PP).

---

## van der Waals Corrections

### INPUT additions (append to any calculation type)
```
# DFT-D3(BJ) — Grimme's D3 with Becke-Johnson damping
vdw_method d3_bj

# DFT-D2 — Grimme's D2
vdw_method d2
```

### Critical rules
- Just add `vdw_method d3_bj` (or `d2`) to any existing INPUT.
- Works with both LCAO and PW basis types.
- Recommended for layered materials, molecular crystals, and adsorption systems.

---

## Molecular Dynamics (NVT / NPT)

### INPUT (NVT with Nosé-Hoover)
```
INPUT_PARAMETERS
calculation md
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
cal_force 1
md_type nhc
md_nstep 5000
md_dt 1.0
md_tfirst 300
md_tfreq 0.01
```

### INPUT (NPT — constant pressure)
```
INPUT_PARAMETERS
calculation md
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
cal_force 1
cal_stress 1
md_type msst
md_nstep 5000
md_dt 1.0
md_tfirst 300
md_tfreq 0.01
md_pfirst 0.0
md_pfreq 0.01
```

### Critical rules
- **`cal_force 1`** MANDATORY for MD.
- NPT additionally needs **`cal_stress 1`**.
- `md_dt`: timestep in fs. 1.0 fs is safe default; 0.5 for light atoms (H).
- Use supercell (≥ 64 atoms recommended for liquid/amorphous).
- `kspacing` preferred over KPT file for supercells.

---

## Projected Band Structure (fat bands)

### Step 1: SCF
Standard SCF with `out_chg 1`.

### Step 2: NSCF
```
INPUT_PARAMETERS
calculation nscf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 300
init_chg file
out_band 1
out_proj_band 1
nbands 40
symmetry 0
smearing_method gauss
smearing_sigma 0.01
```

### Critical rules
- `out_proj_band 1` writes orbital-projected band data.
- Still requires `init_chg file`, `symmetry 0`, `nbands`, `out_band 1`.
- Output: `BANDS_1.dat` + `PBANDS_*` files for plotting.

---

## Berry Phase / Polarization

### INPUT
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
berry_phase 1
gdir 3
```

### Critical rules
- `berry_phase 1` enables Berry phase calculation.
- `gdir`: direction (1=a, 2=b, 3=c) for polarization.
- Requires well-converged SCF; use dense k-mesh in the `gdir` direction.
- Output: `running_scf.log` contains the Berry phase and polarization values.
