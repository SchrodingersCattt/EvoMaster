# ABACUS Complete INPUT Examples

## Quick Reference: Mandatory Parameters by Task Type

Always include **universal baseline**: `calculation`, `basis_type`, `ntype`, `ecutwfc`, `scf_thr`, `scf_nmax`, `smearing_method`, `smearing_sigma`.
> **`ntype`**: Must equal the number of species in STRU `ATOMIC_SPECIES` section. This is mandatory — ABACUS will fail or behave incorrectly without it.
> **Basis-aware default**: `ecutwfc 100` is the standard baseline for `basis_type lcao`; for `basis_type pw`, prefer `ecutwfc 50` unless PP/system-specific convergence tests require higher values.

| Task | Additional mandatory parameters | Common omission |
|------|---------------------------------|-----------------|
| SCF (pre-NSCF) | `out_chg 1` | Forgetting `out_chg` → NSCF cannot read charge |
| Band (NSCF) | `init_chg file`, `out_band 1`, `nbands`, `symmetry 0`; **PW add `pw_diag_thr 1.0e-5`** | Leaving `symmetry 1` → k-path folded; PW missing `pw_diag_thr` → noisy eigenvalues |
| DOS (NSCF) | `init_chg file`, `out_dos 1`, `dos_edelta_ev`, `dos_sigma`, `dos_nche`, `nbands`, `symmetry 0`; **PW add `pw_diag_thr 1.0e-5`** | Missing `dos_nche` for LCAO; PW missing `pw_diag_thr` → noisy DOS |
| Relax | **`cal_force 1`**, `force_thr_ev 0.01`, `relax_nmax 100` | Missing `cal_force` → no force output |
| Cell-relax | **`cal_force 1`**, **`cal_stress 1`**, `force_thr_ev 0.01`, `stress_thr 0.5`, `relax_nmax 100` | Missing `cal_force` or `cal_stress` → relaxation silently broken |
| Work function / dipole | `out_pot 2`, `efield_flag 1`, `dip_cor_flag 1`, `efield_dir <vacuum>`, `efield_amp 0.0` | Missing `efield_amp 0.0` (pure dipole correction) |
| Spin-polarized | `nspin 2`, `mixing_beta 0.1`, `mixing_ndim 20`, `mixing_gg0 1.5` | Omitting mixing params → SCF diverges |
| DFT+U (strongly correlated) | `dft_plus_u 1`, `orbital_corr`, `hubbard_u`, `nspin 2` | See DFT+U section below |
| Slab KPT | Always `1` in vacuum direction (e.g. `20 20 1 0 0 0`) | Using dense mesh in vacuum direction |
| Supercell / vacancy / defect / BSSE | **`kspacing` in INPUT** (e.g. `kspacing 0.10`) | Using fixed KPT mesh for variable-size supercells |
| Large supercell (>30 atoms) LCAO | `gamma_only 1` (Gamma-point only, no KPT file needed) | Using multi-k on already-folded supercell BZ |
| Manual orbital occupation (ocp — NOT `smearing_method fixed`) | `ocp 1`, `ocp_set ...`, `nspin 2`, **`gamma_only 1`** | Missing `gamma_only` → band ordering changes with k-points, `ocp_set` indices become wrong |
| PEXSI solver | `ks_solver pexsi`, `pexsi_npole 80`, `gamma_only 1` | Missing `pexsi_npole` → uses default 40 (less accurate); PEXSI requires LCAO + gamma_only |

> **⚠ `latname` values (exact strings, no abbreviations)**: `sc`, `fcc`, `bcc`, `hexagonal`, `trigonal`, `st`, `bct`, `so`, `baco`, `fco`, `bco`, `sm`, `bacm`, `triclinic`. Do NOT use abbreviations like `hex` — ABACUS will quit with "latname not supported!".

> **⚠ `force_thr_ev` vs `force_thr`**: Always use `force_thr_ev` (unit: eV/Å). The parameter `force_thr` uses Ry/Bohr — completely different units. `force_thr_ev 0.01` ≈ `force_thr 3.9e-4`. Mixing them up produces absurdly loose or tight thresholds.

> **⚠ Supercell k-points**: For **any supercell** (vacancy, defect, BSSE ghost atoms, adsorption), always use `kspacing` inside INPUT instead of a separate KPT file. This guarantees uniform k-point density that automatically adapts to cell size. Value: `0.10` Å⁻¹ for metals, `0.12`–`0.15` for insulators. For slab supercells: `kspacing 0.10 0.10 1.00` (z=vacuum). **Exception**: when `ocp 1` (fixed occupation) is used, ALWAYS use `gamma_only 1` instead of `kspacing` — `ocp_set` indices are only valid at Gamma point.

