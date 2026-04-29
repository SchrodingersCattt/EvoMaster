---
name: gpumd
description: "Use this skill for GPUMD and NEP tasks: MD, EMD/HNEMD/NEMD, diffusion/MSD, SHC, NEP training or prediction."
skill_type: operator
---

# GPUMD Skill

GPUMD (Graphics Processing Units Molecular Dynamics) is a GPU-native MD package. Per official docs, workflow has two interfaces:
- `gpumd` executable: run atomistic simulations using `run.in`.
- `nep` executable: train/infer NEP models using `nep.in`.

## Bohrium Submission Config

| Item | Default Value |
|------|---------------|
| image | `registry.dp.tech/dptech/dp/native/hub/mrdic2/gpumd:1.0.1-1777451100` |
| machine | `c16_m64_1 * NVIDIA 4090` (prefer V100/4090 class) |
| cmd (gpumd) | `gpumd > log 2>&1` |
| cmd (nep) | `nep > log 2>&1` |

> `gpumd` reads `run.in`; `nep` reads `nep.in` from the working directory.

## Reference

- Main docs: `https://gpumd.org/`
- `run.in` protocol: `https://gpumd.org/gpumd/input_files/run_in.html`
- `nep.in` format: `https://gpumd.org/nep/input_files/nep_in.html`
