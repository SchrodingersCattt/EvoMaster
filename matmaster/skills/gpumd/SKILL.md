---
name: gpumd
description: "Use this skill for GPUMD and NEP tasks: MD, EMD/HNEMD/NEMD, diffusion/MSD, SHC, NEP training or prediction."
skill_type: operator
---

# GPUMD Skill

GPUMD is a GPU-native molecular-dynamics package. Use the two official
executables consistently:

- `gpumd`: run MD simulations from `model.xyz` + `run.in`.
- `nep`: train NEP models from `nep.in` + `train.xyz` + `test.xyz`.

## Minimum Workflow

1. Identify the executable: `gpumd` for MD/analysis runs, `nep` for NEP training.
2. Read existing structure/data files before editing. Do not invent species,
   groups, lattice vectors, or training labels.
3. For `gpumd`, write `model.xyz`, `run.in`, and required potential files in the
   same run directory. For `nep`, write `nep.in` next to `train.xyz` and
   `test.xyz`.
4. Use the local references before relying on memory:
   `references/run_in_keywords.md`, `references/nep_in_keywords.md`,
   `references/input_examples.md`, `references/model_xyz_format.md`, and the
   task-specific references listed at the end of this file.
5. If a task asks for a Bohrium job, stage the complete input directory and use
   the defaults below unless the user specifies another image/machine.

## Hard Guards

1. `run.in` is sequential. Put all `potential` lines before any `velocity`,
   `ensemble`, `compute_*`, `dump_*`, or `run` command.
2. Use separate blocks: equilibrate first, then production. A new `ensemble`
   line starts a new block; each block should end with `run N`.
3. Every `compute_*` and `dump_*` must appear before the `run` it belongs to.
   They reset after each `run`, so re-specify them in later blocks.
4. Equilibrium transport production uses `ensemble nve`: EMD (`compute_hac`),
   MSD (`compute_msd`), DOS (`compute_dos`), SHC (`compute_shc`), viscosity
   (`compute_viscosity`). Exceptions: HNEMD uses NVT with `compute_hnemd`;
   source-sink NEMD uses `heat_nhc`, `heat_lan`, or `heat_bdp`.
5. `model.xyz` is extended XYZ, not plain XYZ. Line 2 must contain
   `lattice="..."`, `pbc="T T T"` or task-specific PBC, and a valid
   `Properties=...` declaration.
6. Group-based NEMD/temperature/filtered dumps require integer group columns in
   `model.xyz` before the run. GPUMD will not auto-partition source/sink groups.
7. `model.xyz` species must match the NEP potential exactly. Do not use a
   universal or example `nep.txt` for elements outside its training domain.
8. `compute_dos` takes only 3 required arguments:
   `compute_dos <sample_interval> <Nc> <max_omega>`. Do not add a
   `num_omega` argument.
9. In GPUMD v5.2, `compute_rdf` is `compute_rdf <cutoff> <num_bins> <interval>`
   and `compute_viscosity` has two required arguments:
   `compute_viscosity <sample_interval> <correlation_steps>`.
10. In GPUMD v5.2, `dump_observer` is
    `dump_observer <observe|average> <interval_thermo> <interval_exyz> <has_velocity> <has_force>`.
    Do not use older shorthand forms.
11. In GPUMD v5.2, `active` is
    `active <interval> <has_velocity> <has_force> <has_uncertainty> <threshold>`,
    and only works with a committee of NEP potentials.
12. Respect the Nyquist bound for `compute_dos` and `compute_shc`:
   `max_omega < pi / (sample_interval * time_step)`. For example, with
   `sample_interval=5` and `time_step=1` fs, keep `max_omega < 628` THz.
   See `references/run_in_keywords.md` for examples.
13. For `nep.in`, the `type N El1 El2 ...` line is mandatory and must match the
    species present in `train.xyz` and `test.xyz`.

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

## Common `run.in` Patterns

Use these as templates, then adjust temperatures, steps, and output intervals to
the task scale.

- EMD thermal conductivity: NVT equilibration, then NVE production with
  `compute_hac`.
- HNEMD thermal conductivity: NVT equilibration, then NVT production with
  `compute_hnemd`; add `compute_shc` only if spectral decomposition is requested.
- NEMD source-sink thermal transport: define source/sink groups in `model.xyz`,
  equilibrate, then use `ensemble heat_nhc`/`heat_lan`/`heat_bdp` and
  `compute_temperature group_method <idx>`.
- Liquid diffusion/viscosity/RDF: NPT or NVT equilibration, then NVE production
  with `compute_msd`, `compute_sdc`, `compute_viscosity`, `compute_rdf`, and
  optionally `compute_adf`.
- Phonon DOS: NVT equilibration, then NVE production with `compute_dos`.
- Active learning / observer: at least two `potential` lines; first potential
  drives MD in `observe` mode, or all potentials are averaged in `average` mode.
  Use `active` and/or v5.2 `dump_observer` syntax.

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

NEP training guardrails:

- If `train.xyz` lacks virial/stress data, set `lambda_v 0.0`.
- Keep angular cutoff <= radial cutoff. Typical baseline: `cutoff 8 4`.
- Keep `batch` <= number of training structures. For small datasets, use the
  dataset size instead of blindly keeping `batch 1000`.
- Add `zbl <r_inner> <r_outer>` for high-energy collision/radiation-damage data
  when short-range repulsion is needed.
- Do not claim a trained model is production quality from `nep.in` alone; inspect
  `loss.out`, `energy_train/test.out`, `force_train/test.out`, and
  `virial_train/test.out` when available.

## Official Docs

The official GPUMD docs describe GPUMD as a GPU MD package that supports NEP
models and exposes the `gpumd` and `nep` executables:
https://gpumd.org/

Official required files:

- `gpumd`: `model.xyz` and `run.in`
  (https://gpumd.org/gpumd/input_files/index.html)
- `nep`: `nep.in`, `train.xyz`, and `test.xyz`
  (https://gpumd.org/nep/input_files/index.html)

## References (read on demand)

- `references/official_docs_map.md` — official GPUMD page map and when to use each page
- `references/run_in_keywords.md` — complete keyword reference for `run.in`
- `references/potential_files.md` — NEP, EAM, ADP, Tersoff, FCP potential formats
- `references/dump_outputs.md` — dump keywords, observer outputs, trajectories, restarts
- `references/nep_in_keywords.md` — NEP training parameter reference for `nep.in`
- `references/nep_advanced_workflows.md` — NEP prediction, descriptors, type weights, fine-tuning
- `references/input_examples.md` — worked examples for common simulation types
- `references/model_xyz_format.md` — model.xyz extended XYZ format, group definitions
- `references/nep_training_data_format.md` — `train.xyz`/`test.xyz` schema, units, labels
- `references/output_files.md` — GPUMD/NEP output files and analysis guards
- `references/heat_transport_workflows.md` — EMD, NEMD, HNEMD, SHC, modal workflow rules
- `references/modal_analysis.md` — GKMA/HNEMA modal analysis syntax and guards
- `references/active_learning.md` — NEP committee uncertainty and active outputs
- `references/general_md_static_workflows.md` — minimization, NVT/NPT, elastic, phonon, RDF/ADF
- `references/installation_runtime.md` — GPU/runtime, optional features, Bohrium notes
