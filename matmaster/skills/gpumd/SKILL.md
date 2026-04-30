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
5. **NVE for transport properties.** EMD (`compute_hac`), MSD (`compute_msd`), SHC (`compute_shc`), viscosity (`compute_viscosity`) require `ensemble nve` in the production stage.
6. **NEP: `type` line must list actual species.** `type N El1 El2 ...` where N = number of species, matching `train.xyz` data.

## Bohrium Submission Defaults

| Item | Default Value |
|------|---------------|
| image | `registry.dp.tech/dptech/dp/native/hub/mrdic2/gpumd:1.0.1-1777451100` |
| machine | `c16_m64_1 * NVIDIA 4090` |
| cmd (gpumd) | `gpumd > log 2>&1` |
| cmd (nep) | `nep > log 2>&1` |

## References (read on demand)

- `references/run_in_keywords.md` — complete keyword reference for `run.in`
- `references/nep_in_keywords.md` — NEP training parameter reference for `nep.in`
- `references/input_examples.md` — worked examples for common simulation types
