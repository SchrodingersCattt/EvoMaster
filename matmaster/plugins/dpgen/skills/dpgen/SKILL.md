---
name: dpgen
description: "Use for DP-GEN active-learning dataset workflows: param.json/machine.json generation, repair, validation, init-data planning, and train-explore-label-retrain setup. Not for pretrained MLIP inference; use `mlips`."
---

# DP-GEN Skill

DP-GEN builds Deep Potential / DeePMD training workflows through active learning: train, explore, label, merge data, and retrain.

## Scope

Use for DP-GEN `param.json` / `machine.json`, DeePMD training input setup, model-deviation loops, LAMMPS exploration, CP2K/VASP/QE/ABACUS FP labeling, and launch-readiness triage. For one-off inference with pretrained DPA/MACE/SevenNet/MatterSim potentials, use `mlips`.

## Reference templates

Use real example files, not guessed top-level names:

- `param.json` directory: <https://github.com/deepmodeling/dpgen/tree/master/examples/run>
- `machine.json` directory: <https://github.com/deepmodeling/dpgen/tree/master/examples/machine/DeePMD-kit-2.x>
- Raw param example: <https://raw.githubusercontent.com/deepmodeling/dpgen/master/examples/run/dp2.x-lammps-vasp/param_CH4_deepmd-kit-2.0.1.json>
- Raw machine example: <https://raw.githubusercontent.com/deepmodeling/dpgen/master/examples/machine/DeePMD-kit-2.x/lebesgue_v2_machine.json>

Notes: `examples/run/param.json` and `examples/machine/.../machine.json` do not exist. `lebesgue_v2_machine.json` may use old `DpCloudServer` / `DpCloudServerContext`; Bohrium setups typically need `Bohrium` / `BohriumContext`.

## From-scratch DP-GEN package checklist

For a new project, prepare more than two JSON files:

- Build or collect initial structures with composition, cell, density, and provenance.
- Generate initial labels by DFT/AIMD or single-point FP jobs, then convert with `dpdata` to DeePMD `npy` format.
- `init_data_sys` entries should point to DeePMD data dirs, not flat POSCAR files. Typical files: `type.raw`, `type_map.raw`, `set.000/box.npy`, `coord.npy`, `energy.npy`, `force.npy`.
- `sys_configs` are exploration seeds; keep them separate from labeled `init_data_sys`.
- Prefer absolute paths for launchable packages. `dpgen-lsp-tool` may resolve `init_data_sys` relatives against the `param.json` directory rather than `init_data_prefix`.

## Parameter adaptation guidance

Template values are not chemistry-independent. Recheck `sel`, `rcut`, `neuron`, batch size, learning-rate schedule, and descriptor family for the target system. For example, water and dense molecular systems should not inherit CH4 settings blindly.

## Validation gate

Before saying inputs are ready, run:

```bash
dpgen-lsp-tool check param.json --fail-on-blocking
dpgen-lsp-tool check machine.json --fail-on-blocking
```

Report `commands`, `files_checked`, `tool_available`, `diagnostics`, `expected_path_warnings_ignored`, `blocking_findings`, `readiness`, and `reason`. Placeholder paths such as `/path/to/...` or `path/to/xx` are expected warnings unless they block the requested launch state.

## Installing the checker

Prefer pinned installs when a commit is known:

```bash
uv pip install git+https://github.com/SchrodingersCattt/dpgen-lsp.git@<commit-sha>
uv pip install git+https://github.com/SchrodingersCattt/dpgen-lsp.git@main
uv tool install git+https://github.com/SchrodingersCattt/dpgen-lsp.git@main
```

Useful inspection commands:

```bash
dpgen-lsp-tool capabilities
dpgen-lsp-tool context param.json --line 1 --character 1
dpgen-lsp-tool hover param.json --line 1 --character 1
dpgen-lsp-tool complete param.json --line 1 --character 1
dpgen-lsp-tool symbols param.json
dpgen-lsp-tool fix param.json --line 1 --character 1
```

`fix` is advisory/preview only. Do not blindly auto-apply a fix without preserving the user’s scientific intent.

## Repair rules

1. Validate first and identify the smallest blocking issue.
2. Fix syntax errors with minimal edits.
3. Preserve scientific settings unless the user explicitly asks to redesign them.
4. Re-check the repaired file if the checker is available.
5. Separate syntax errors from schema/semantic issues and placeholder path warnings.

## Workflow planning rules

- Identify the target chemistry, temperature and pressure ranges, exploration engine, FP engine, and descriptor/training family from the user’s request.
- For LAMMPS exploration + CP2K FP workflows, ensure the plan includes model-deviation selection, FP labeling queue, data merge, and retraining gates.
- If the user asks for an init dataset plan, specify structure classes, coverage targets, labeling method, and minimum metadata required, but do not invent unavailable datasets.
- For DFT settings from an external paper, use only provided excerpts or cite that details must be supplied; do not fabricate exact CP2K parameters from memory.
