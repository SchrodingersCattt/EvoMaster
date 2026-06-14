---
name: gpumd
description: "Use to RUN GPUMD or NEP tasks (MD, EMD/HNEMD/NEMD thermal conductivity, diffusion/MSD, SHC, NEP training or prediction). DO NOT use for plotting pre-computed data (RDF / MSD / HAC from arbitrary sources go to data-analysis) or for other MD engines."
---

# GPUMD Skill

GPUMD is a GPU-native MD package with two executables:
- `gpumd`: run simulations via `run.in`
- `nep`: train/infer NEP models via `nep.in`

## Capability Gate

**STOP** if any of:
- Task requires LAMMPS, VASP, CP2K, or any non-GPUMD engine syntax
- Task is plotting/analyzing pre-computed data with no MD run needed (→ `data-analysis` skill)
- Task requires a potential type GPUMD doesn't support (only NEP and Tersoff-mini)

## Hard Guards

### Command Ordering

| Rule | Consequence if violated |
|------|------------------------|
| `potential` must be the first non-comment command | GPUMD segfaults or refuses to start |
| `compute_*` / `dump_*` before its `run` in the same block | Silently produces no output |
| `compute_*` / `dump_*` reset after each `run` — re-specify for subsequent blocks | Silently produces no output in later blocks |

### Ensemble Selection

| Compute / Task | Required Ensemble | Why |
|---------------|-------------------|-----|
| `compute_hac`, `compute_msd`, `compute_sdc`, `compute_viscosity`, `compute_dos` | `nve` | Thermostat corrupts correlation functions |
| `compute_shc` (standalone) | `nve` | Same |
| `compute_hnemd` + `compute_shc` | `nvt_nhc` | Thermostat absorbs driving-force heat |
| NEMD source/sink | `heat_nhc` / `heat_lan` / `heat_bdp` | Built-in thermostat on source/sink groups |
| Phonon (`compute_phonon`) | N/A (no `run`) | Exits after force-constant calculation |
| Thermal expansion / NPT properties | `npt_scr` or `npt_ber` | Need pressure coupling for volume sampling |

### Other Guards

- **Two-stage pattern**: equilibrate (NVT/NPT) → produce (target ensemble). Separate with distinct `run` blocks.
- **NEP `type` line must list actual species**: `type N El1 El2 ...` matching `train.xyz`.
- **`model.xyz` extended XYZ format**: header line 2 must have `lattice="..."` (9 floats) and `pbc="T T T"`. See `references/model_xyz_format.md`.
- **Group columns required** for NEMD `source`/`sink`, `compute_temperature group_method`, or group-filtered keywords. Define in `model.xyz` Properties.

## Workflow

1. Read the task; identify simulation type
2. Consult `references/run_in_keywords.md` (for `run.in`) or `references/nep_in_keywords.md` (for `nep.in`)
3. Write input file following two-stage pattern; for worked examples → `references/input_examples.md`
4. Stage all input files (`run.in`, `model.xyz`, potential files) in `input_dir/`
5. Submit to Bohrium (defaults below)

## Default Potential

When no task-specific NEP potential is provided:

| Potential | Coverage | Size | URL |
|-----------|----------|------|-----|
| Si_Fan_2019.txt | Si (Tersoff) | ~90 B | `https://raw.githubusercontent.com/brucefan1983/GPUMD/master/potentials/tersoff/Si_Fan_2019.txt` |
| C_2024_NEP4.txt | Carbon | ~50 KB | `https://raw.githubusercontent.com/brucefan1983/GPUMD/master/potentials/nep/C_2024_NEP4.txt` |
| NEP89 (universal) | 89 elements | ~15 MB | `https://matmaster-test.oss-cn-zhangjiakou.aliyuncs.com/gpumd/potentials/nep89_20250409.txt` |

Browse all: https://gpumd.cn/database.html

## Bohrium Submission Defaults

| Item | Default Value |
|------|---------------|
| image | `registry.dp.tech/dptech/matmaster:gpumd-1.0.2-1777991160` |
| machine | `c16_m64_1 * NVIDIA 4090` |
| cmd (gpumd) | `gpumd > log 2>&1` |
| cmd (nep) | `nep > log 2>&1` |

## `nep.in` Defaults

```text
type          2 Te Pb   # MANDATORY — must match train.xyz species
version       4
cutoff        8 4
n_max         6 6
basis_size    6 6
l_max         4 2 0
neuron        30
lambda_e      1.0
lambda_f      1.0
lambda_v      0.1
batch         1000
population    50
generation    100000
```
