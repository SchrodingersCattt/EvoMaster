# ABACUS Advanced Task Templates

Concrete multi-file generation templates for surface energy, vacancy formation,
EOS, and other comparative studies. Use alongside `input_examples.md` for
parameter details.

---

## Basis Type Detection — CRITICAL

**Before generating any INPUT, inspect the STRU file(s):**

- If STRU contains a `NUMERICAL_ORBITAL` section (listing `.orb` files) →
  **`basis_type lcao`**, `ecutwfc 100`
- If STRU has NO `NUMERICAL_ORBITAL` section → **`basis_type pw`**, `ecutwfc 50`
  (or higher if PP requires)

**This is the single most important decision.** Using `pw` when orbitals are
present wastes the orbital basis. Using `lcao` without orbitals crashes ABACUS.

---

## Surface Energy (Bulk + Multiple Slabs)

**Goal**: Generate INPUT/KPT files for bulk reference and 2+ slab thicknesses.

### Required files (example: Al FCC(100))

| File | `calculation` | Key params | STRU ref |
|------|--------------|------------|----------|
| `INPUT_bulk_relax` | `cell-relax` | `cal_force 1`, `cal_stress 1`, `stress_thr 0.5`, `relax_nmax 100` | bulk STRU |
| `INPUT_slab5` | `relax` | `cal_force 1`, `force_thr_ev 0.01`, `relax_nmax 100` | 5-layer slab STRU |
| `INPUT_slab7` | `relax` | `cal_force 1`, `force_thr_ev 0.01`, `relax_nmax 100` | 7-layer slab STRU |
| `KPT_bulk` | — | Dense 3D (e.g. `8 8 8 0 0 0`) | — |
| `KPT_slab` | — | Dense in-plane, 1 in vacuum (e.g. `8 8 1 0 0 0`) | — |

### Mandatory consistency rules

All INPUT files **must** share identical values for:
- `basis_type` (detect from STRU!)
- `ecutwfc` (100 for LCAO, 50+ for PW)
- `smearing_method` and `smearing_sigma`
- `scf_thr`

### Every INPUT must include:
- `stru_file <exact_filename>` — point to the correct STRU
- `kpoint_file <exact_filename>` — point to the correct KPT
- `ntype <N>` matching species count in referenced STRU

### KPT design:
- Bulk: dense 3D uniform mesh (e.g. `8 8 8` for FCC metals)
- Slab: same in-plane density as bulk, **always 1 in vacuum direction**
- Both slabs share the same KPT file if their in-plane lattice is identical

### STRU preservation:
- **Do NOT modify** the provided STRU files
- Preserve all atomic position freeze/relax flags (`0 0 0` / `1 1 1`) as given
- Do NOT rewrite or "clean up" STRU files

---

## Vacancy Formation Energy (Bulk + Clean Slab + Vacancy Slab)

**Goal**: Generate INPUT/KPT for bulk reference, pristine surface, and surface
with vacancy removed.

### Required files (example: Mo BCC(110))

| File | `calculation` | Key params | STRU ref |
|------|--------------|------------|----------|
| `INPUT_bulk` | `cell-relax` | `cal_force 1`, `cal_stress 1`, `stress_thr 0.5` | bulk STRU |
| `INPUT_slab_clean` | `relax` or `scf` | `cal_force 1` (if relax) | pristine slab STRU |
| `INPUT_slab_vac` | `relax` or `scf` | `cal_force 1` (if relax) | vacancy slab STRU |
| KPT file(s) | — | Consistent k-mesh or `kspacing` | — |

### Key requirements:
- Same consistency rules as surface energy (basis_type, ecutwfc, smearing, etc.)
- If the system is magnetic (e.g. Fe, Co, Ni, Mn, Cr): add `nspin 2` and
  mixing parameters (`mixing_beta 0.1`, `mixing_ndim 20`, `mixing_gg0 1.5`)
- For vacancy supercells: prefer `kspacing` in INPUT over fixed KPT mesh
- Every INPUT must reference its STRU and KPT via `stru_file` / `kpoint_file`
- Slab KPT: 1 in vacuum direction

### Formula:
```
E_vac = E(slab_vac) - E(slab_clean) + E(bulk) / N_bulk
```

---

## Common Multi-File Pitfalls

1. **Wrong basis_type**: Always check STRU for `NUMERICAL_ORBITAL` section first
2. **Inconsistent ecutwfc**: All files must use the same value
3. **Missing stru_file/kpoint_file**: ABACUS defaults to `STRU`/`KPT` — if your
   files have different names, the job fails silently
4. **Missing cal_force/cal_stress**: NEVER implied by `calculation` type
5. **Slab KPT with >1 in vacuum**: Wastes compute and can give wrong physics
6. **Modifying provided STRU files**: Preserve freeze/relax flags exactly as given
