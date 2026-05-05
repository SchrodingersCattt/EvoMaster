---
name: gpumd
description: "Use this skill for GPUMD and NEP tasks: MD, EMD/HNEMD/NEMD, diffusion/MSD, SHC, NEP training or prediction."
skill_type: operator
---

# GPUMD Skill

GPUMD (Graphics Processing Units Molecular Dynamics) is a GPU-native MD package. Per official docs, workflow has two interfaces:
- `gpumd` executable: run atomistic simulations using `run.in`.
- `nep` executable: train/infer NEP models using `nep.in`.
- For NEMD workflows, use `ensemble heat_lan` to set up heat source/sink control.

## Bohrium Submission Config

| Item | Default Value |
|------|---------------|
| image | `registry.dp.tech/dptech/dp/native/hub/mrdic2/gpumd:1.0.2-1777991160` |
| machine | `c16_m64_1 * NVIDIA 4090` (prefer V100/4090 class) |
| cmd (gpumd) | `gpumd > log 2>&1` |
| cmd (nep) | `nep > log 2>&1` |

> `gpumd` reads `run.in`; `nep` reads `nep.in` from the working directory.
> 通用 `nep89` 势函数在 `GPUMD/potentials/nep/nep89_20250409/`。

## Reference

- Main docs: `https://gpumd.org/`
- `run.in` protocol: `https://gpumd.org/gpumd/input_files/run_in.html`
- `nep.in` format: `https://gpumd.org/nep/input_files/nep_in.html`

## `nep.in` Default Tags (Quick Baseline)

Use this as a default baseline when the task does not override settings.  
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
