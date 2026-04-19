# DFT Best Practices — Comprehensive Evaluation Checklist

Use this reference when evaluating DFT input files or computational workflows. Check **every applicable category** systematically.

---

## 1. Basis Set / Cutoff Energy

### Plane-Wave Codes (ABACUS PW, QE, ABINIT)
| Parameter | Minimum Standard | Notes |
|-----------|-----------------|-------|
| `ecutwfc` (Ry) | **100 Ry** for ABACUS; 40-60 Ry for QE | Too low → inaccurate forces/energies |
| Convergence test | Should test at 2-3 cutoff values | Energy should converge to <1 meV/atom |

### LCAO Codes (ABACUS LCAO)
| Parameter | Standard | Notes |
|-----------|----------|-------|
| `ecutwfc` | **100 Ry** even for LCAO | Controls auxiliary PW grid |
| Orbital quality | Match system (efficiency vs. full) | Check orbital filename matches element |

### Common Issues
- ❌ Using `ecutwfc 50` for ABACUS (too low for both PW and LCAO)
- ❌ Mixing orbital quality levels across elements
- ✅ `ecutwfc 100` is the ABACUS standard for both basis types

---

## 2. K-Point Sampling

### Bulk Systems
| System Type | Minimum k-mesh | kspacing equivalent |
|-------------|---------------|---------------------|
| Metal | 12×12×12 or denser | ≤ 0.10 Å⁻¹ |
| Semiconductor/Insulator | 8×8×8 | ≤ 0.15 Å⁻¹ |
| Large cell (>15 Å) | Gamma-only OK | kspacing auto-adjusts |

### Slab / Surface
| Direction | Requirement | Notes |
|-----------|-------------|-------|
| In-plane | Dense (≥12×12 for metals) | Same density as bulk |
| Vacuum | **Always 1** | More than 1 wastes compute, wrong physics |

### Supercell / Defect / Vacancy
| Rule | Details |
|------|---------|
| Use `kspacing` in INPUT | NOT fixed KPT mesh — must adapt to cell size |
| Standard value | `0.10` Å⁻¹ for metals; `0.12`–`0.15` for insulators |

### Common Issues
- ❌ Dense k-mesh in vacuum direction for slab calculations
- ❌ Fixed KPT file for supercell calculations (should use `kspacing`)
- ❌ Too sparse k-mesh for metals (need ≤ 0.10 Å⁻¹)
- ❌ Using Gamma-only for small metallic cells
- ✅ `kspacing 0.10 0.10 1.00` for metallic slabs (vacuum in z)

---

## 3. SCF Convergence

| Parameter | Standard | Too Loose | Too Tight |
|-----------|----------|-----------|-----------|
| `scf_thr` (ABACUS) | **1.0e-7** | >1.0e-6 | <1.0e-9 (wasteful) |
| `scf_nmax` | **100** (SCF), **300** (NSCF) | <50 (may not converge) | >500 (problem elsewhere) |
| `conv_thr` (QE) | 1.0e-8 Ry | >1.0e-6 | — |

### Spin-Polarized Systems
| Parameter | Standard | Notes |
|-----------|----------|-------|
| `mixing_beta` | **0.1** (magnetic systems) | Default 0.7 causes divergence for magnets |
| `mixing_ndim` | **20** | Larger history helps convergence |
| `mixing_gg0` | **1.5** | Helps with charge sloshing |
| `nspin` | **2** | Required for any magnetic system |

### Common Issues
- ❌ Default `mixing_beta` for magnetic systems → SCF divergence
- ❌ Missing `nspin 2` for systems with magnetic elements (Fe, Co, Ni, Mn, Cr)
- ❌ `scf_nmax 200` when 100 is standard (suggests convergence problem)
- ✅ Tight `mixing_beta 0.1` + `mixing_ndim 20` for spin-polarized

---

## 4. Smearing

| Parameter | Standard | Notes |
|-----------|----------|-------|
| `smearing_method` | **gauss** (Gaussian) | Methfessel-Paxton also acceptable |
| `smearing_sigma` | **0.01** Ry | Metals: 0.01; Insulators: 0.001-0.01 |

### Common Issues
- ❌ Large `smearing_sigma` (>0.05) → artificial electronic temperature
- ❌ Missing smearing for metals → slow convergence
- ✅ `smearing_method gauss` + `smearing_sigma 0.01` is universal standard

---

## 5. Relaxation Parameters

### Atomic Relaxation (`calculation relax`)
| Parameter | Required? | Standard Value |
|-----------|-----------|---------------|
| `cal_force` | **MANDATORY** | **1** — NOT implied by `calculation relax` |
| `force_thr_ev` | **MANDATORY** | **0.01** eV/Å |
| `relax_nmax` | Recommended | **100** |

### Cell Relaxation (`calculation cell-relax`)
| Parameter | Required? | Standard Value |
|-----------|-----------|---------------|
| `cal_force` | **MANDATORY** | **1** |
| `cal_stress` | **MANDATORY** | **1** — NOT implied by `calculation cell-relax` |
| `force_thr_ev` | **MANDATORY** | **0.01** eV/Å |
| `stress_thr` | **MANDATORY** | **0.5** kbar |
| `relax_nmax` | Recommended | **100** |

### Critical Anti-Pattern
- ❌ **`calculation relax` without `cal_force 1`** → ABACUS runs but positions don't move (silent failure!)
- ❌ **`calculation cell-relax` without `cal_stress 1`** → atoms relax but cell stays fixed
- ❌ Using `force_thr` (Ry/Bohr) instead of `force_thr_ev` (eV/Å) — wrong units!
- ✅ Always explicitly include `cal_force 1` for any relaxation

