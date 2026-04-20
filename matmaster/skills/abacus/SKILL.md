---
name: abacus
description: "MUST use this skill for ANY task involving ABACUS (input preparation, parameter configuration, SCF, band structure, DOS, geometry/cell relaxation, MD, electric field, dipole correction, BSSE, electrostatic potential, etc.)."
skill_type: operator
---

# ABACUS Skill

ABACUS supports plane-wave (PW) and numerical atomic orbital (LCAO) basis sets.

**Action rule**: When generating ABACUS input files, **always use Write tool**. **Read any provided STRU first** to extract: (1) PP/orbital filenames — reuse exactly, never invent; (2) ntype from species count; (3) basis_type from NUMERICAL_ORBITAL presence; (4) coordinate type and geometry (detect slab vs bulk by vacuum gap). Then Write all output files.

## Bohrium Submission

| Item | Default |
|------|---------|
| image | `registry.dp.tech/dptech/abacus:LTSv3.10.1` |
| machine | `c32_m128_cpu` |
| cmd | `OMP_NUM_THREADS=1 mpirun -np 16 abacus > log 2>&1` |

> `-np` = **half CPU cores** (32 → 16). GPU: `c8_m60_1 * NVIDIA 4090` with `basis_type pw`.

## K-point Strategy

| Scenario | Use `kspacing` in INPUT | Use KPT file |
|----------|:-----------------------:|:------------:|
| Supercell (vacancy, defect, BSSE) | ✅ **mandatory** | ✗ |
| Standard bulk | optional | ✅ |
| Slab | ✅ recommended | ✅ |
| Band structure (k-path) | ✗ | ✅ (line-mode) |

**Supercell rule**: Always use `kspacing` (e.g. `0.10 0.10 0.10`) inside INPUT. For slabs: `kspacing 0.10 0.10 1.00` (vacuum direction = 1.00).

## Input Files

Three files: **INPUT** (parameters), **STRU** (structure), **KPT** (k-points; optional with `kspacing`).

**INPUT** format: `keyword value` with single space. See `references/input_examples.md` for templates.

**STRU** format: See `references/stru_format.md`. Key: `LATTICE_CONSTANT 1.8897259886` (1 Å in Bohr), species order must match ATOMIC_SPECIES.

**KPT**: Must start with `K_POINTS`. Use `Gamma` mesh for uniform sampling, line-mode for bands.

### Recommended generation
```bash
uv run python scripts/render_input.py --software abacus --task scf --output INPUT
uv run python scripts/diagnose_input.py --software abacus --input INPUT
```

## Task Types

| Task | `calculation` value | Notes |
|------|-------------------|-------|
| scf | `scf` | Single-point energy |
| band | `nscf` | Needs prior SCF charge density |
| dos | `nscf` | Needs prior SCF charge density |
| relax | `relax` | Atomic relaxation |
| cell_relax | `cell-relax` | Full cell + position relaxation |
| md | `md` | NVT molecular dynamics |

## Mandatory Parameters Per Task

| Task | MUST-ADD (beyond baseline) |
|------|---------------------------|
| **relax** | `cal_force 1`, `force_thr_ev 0.01`, `relax_nmax 100` |
| **cell-relax** | `cal_force 1`, `cal_stress 1`, `force_thr_ev 0.01`, `stress_thr 0.5`, `relax_nmax 100` |
| **SCF → NSCF** | SCF: `out_chg 1`; NSCF: `init_chg file`, `symmetry 0`, `nbands <N>`, `out_band 1` or `out_dos 1` |
| **work function** | `out_pot 2` |
| **dipole correction** | `efield_flag 1`, `dip_cor_flag 1`, `efield_dir <vacuum>`, `efield_pos_max`, `efield_pos_dec`, `efield_amp 0.0` |
| **spin-polarized** | `nspin 2`, `mixing_beta 0.1`, `mixing_ndim 20`, `mixing_gg0 1.5` |
| **supercell/vacancy/BSSE** | `kspacing 0.10` inside INPUT |

> Use **`force_thr_ev`** (eV/Å), not `force_thr` (Ry/Bohr). Before writing INPUT, consult `references/input_examples.md`.

## Band/DOS Two-Step Workflow

SCF (`out_chg 1`) → NSCF (`init_chg file`, `symmetry 0`, `nbands`, `out_band 1` or `out_dos 1`). For Bohrium: write `run.sh` that runs both steps sequentially. Details in `references/input_examples.md`.

## Electric Field & Dipole Correction

See `references/electric_field.md`. Key: dipole correction = `efield_flag 1` + `dip_cor_flag 1` + `efield_amp 0.0`. Work function: `out_pot 2`.

## STRU Pre-flight Checklist

1. PP filenames match actual files (`ls *.upf`)
2. NUMERICAL_ORBITAL entries match `.orb` files (LCAO only)
3. `LATTICE_CONSTANT 1.8897259886`
4. ATOMIC_POSITIONS species order = ATOMIC_SPECIES order
5. Total atom count matches expected composition

Full STRU format details: `references/stru_format.md`.

## Required Files

- **Pseudopotentials** (`.upf`) and **Orbitals** (`.orb`, LCAO): Download from AIS Square:
  ```bash
  wget -q "https://store.aissquare.com/datasets/dc875646-a526-41f1-a180-d54b218fc80a/ABACUS-APNS-PPORBs-v1.zip" && unzip -qo ABACUS-APNS-PPORBs-v1.zip
  cp apns-pseudopotentials-v1/Si.upf .
  cp apns-orbitals-efficiency-v1/Si_gga_7au_100Ry_2s2p1d.orb .
  ```

## Output Files

See `references/output_params.md` for output file list and grep patterns.

## Submission Workflow

1. Prepare STRU (or convert from CIF/POSCAR)
2. Download PP + orbital files from AIS Square
3. Generate INPUT via `render_input.py`
4. Prepare KPT (or use `kspacing`)
5. Diagnose via `diagnose_input.py`
6. Submit via Bohrium tool
7. Poll and read results
