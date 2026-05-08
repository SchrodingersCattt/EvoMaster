# GPUMD Official Documentation Map

Use this file to choose the right GPUMD reference before writing inputs or
answering task-specific questions.

## Core Pages

| Topic | Official URL | Use when |
|-------|--------------|----------|
| Overview | https://gpumd.org/ | Explaining what GPUMD is and which executable to use. |
| Introduction | https://gpumd.org/introduction.html | Checking scope, GPU requirement, Python interfaces, discussions. |
| Installation | https://gpumd.org/installation.html | Compiling GPUMD, optional NetCDF/PLUMED/DP support, runtime requirements. |
| `gpumd` executable | https://gpumd.org/gpumd/index.html | MD simulations and analysis workflows. |
| `nep` executable | https://gpumd.org/nep/index.html | NEP training and prediction workflows. |

## `gpumd` Input Files

| File | Official URL | Required? | Notes |
|------|--------------|-----------|-------|
| `model.xyz` | https://gpumd.org/gpumd/input_files/model_xyz.html | Yes | Extended XYZ simulation model. |
| `run.in` | https://gpumd.org/gpumd/input_files/run_in.html | Yes | Sequential simulation protocol. |
| `kpoints.in` | https://gpumd.org/gpumd/input_files/kpoints_in.html | Conditional | Static/phonon workflows that need k-points. |
| `eigenvector.in` | https://gpumd.org/gpumd/input_files/eigenvector_in.html | Conditional | Modal analysis such as GKMA/HNEMA. |

The official docs state that a `gpumd` simulation needs at least `model.xyz`
and `run.in`.

## `nep` Input Files

| File | Official URL | Required? | Notes |
|------|--------------|-----------|-------|
| `nep.in` | https://gpumd.org/nep/input_files/nep_in.html | Yes | NEP hyperparameters and training mode. |
| `train.xyz` | https://gpumd.org/nep/input_files/train_test_xyz.html | Yes | Training dataset in extended XYZ. |
| `test.xyz` | https://gpumd.org/nep/input_files/train_test_xyz.html | Yes | Test dataset in the same format. |

The official docs state that a `nep` construction needs `nep.in`, `train.xyz`,
and `test.xyz`.

## Theory / Methods

| Topic | Official URL | Use when |
|-------|--------------|----------|
| NEP formalism | https://gpumd.org/potentials/nep.html | Explaining NEP versions, descriptor size, loss terms. |
| Interatomic potentials | https://gpumd.org/potentials/index.html | Checking potential families supported by GPUMD. |
| `potential` keyword | https://gpumd.org/gpumd/input_parameters/potential.html | Selecting potential files in `run.in`. |
| EAM potential | https://gpumd.org/potentials/eam.html | EAM analytical/table formats. |
| FCP potential | https://gpumd.org/potentials/fcp.html | Force constant potential driver and data files. |
| Tersoff 1988 | https://gpumd.org/potentials/tersoff_1988.html | General Tersoff format. |
| Tersoff 1989 | https://gpumd.org/potentials/tersoff_1989.html | Faster one/two-element Tersoff format. |
| ADP potential | https://gpumd.org/potentials/adp.html | Angular dependent metallic potentials. |
| Heat transport | https://gpumd.org/theory/heat_transport.html | EMD, NEMD, HNEMD, SHC, GKMA, HNEMA, HNEMDEC. |
| Minimize | https://gpumd.org/gpumd/input_parameters/minimize.html | Structure relaxation and relaxed-structure output. |
| Elastic constants | https://gpumd.org/gpumd/input_parameters/compute_elastic.html | Static elastic tensor calculations. |
| Phonons | https://gpumd.org/gpumd/input_parameters/compute_phonon.html | Phonon dispersion with `kpoints.in`. |
| RDF | https://gpumd.org/gpumd/input_parameters/compute_rdf.html | Radial distribution functions. |
| ADF | https://gpumd.org/gpumd/input_parameters/compute_adf.html | Angular distribution functions. |
| NEP prediction | https://gpumd.org/nep/input_parameters/prediction.html | Existing `nep.txt` inference. |
| NEP fine-tuning | https://gpumd.org/nep/input_parameters/fine_tune.html | Fine-tuning from foundation models. |
| NEP model type | https://gpumd.org/nep/input_parameters/model_type.html | Potential, dipole, polarizability training. |
| Dump thermo | https://gpumd.org/gpumd/input_parameters/dump_thermo.html | Global thermodynamic output. |
| Dump position | https://gpumd.org/gpumd/input_parameters/dump_position.html | `movie.xyz` trajectory output. |
| Dump force | https://gpumd.org/gpumd/input_parameters/dump_force.html | Atomic force output. |
| Dump velocity | https://gpumd.org/gpumd/input_parameters/dump_velocity.html | Atomic velocity output. |
| Dump EXYZ | https://gpumd.org/gpumd/input_parameters/dump_exyz.html | Standard extended XYZ dump. |
| Dump XYZ | https://gpumd.org/gpumd/input_parameters/dump_xyz.html | Custom extended XYZ output. |
| Dump observer | https://gpumd.org/gpumd/input_parameters/dump_observer.html | Multi-NEP observe/average outputs. |
| Dump restart | https://gpumd.org/gpumd/input_parameters/dump_restart.html | Restart files. |
| Active learning | https://gpumd.org/gpumd/input_parameters/active.html | NEP committee uncertainty. |
| `active.xyz` | https://gpumd.org/gpumd/output_files/active_xyz.html | Selected high-uncertainty structures. |
| `active.out` | https://gpumd.org/gpumd/output_files/active_out.html | Uncertainty time series. |
| GKMA | https://gpumd.org/gpumd/input_parameters/compute_gkma.html | Modal heat current. |
| HNEMA | https://gpumd.org/gpumd/input_parameters/compute_hnema.html | Modal thermal conductivity. |
| Eigenvectors | https://gpumd.org/gpumd/input_files/eigenvector_in.html | Modal-analysis input file. |

## Local References

- `run_in_keywords.md`: practical `run.in` keyword cheatsheet.
- `potential_files.md`: potential file formats and selection guards.
- `dump_outputs.md`: dump keyword syntax and outputs.
- `model_xyz_format.md`: `model.xyz` format and group columns.
- `nep_in_keywords.md`: practical `nep.in` keyword cheatsheet.
- `nep_advanced_workflows.md`: prediction, descriptors, model type, type weights, fine-tuning.
- `nep_training_data_format.md`: `train.xyz`/`test.xyz` format rules.
- `output_files.md`: generated output files and what to inspect.
- `heat_transport_workflows.md`: EMD/HNEMD/NEMD/SHC/modal workflow rules.
- `modal_analysis.md`: GKMA/HNEMA syntax and memory guards.
- `active_learning.md`: on-the-fly NEP committee uncertainty workflow.
- `general_md_static_workflows.md`: relaxation, NVT/NPT, elastic, phonon, RDF/ADF.
- `installation_runtime.md`: installation, GPU, Bohrium, optional feature notes.
