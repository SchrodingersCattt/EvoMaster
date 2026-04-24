# ABACUS Advanced Task Templates

Quick-reference INPUT templates for advanced ABACUS workflows. For basic SCF/relax/band/DOS,
see `input_examples.md`. All templates assume `basis_type lcao` unless noted; adjust `ecutwfc` for PW.

---

## Basis Type Detection (LCAO vs PW)

Before generating INPUT, inspect the provided STRU file:

| STRU has `NUMERICAL_ORBITAL` section? | → `basis_type` | `ecutwfc` default |
|---------------------------------------|-----------------|-------------------|
| Yes (lists `.orb` files)              | `lcao`          | `100`             |
| No                                    | `pw`            | `50`              |

**Rule**: If a STRU file is provided, always read it first. Match `basis_type` to what the STRU supports.
Do not use `basis_type lcao` without orbital files, and do not use `basis_type pw` if the task
expects LCAO features (BSSE ghost atoms, LCAO-specific DOS, etc.).

---

## EOS / Bulk Modulus (Equation of State)

Generate multiple SCF inputs at different volumes (typically 7–11 points, ±5% around equilibrium).

### Strategy
1. Start from the equilibrium STRU.
2. Scale lattice vectors uniformly: factors like 0.96, 0.97, 0.98, 0.99, 1.00, 1.01, 1.02, 1.03, 1.04.
3. Run SCF at each volume; extract total energy.
4. Fit E(V) to Birch-Murnaghan or Murnaghan EOS to get B₀ (bulk modulus) and V₀.

### INPUT template (each volume point)
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
stru_file       STRU_v096
kpoint_file     KPT
```

**Key points**:
- `cal_stress 1` is recommended (verifies pressure at each point).
- All volume points must share identical `ecutwfc`, `smearing_method`, `smearing_sigma`, `scf_thr`.
- Use the SAME KPT file (or `kspacing`) for all points — consistent k-density matters.
- Each STRU file differs only in LATTICE_VECTORS (scaled uniformly).
- Use `stru_file` to point each INPUT to its own STRU.

### Generating scaled STRU files

Scale lattice vectors by factor `s` (keep fractional coordinates unchanged):
```python
import numpy as np

scales = [0.96, 0.97, 0.98, 0.99, 1.00, 1.01, 1.02, 1.03, 1.04]
# For each scale, multiply all LATTICE_VECTORS by s^(1/3) to scale volume by s
# Volume scales as s, linear dimension scales as s^(1/3)
for s in scales:
    linear = s ** (1.0/3.0)
    # new_vector = original_vector * linear