---

## 6. Two-Step Workflows (Band / DOS)

### SCF Step Requirements
| Parameter | Required Value | Purpose |
|-----------|---------------|---------|
| `out_chg` | **1** | Write charge density for NSCF |
| KPT | Uniform Gamma mesh | Standard sampling |

### NSCF Step Requirements
| Parameter | Required Value | Purpose |
|-----------|---------------|---------|
| `init_chg` | **file** | Read SCF charge density |
| `symmetry` | **0** | MANDATORY for line-mode k-paths |
| `nbands` | integer > occupied | Must be explicit |
| `out_band` | **1** (band structure) | Output eigenvalues |
| `out_dos` | **1** (DOS) | Output density of states |
| KPT | Line-mode (band) or dense uniform (DOS) | Different from SCF KPT |

### Two-Step File Management
| Requirement | Details |
|-------------|---------|
| **Two KPT files** | `KPT_scf` (uniform) + `KPT_band`/`KPT_dos` (line-mode/dense) |
| **File references** | Each INPUT must have `kpoint_file <name>` pointing to its KPT |
| **run.sh** | Must chain SCF → NSCF in correct order |

### Common Issues
- ❌ Missing `out_chg 1` in SCF → NSCF reads no charge density
- ❌ `symmetry 1` in NSCF band structure → k-path folded incorrectly
- ❌ Only one KPT file for two-step workflow
- ❌ Missing `init_chg file` in NSCF → re-runs SCF from scratch
- ✅ Both INPUTs reference their own KPT with explicit `kpoint_file`

---

## 7. File Reference Consistency

| Rule | Details |
|------|---------|
| Non-default STRU name | INPUT **must** include `stru_file <actual_name>` |
| Non-default KPT name | INPUT **must** include `kpoint_file <actual_name>` |
| All referenced files exist | Every file named in INPUT/STRU must be in the workspace |
| Multi-file consistency | All INPUTs in a comparative study share identical `basis_type`, `ecutwfc`, `smearing_method`, `smearing_sigma`, `scf_thr` |

### Common Issues
- ❌ STRU file is `bulk.stru` but INPUT has no `stru_file` → ABACUS looks for `STRU`, fails
- ❌ Created `KPT_band` but forgot to create `KPT_scf` → SCF step fails
- ❌ Different `ecutwfc` across bulk and slab INPUTs → invalidates energy differences
- ✅ Always verify all referenced files exist with `ls`

---

## 8. Pseudopotential / Orbital Consistency

| Check | Standard |
|-------|----------|
| PP files match ATOMIC_SPECIES | Filename in STRU must match actual `.upf` file |
| Orbital files match NUMERICAL_ORBITAL | Filename in STRU must match actual `.orb` file (LCAO) |
| `ntype` matches species count | Count ALL species in ATOMIC_SPECIES (including ghost atoms) |
| `basis_type` matches orbital presence | `lcao` if orbitals present; `pw` if not |

### Common Issues
- ❌ `ntype` doesn't match number of species in STRU
- ❌ Missing orbital files for LCAO calculation
- ❌ `basis_type pw` but orbital files are referenced → confusion
- ✅ Count `.upf` lines in STRU and verify = `ntype` in INPUT

---

## 9. STRU File Quality

| Check | Standard |
|-------|----------|
| `LATTICE_CONSTANT` | **1.8897259886** (1 Å in Bohr) when vectors are in Å |
| Coordinate type | Consistent (`Direct`, `Cartesian_angstrom`, etc.) |
| Species order | ATOMIC_POSITIONS species order = ATOMIC_SPECIES order |
| Magnetic moments | Non-zero for magnetic species (Fe: ~2.5, Co: ~1.7, Ni: ~0.6) |
| Ghost atoms | Must have moment 0.0, mobility 0 0 0 |

---

## 10. Slab / Surface Calculations

| Check | Standard |
|-------|----------|
| Vacuum thickness | ≥ 15 Å (≥ 20 Å for work function/dipole) |
| K-points in vacuum dir | Always **1** |
| Dipole correction | Required for asymmetric slabs or work function |
| `efield_amp` | **0.0** for pure dipole correction (NOT finite field) |

---

## 11. Multi-Configuration Studies (Surface Energy, Vacancy, EOS)

| Rule | Details |
|------|---------|
| Consistent parameters | ALL INPUTs must share `basis_type`, `ecutwfc`, `smearing_*`, `scf_thr` |
| Individual file references | Each INPUT has its own `stru_file` and `kpoint_file` |
| Task-specific params still apply | `cell-relax` still needs `cal_force 1` + `cal_stress 1` even in a multi-file set |

---

## 12. Evaluation Report Template

When reporting a best-practice evaluation, use this structure:

```markdown
## Best Practice Evaluation Report

### Software & Task
- Software: [name and version]
- Task type: [SCF/relax/cell-relax/band/DOS/MD/...]
- System: [brief description]

### Critical Issues (Must Fix)
1. [Issue]: [Parameter] is [current value], should be [correct value].
   Reason: [why this causes failure or wrong results]

### Best Practice Violations (Should Fix)
1. [Issue]: [description and recommendation]

### Recommendations (Nice to Have)
1. [Suggestion for improvement]

### Correct Practices
1. [What the setup does well]

### Summary
- Critical issues: [count]
- Best practice violations: [count]
- Overall assessment: [pass/needs fixes/major revision]
```
