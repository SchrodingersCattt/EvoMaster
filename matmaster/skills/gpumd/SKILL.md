---
name: gpumd
description: "Use this skill for GPUMD and NEP tasks: MD, EMD/HNEMD/NEMD, diffusion/MSD, SHC, NEP training or prediction."
skill_type: operator
---

# GPUMD Skill

GPUMD is a GPU-native MD package with two executables:
- `gpumd`: run simulations via `run.in`
- `nep`: train/infer NEP models via `nep.in`

## Minimum Workflow

1. Read the task; identify which simulation type is needed
2. Load this skill; consult `references/run_in_keywords.md` or `references/nep_in_keywords.md`
3. Write `run.in` (or `nep.in`) following the two-stage pattern below
4. Stage all input files (`run.in`, `model.xyz`, potential files) in `input_dir/`
5. Submit to Bohrium

## Hard Guards

1. **`potential` must come first.** Every `run.in` must start with `potential` line(s) before any `ensemble`, `run`, or `compute_*`.
2. **Two-stage pattern: equilibrate then produce.** Equilibration (NVT/NPT) → production (NVE for transport, or target ensemble). Separate with distinct `run` blocks.
3. **`compute_*` before its `run`.** Any `compute_hac`, `compute_hnemd`, `compute_shc`, `compute_msd`, `compute_sdc`, `compute_viscosity`, `compute_dos` must appear before the `run` command in the same block.
4. **`dump_*` before its `run`.** Same rule for `dump_thermo`, `dump_position`, `dump_force`, `dump_dipole`, `dump_polarizability`, `dump_observer`.
5. **NVE for equilibrium transport properties.** EMD (`compute_hac`), MSD (`compute_msd`), DOS (`compute_dos`), SHC (`compute_shc`), viscosity (`compute_viscosity`) require `ensemble nve` in the production stage. Exception: HNEMD (`compute_hnemd`) uses NVT; NEMD source-sink (`heat_nhc`/`heat_lan`/`heat_bdp`) uses its own thermostatted ensemble.
6. **NEP: `type` line must list actual species.** `type N El1 El2 ...` where N = number of species, matching `train.xyz` data.
7. **`model.xyz` must use extended XYZ format.** Header line 2 must contain `lattice="..."` (9 floats, row-major) and `pbc="T T T"`. See `references/model_xyz_format.md`.
8. **Group columns required for group-based keywords.** If using `source`/`sink` in NEMD ensembles, `compute_temperature group_method`, or group-filtered `compute_*`/`dump_*`, the `model.xyz` must define group columns in Properties.
9. **`compute_*` and `dump_*` reset after each `run`.** If a second `run` block needs the same compute/dump, re-specify them before that `run`.

## Default Potential

When no task-specific NEP potential is provided:

**Quick demos (preferred)** — use a small element-specific potential from the GPUMD examples (~50 KB, downloads instantly):

```bash
# PbTe example potential
curl -fsSL -o nep.txt https://raw.githubusercontent.com/brucefan1983/GPUMD/master/examples/nep_prediction/nep.txt
```

Other small potentials in the repo:
- `potentials/nep/C_2024_NEP4.txt` — Carbon
- `potentials/nep/Si_2022_NEP3_5body.txt` — Silicon

**Universal NEP89** (~15 MB, covers 89 elements):

```bash
curl -fsSL -o nep.txt https://matmaster-test.oss-cn-zhangjiakou.aliyuncs.com/gpumd/potentials/nep89_20250409.txt
```

This is hosted on internal OSS and downloads fast from Bohrium nodes.

Browse all available potentials: https://gpumd.cn/database.html

## Bohrium Submission Defaults

| Item | Default Value |
|------|---------------|
| image | `registry.dp.tech/dptech/dp/native/hub/mrdic2/gpumd:1.0.2-1777991160` |
| machine | `c16_m64_1 * NVIDIA 4090` |
| cmd (gpumd) | `gpumd > log 2>&1` |
| cmd (nep) | `nep > log 2>&1` |

## `nep.in` Default Tags (Quick Baseline)

Use this baseline when the task does not override settings.
`type` is mandatory and species names must match the actual dataset.

```text
type          2 Te Pb # this is a mandatory keyword
version       4       # default
cutoff        8 4     # default
n_max         6 6     # default
basis_size    6 6     # default
l_max         4 2 0   # default
neuron        30      # default
lambda_e      1.0     # default
lambda_f      1.0     # default
lambda_v      0.1     # default
batch         1000    # default
population    50      # default
generation    100000  # default
```

## References (read on demand)

- `references/run_in_keywords.md` — complete keyword reference for `run.in`
- `references/nep_in_keywords.md` — NEP training parameter reference for `nep.in`
- `references/input_examples.md` — worked examples for common simulation types
- `references/model_xyz_format.md` — model.xyz extended XYZ format, group definitions