### Smearing Method

| `smearing_method` | Use case | `smearing_sigma` | Notes |
|-------------------|----------|-------------------|-------|
| `gauss` | Default for metals and general use | 0.01–0.02 | Standard Gaussian smearing |
| `mp` | Metals requiring better total energy | 0.01–0.02 | Methfessel-Paxton, more accurate forces |
| `fd` | Finite-temperature DFT | 0.01–0.05 | Fermi-Dirac distribution |
| `fixed` | Insulators / exact integer occupation | *(ignored)* | No smearing — each state is 0 or 1. **This is NOT the same as `ocp 1`** (manual orbital occupation). Use `fixed` when you want standard SCF without artificial broadening. |

> **⚠ `smearing_method fixed` ≠ `ocp 1`**: `smearing_method fixed` simply uses step-function occupation (no broadening) in a normal SCF. `ocp 1` manually specifies per-orbital occupation numbers and requires `gamma_only 1`. When a task says "fixed smearing" or "fixed occupation method", it means `smearing_method fixed` — not `ocp 1`.

### Charge Mixing

| `mixing_type` | Use case | `mixing_beta` range | Notes |
|---------------|----------|---------------------|-------|
| `broyden` | Default, metals, non-magnetic | 0.7–0.8 | Fast convergence |
| `pulay` | Magnetic / DFT+U (small systems, <20 atoms) | 0.4–0.6 | Add `mixing_ndim 20`, `mixing_gg0 1.5` |
| `pulay` | **Large magnetic systems (>30 atoms, DFT+U)** | **0.01–0.05** | See below |
| `plain` | Debugging / baseline comparison | 0.3–0.4 | Slowest but most stable |

> **⚠️ Large magnetic/DFT+U systems (>30 atoms)**: Use very conservative `mixing_beta 0.01–0.05` to prevent charge sloshing. Also add `mixing_beta_mag 1.6` (separate magnetic moment mixing rate) and increase `scf_nmax` to 200+ since convergence is slow. Without these, SCF will oscillate indefinitely for systems like iron phosphates, transition-metal oxides, etc.

> **Rule**: When setting `mixing_type`, ALWAYS explicitly set `mixing_beta` in the same INPUT. Never rely on the default — it may not converge for your system.
> When comparing mixing strategies (e.g. broyden vs plain), each INPUT must have its own `mixing_type` + `mixing_beta` pair.

### DFT+U — When and How

**When to use DFT+U**: Systems containing transition-metal 3d or rare-earth 4f electrons in localized environments (oxides, phosphates, fluorides, sulfides). Standard PBE severely over-delocalizes these electrons, giving wrong band gaps, magnetic moments, and relative phase energies.

**Must-use cases** (always add DFT+U without being asked):
- Fe, Co, Ni, Mn, Cr, V, Ti in oxides/phosphates/silicates (e.g. LiFePO₄, NiO, Fe₂O₃, NFPP)
- Cu in cuprates, Ce/U in f-electron systems

**Typical U values** (eV, on 3d orbitals):
| Element | U range | Common choice |
|---------|---------|---------------|
| Fe | 3.0–5.0 | 3.5–4.0 |
| Co | 3.0–5.0 | 3.3 |
| Ni | 5.0–7.0 | 5.0–6.0 |
| Mn | 3.5–5.0 | 4.0 |
| V | 3.0–4.0 | 3.25 |
| Ti | 2.5–4.0 | 3.0 |

**ABACUS syntax**:
```
dft_plus_u      1
orbital_corr    2 -1        # 2=d for Fe, -1=none for O (one per species in STRU order)
hubbard_u       3.5 0.0     # U values matching orbital_corr
```

### Ultrasoft Pseudopotential (USPP) — `ecutrho`

When using ultrasoft pseudopotentials (filename often contains `rrkjus` or `us`), you MUST set `ecutrho` explicitly:
```
ecutwfc             50
ecutrho             400
```
- `ecutrho` controls the FFT mesh for augmented charge density
- Typical ratio: `ecutrho` = 8–10 × `ecutwfc` (for USPP)
- Without `ecutrho`, ABACUS defaults to 4×ecutwfc which is insufficient for USPP — results will have aliasing errors
- NCPP (norm-conserving) does NOT need `ecutrho` — omit it for NCPP calculations

### Relaxation INPUT Example
```
INPUT_PARAMETERS
calculation relax
basis_type lcao
ntype 1
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
cal_force 1
force_thr_ev 0.01
relax_nmax 100
relax_method cg
```