```

---

## DFT+U (Hubbard U Correction)

For systems with localized d/f electrons (transition metal oxides, rare earths).

### INPUT additions
```
INPUT_PARAMETERS
calculation     scf
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-7
scf_nmax        200
smearing_method gauss
smearing_sigma  0.01
dft_plus_u      1
orbital_corr    2 -1
hubbard_u       5.0 0.0
```

**Parameter guide**:
- `dft_plus_u 1`: Enable DFT+U.
- `orbital_corr`: One integer per species (same order as ATOMIC_SPECIES in STRU).
  - `2` = d-orbital correction, `3` = f-orbital, `-1` = no correction.
  - Example for Fe₂O₃: Fe gets `2` (d-orbital), O gets `-1` (none) → `orbital_corr 2 -1`.
- `hubbard_u`: U value (eV) per species, same order. Set `0.0` for species without +U.
  - Typical values: Fe 3d → 4.0–5.3 eV, Co 3d → 3.3–5.0 eV, Ni 3d → 5.1–6.4 eV, Ti 3d → 3.0–4.0 eV, Mn 3d → 3.5–4.5 eV.
- `scf_nmax 200`: DFT+U often needs more SCF cycles.
- For magnetic systems, add `nspin 2` and mixing parameters.

---

## Phonon Calculation (Finite Displacement)

ABACUS does not have built-in phonon; use finite displacement with an external tool (Phonopy).

### Strategy
1. **Relax** the structure first (`cell-relax` or `relax`).
2. **Generate displacements** with Phonopy:
   ```bash
   phonopy -d --dim="2 2 2" --abacus
   ```
   This creates `STRU-001`, `STRU-002`, etc.
3. **Run SCF** on each displaced structure (with `cal_force 1`).
4. **Collect forces** and run Phonopy post-processing.

### INPUT template (per displacement)
```
INPUT_PARAMETERS
calculation     scf
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-8
scf_nmax        100
smearing_method gauss
smearing_sigma  0.005
cal_force       1
stru_file       STRU-001
kpoint_file     KPT
```

**Key points**:
- Tighter `scf_thr` (1e-8) and smaller `smearing_sigma` (0.005) for accurate forces.
- `cal_force 1` is mandatory — phonon needs forces.
- Use `kspacing` for supercells: `kspacing 0.10` (or appropriate for supercell size).
- All displacements must share identical numerical parameters.

---

## Spin-Orbit Coupling (SOC)

### INPUT additions
```
INPUT_PARAMETERS
calculation     scf
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-7
scf_nmax        200
smearing_method gauss
smearing_sigma  0.01
noncolin        1
lspinorb        1
```

**Key points**:
- `noncolin 1`: Enable noncollinear magnetism (required for SOC).
- `lspinorb 1`: Enable spin-orbit coupling.
- Do NOT set `nspin 2` when using `noncolin 1` — they are mutually exclusive.
- SOC doubles the number of bands; set `nbands` accordingly for NSCF.
- Requires fully relativistic pseudopotentials (check PP file).

---

## van der Waals Corrections

### INPUT additions (DFT-D3)
```
INPUT_PARAMETERS
vdw_method      d3_bj
```

### Available methods
| `vdw_method` | Description |
|--------------|-------------|
| `d2`         | Grimme DFT-D2 |
| `d3_0`       | DFT-D3 (zero damping) |
| `d3_bj`      | DFT-D3 (Becke-Johnson damping) — **recommended** |

**Key points**:
- Add to any calculation type (SCF, relax, cell-relax, MD).
- For layered materials, surfaces, molecular crystals: vdW correction is essential.
- `d3_bj` is the most widely used and recommended default.

---

## Molecular Dynamics (MD)

### INPUT template
```
INPUT_PARAMETERS
calculation     md
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-7
scf_nmax        100
smearing_method gauss
smearing_sigma  0.01
cal_force       1
cal_stress      1
md_type         nvt
md_nstep        5000
md_dt           1.0
md_tfirst       300
md_thermostat   nhc
md_tfreq        0.025
```

**Parameter guide**:
- `md_type`: `nve` (microcanonical), `nvt` (canonical), `npt` (isothermal-isobaric).
- `md_nstep`: Total MD steps.
- `md_dt`: Time step in fs (1.0 fs typical; 0.5 fs for light elements like H).
- `md_tfirst`: Target temperature (K). For NVT, also set `md_tlast` if ramping.
- `md_thermostat`: `nhc` (Nosé-Hoover chain, recommended for NVT).
- `md_tfreq`: Thermostat frequency (0.025 typical).
- For NPT: add `md_pfirst`, `md_plast` (pressure in kbar).
- `cal_force 1` is mandatory for MD.
- `cal_stress 1` is mandatory for NPT; optional but useful for NVT.

---

## Surface Energy (Multi-File Workflow)

Surface energy = (E_slab − n × E_bulk) / (2 × A), where n = atoms in slab / atoms in bulk unit.

### Files to generate
1. **Bulk cell-relax**: Get equilibrium bulk energy per atom.
2. **Slab relax** (multiple thicknesses recommended): Relax atomic positions with fixed cell.

### INPUT — Bulk
```
INPUT_PARAMETERS
calculation     cell-relax
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-7
scf_nmax        100
smearing_method gauss
smearing_sigma  0.01
cal_force       1
cal_stress      1
force_thr_ev    0.01
stress_thr      0.5
relax_nmax      100
stru_file       STRU_bulk
kpoint_file     KPT_bulk
```

### INPUT — Slab
```
INPUT_PARAMETERS
calculation     relax
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-7
scf_nmax        100
smearing_method gauss
smearing_sigma  0.01
cal_force       1
force_thr_ev    0.01
relax_nmax      100
stru_file       STRU_slab
kpoint_file     KPT_slab
```

### KPT files
- **Bulk**: `KPT_bulk` — dense 3D mesh (e.g. `12 12 12 0 0 0`).
- **Slab**: `KPT_slab` — dense in-plane, 1 in vacuum (e.g. `12 12 1 0 0 0`).

**Key points**:
- All INPUTs must share `ecutwfc`, `smearing_method`, `smearing_sigma`, `scf_thr`.
- Slab must have ≥15 Å vacuum.
- Each INPUT must have explicit `stru_file` and `kpoint_file`.

---

## Vacancy Formation Energy

E_vac = E_defect − (N−1)/N × E_pristine, where N = atoms in pristine supercell.

### Strategy
1. Build supercell from bulk (e.g. 3×3×3).
2. Remove one atom → vacancy structure.
3. For LCAO: add ghost atom at vacancy site for BSSE correction (see `stru_format.md`).
4. Run SCF on both pristine and vacancy supercells.

### INPUT template (same for pristine and vacancy)
```
INPUT_PARAMETERS
calculation     scf
basis_type      lcao
ecutwfc         100
scf_thr         1.0e-7
scf_nmax        100
smearing_method gauss
smearing_sigma  0.01
kspacing        0.10
nspin           2
mixing_beta     0.1
mixing_ndim     20
mixing_gg0      1.5
```

**Key points**:
- Use `kspacing` (not KPT file) — adapts to supercell size automatically.
- For magnetic metals (Fe, Co, Ni): `nspin 2` + mixing parameters.
- `ntype` must count ghost species too (see `stru_format.md` for BSSE ghost atoms).

---

## Hybrid DFT (HSE06)

### INPUT additions
```
INPUT_PARAMETERS
dft_functional  hse
exx_hybrid_alpha 0.25
exx_pca_threshold 1e-4
exx_ccp_rmesh_times 1.5
```

**Key points**:
- `dft_functional hse`: Activates HSE06 functional.
- `exx_hybrid_alpha 0.25`: Standard HSE06 mixing parameter.
- Hybrid DFT is very expensive — use smaller k-meshes and test convergence.
- Supported for both LCAO and PW, but LCAO is more efficient for hybrid.
- For band gaps: run HSE SCF first, then NSCF for band structure (same two-step workflow).
