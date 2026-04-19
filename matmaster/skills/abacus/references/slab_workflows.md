# ABACUS Slab Workflows — Work Function, Surface Energy, Vacancy Formation

## ⚠ Parameter Lockdown — ALWAYS Applies

**Even when a task says "low-cost", "benchmark", "quick test", or "Gamma-only"**, the following baseline parameters are **NEVER reduced**:

| Parameter | Mandatory Value | Why |
|-----------|----------------|-----|
| `ecutwfc` | **100** | Values below 100 Ry invalidate energy comparisons; energy cutoff error is NOT "low-cost" |
| `scf_thr` | **1.0e-7** | Looser thresholds produce unconverged forces/stress and meaningless energy differences |
| `smearing_sigma` | **0.01** | Larger values smear out fine electronic structure; not recoverable post-hoc |
| `smearing_method` | **gauss** | Consistency across all calculations in a comparative study |

"Low-cost" means: fewer k-points (e.g., Gamma only), smaller cell, fewer layers — **NOT** reduced accuracy parameters.

---

## Work Function / Electrostatic Potential Workflow

**Dipole correction is MANDATORY for ANY slab electrostatic potential calculation.** Without it, the periodic boundary condition creates a fictitious electric field across the vacuum, making the vacuum level undefined.

### Required Parameters (ALL must appear in INPUT)

```
out_pot 2
efield_flag 1
dip_cor_flag 1
efield_dir 2          # 0=x, 1=y, 2=z — set to vacuum direction
efield_pos_max 0.0    # or 0.95 — position of sawtooth in vacuum
efield_pos_dec 0.1    # decay width
efield_amp 0.0        # zero = pure dipole correction, no external field
```

### Checklist
- [ ] `out_pot 2` present (outputs `ElecStaticPot.cube`)
- [ ] `efield_flag 1` + `dip_cor_flag 1` + `efield_amp 0.0` — ALL THREE present
- [ ] `efield_dir` matches the vacuum direction (usually 2 for z)
- [ ] KPT: dense in-plane (≥ 12×12 for metals), exactly 1 in vacuum direction
- [ ] Vacuum gap ≥ 20 Å (work function requires large vacuum)
- [ ] `ecutwfc 100`, `scf_thr 1.0e-7`, `smearing_sigma 0.01` — no exceptions

### Common Failure Mode
> Agent omits dipole correction when task only mentions "electrostatic potential" without explicitly saying "work function" or "dipole correction". **Rule: if the system is a slab AND you output the potential (`out_pot 2`), ALWAYS include dipole correction.**

---

## Surface Energy Workflow (Bulk + Slab Relaxation)

Surface energy requires **at minimum 3 calculations** with **identical** accuracy parameters.

### File Set
| File | `calculation` | Purpose |
|------|--------------|---------|
| `INPUT_bulk_relax` | `cell-relax` | Equilibrium bulk energy per atom |
| `INPUT_slab<N>` | `relax` | Relaxed slab total energy |
| `KPT_bulk` | — | Dense 3D mesh |
| `KPT_slab` | — | Dense in-plane, 1 in vacuum |

### Mandatory Consistency Rules
1. **ALL INPUT files** share identical: `basis_type`, `ecutwfc 100`, `smearing_method gauss`, `smearing_sigma 0.01`, `scf_thr 1.0e-7`
2. **Bulk cell-relax** MUST have: `cal_force 1`, `cal_stress 1`, `force_thr_ev 0.01`, `stress_thr 0.5`, `relax_nmax 100`
3. **Slab relax** MUST have: `cal_force 1`, `force_thr_ev 0.01`, `relax_nmax 100`
4. **Every INPUT** must have explicit `stru_file` and `kpoint_file` directives

### K-Point Requirements for Surface Energy
| System | Bulk KPT | Slab KPT |
|--------|----------|----------|
| Metals (Al, Mo, Cu…) | ≥ 8×8×8 | ≥ 12×12×1 |
| Semiconductors | ≥ 6×6×6 | ≥ 8×8×1 |

**Match k-point density**: bulk kspacing ≈ slab in-plane kspacing. For Al (a=4.05 Å): bulk 8×8×8 → slab 12×12×1 (both ≈ 0.03 Å⁻¹).

---

## Vacancy Formation Energy Workflow

### File Set
| File | `calculation` | Purpose |
|------|--------------|---------|
| `INPUT_bulk` | `cell-relax` | Bulk reference energy per atom |
| `INPUT_slab_clean` | `scf` or `relax` | Pristine slab energy |
| `INPUT_slab_vac` | `scf` or `relax` | Slab with vacancy energy |
| `KPT` (shared or per-file) | — | Consistent across all |

### Formula
```
E_vac = E(slab_vac) - E(slab_clean) + E(bulk) / N_bulk
```

### Rules
1. Same consistency rules as surface energy (identical baseline parameters)
2. Clean and vacancy slabs use **identical cells** — same lattice vectors, same KPT
3. For supercell-based vacancies, prefer `kspacing` in INPUT
4. **Never use `ecutwfc 50`** or `scf_thr 1.0e-6` for "benchmark" calculations — these are NOT valid shortcuts

---

## 1D/2D System Workflows (Nanoribbon, Nanotube)

### K-Point Strategy for Low-Dimensional Systems
| Dimensionality | Periodic direction(s) | K-mesh pattern |
|---------------|----------------------|----------------|
| 1D (nanoribbon) | One direction (e.g., y) | `1 N 1` (N ≥ 12) |
| 2D (slab, 2D material) | Two directions (e.g., x, y) | `N N 1` (N ≥ 8) |

For the **non-periodic** directions, ALWAYS use exactly 1 k-point.

### Electric Field on 1D/2D Systems
When applying an electric field to a nanoribbon/nanotube:
1. Identify which direction is periodic (from STRU lattice vectors — shortest = periodic)
2. `efield_dir` = vacuum direction where field is applied (NOT the periodic direction)
3. Include `dip_cor_flag 1` if `efield_flag 1` is set
4. `efield_pos_max` and `efield_pos_dec`: place sawtooth in vacuum, away from atoms

### Unit Conversion for Electric Field
- ABACUS `efield_amp` is in **atomic units** (1 a.u. = 51.4220632 V/Å)
- To convert V/Å → a.u.: divide by 51.4220632
- Example: 0.1 V/Å = 0.1 / 51.4220632 ≈ 0.001944 a.u.