### Cell Relaxation INPUT Example
```
INPUT_PARAMETERS
calculation cell-relax
basis_type pw
ntype 1
ecutwfc 50
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
cal_force 1
cal_stress 1
force_thr_ev 0.01
stress_thr 0.5
relax_nmax 100
relax_method bfgs
```
> **Critical**: `cal_force 1` and `cal_stress 1` are BOTH mandatory for cell-relax. Without `cal_force 1`, ABACUS does not compute forces and the optimizer cannot work. Without `cal_stress 1`, cell vectors are not optimized. These are NOT implied by `calculation cell-relax` — you must include them explicitly.

> **`relax_method`**: Always set explicitly. Use `cg` (conjugate gradient) for atomic relax, `bfgs` (quasi-Newton) for cell-relax. BFGS converges faster for cell optimization via Hessian approximation.

### Noncollinear / Spin-Orbit Coupling (SOC)
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ntype 1
nspin 4
noncolin 1
lspinorb 1
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 200
smearing_method gauss
smearing_sigma 0.01
mixing_type broyden
mixing_beta 0.2
mixing_ndim 8
```
All three are required together: `nspin 4` + `noncolin 1` + `lspinorb 1`. Values `1` and `true` are both valid. Without `nspin 4`, noncollinear/SOC silently falls back to collinear.

### BSSE Ghost Atom INPUT Example (Bulk / Supercell)
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ntype 2
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
kspacing 0.10
```
> Use `kspacing` (not a KPT file) for supercell/vacancy/BSSE calculations.
> For magnetic systems, add `nspin 2` and tune mixing parameters (`mixing_beta`, `mixing_ndim`, `mixing_gg0`) for convergence.

### BSSE Ghost Atom INPUT Example (Slab)
Same as above, but set the vacuum direction of kspacing to `1.00`:
```
kspacing 0.10 0.10 1.00
```

### PEXSI Solver INPUT Example (33_pexsi)
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ntype 1
ks_solver pexsi
pexsi_npole 80
pexsi_temp 0.1
ecutwfc 100
scf_thr 1.0e-6
scf_nmax 200
nspin 2
gamma_only 1
smearing_method gauss
smearing_sigma 0.01
pseudo_dir /root/apns-pseudopotentials-v1/
orbital_dir /root/apns-orbitals-efficiency-v1/
```
> PEXSI requires `basis_type lcao` and `gamma_only 1`. Always set `pexsi_npole` (default 40, use 80 for production).

### Work Function / Electrostatic Potential INPUT Example
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ntype 1
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
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
Slab KPT for work function (z = vacuum): `20 20 1 0 0 0`

### External Electric Field INPUT Example (28_efield)
```
INPUT_PARAMETERS
calculation scf
basis_type pw
ntype 1
ecutwfc 50
scf_thr 1.0e-7
scf_nmax 200
smearing_method gauss
smearing_sigma 0.015
efield_flag 1
dip_cor_flag 0
efield_dir 2
efield_pos_max 0.95
efield_pos_dec 0.10
efield_amp 0.001
pseudo_dir /root/apns-pseudopotentials-v1/
```

> **⚠ `dip_cor_flag` distinguishes two different physics:**
> - `dip_cor_flag 1` + `efield_amp 0.0` = **dipole correction only** (work function, no external field)
> - `dip_cor_flag 0` + `efield_amp ≠ 0` = **external electric field** (sawtooth field applied to slab)
>
> When a task says "external electric field" or "apply E-field", use `dip_cor_flag 0`. When it says "work function" or "dipole correction", use `dip_cor_flag 1` + `efield_amp 0.0`.

---

## Two-Step Electronic Property Workflow

Electronic property calculations (band structure, DOS) require: SCF → NSCF.

### Step 1: SCF INPUT (charge density output)
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ntype 1
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
ntype 1
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
| `pw_diag_thr` | `1.0e-5` | **Mandatory for `basis_type pw` NSCF.** Default 0.01 is too loose — eigenvalues will have 10–100 meV noise, making band gaps and SOC splittings unreliable. |

> **⚠ PW NSCF precision**: `pw_diag_thr` is NOT optional for PW band/DOS. Without it, SOC splittings, band gaps, and DOS peaks are quantitatively wrong. Always include `pw_diag_thr 1.0e-5` (or tighter) in any `basis_type pw` NSCF INPUT.

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
ntype 1
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

DOS KPT — **dense uniform mesh** (NOT line-mode, minimum 8×8×8 for bulk):
```
K_POINTS
0
Gamma
12 12 12 0 0 0
```
> Even for expensive functionals (HSE, PBE0), the DOS k-mesh must remain dense. A sparse SCF mesh is acceptable to save cost, but the DOS NSCF step requires a dense mesh for smooth spectra — never copy the SCF mesh to DOS.

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
- In-plane: dense mesh. **Minimum `12 12` for metals** (hard floor — do not use less); `20 20` for production-quality surface energy.
- Vacuum direction: **always `1`**. Never more than 1 k-point.
- `kspacing` mode: `kspacing 0.10 0.10 1.00` (slab, z=vacuum). Bulk: `kspacing 0.10`.

---

## Multi-File Consistency Rules

When generating multiple INPUT files for a comparative study (surface energy, vacancy formation, EOS, etc.):

1. **All INPUT files must share identical**: `basis_type`, `ecutwfc`, `smearing_method`, `smearing_sigma`, `scf_thr`. Use exactly the same values — do not vary these between bulk and slab.
2. **Each INPUT must reference its STRU and KPT files**: add `stru_file <name>` and `kpoint_file <name>` **in every INPUT file**. This is mandatory whenever the workspace contains multiple STRU/KPT files or uses non-default names. ABACUS defaults to looking for files named `STRU` and `KPT` — if your files are named differently (e.g. `bulk.stru`, `KPT_slab`), ABACUS will fail silently.
3. **Task-specific mandatory params still apply**: a `cell-relax` INPUT inside a multi-file set still needs `cal_force 1`, `cal_stress 1`, `force_thr_ev`, `stress_thr`, `relax_nmax`. A `relax` INPUT still needs `cal_force 1`, `force_thr_ev`, `relax_nmax`. **These are NEVER implied by `calculation`** — you must write them explicitly.
4. **Recommended standard values** for consistency: `scf_thr 1.0e-7`, `smearing_method gauss`, `smearing_sigma 0.01`.

## Low-Cost / Benchmark Mode Guidance (PW)

When a task requests "low-cost", "benchmark", or "minimal cost" parameters:

- **`ecutwfc`**: reduce significantly from the production default (e.g. use roughly 1/3 to 1/2 of the standard 50 Ry). The exact value depends on the pseudopotential.
- **k-points**: reduce density or use Gamma-only where the task allows.
- **Convergence thresholds** (`scf_thr`, `stress_thr`, `force_thr_ev`): keep at physically reasonable values — "low-cost" means cheaper basis, not looser convergence.
- **`relax_nmax`**: keep ≥ 50 to ensure the optimizer has enough steps.

---

## File Reference Rule — CRITICAL

**Every non-default filename must be explicitly referenced in INPUT.** Common mistakes:
- ❌ STRU file is `mo_bulk.stru` but INPUT has no `stru_file` → ABACUS looks for `STRU`, fails
- ❌ KPT file is `KPT_slab` but INPUT has no `kpoint_file` → ABACUS looks for `KPT`, fails
- ❌ Two-step workflow: created `KPT_band` for NSCF but forgot to create `KPT_scf` for SCF → SCF INPUT references `kpoint_file KPT_scf` which doesn't exist
- ✅ Always: `stru_file <exact_filename>` and `kpoint_file <exact_filename>` in every INPUT

---

## Common Mistakes Checklist

Before finalizing any INPUT file, verify none of these apply:

- ❌ Missing `ntype` in INPUT → must equal STRU ATOMIC_SPECIES count (validator will reject)
- ❌ `cell-relax` without `cal_force 1` → optimizer has no forces, **silently broken** (most common error)
- ❌ `cell-relax` without `cal_stress 1` → cell vectors not optimized
- ❌ `relax` without `cal_force 1` → same problem, forces not computed
- ❌ Using `force_thr` (Ry/Bohr) instead of `force_thr_ev` (eV/Å) → wrong units
- ❌ SCF feeding NSCF but missing `out_chg 1` → NSCF fails to read charge
- ❌ NSCF with `symmetry 1` → k-path folded, wrong band plot
- ❌ Slab KPT with >1 in vacuum direction → wasted computation, wrong physics
- ❌ Multi-file set with inconsistent `ecutwfc` or `smearing_sigma` → invalidates energy differences
- ❌ **STRU/KPT file named non-default but INPUT missing `stru_file`/`kpoint_file`** → ABACUS looks for `STRU`/`KPT`, fails silently
- ❌ **Two-step workflow with only one KPT file** → SCF needs uniform mesh KPT, NSCF needs line-mode KPT; must create both
- ❌ **INPUT references a file that doesn't exist** → always list workspace files and verify every referenced filename exists
