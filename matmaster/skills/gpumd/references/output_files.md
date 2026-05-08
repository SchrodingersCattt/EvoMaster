# GPUMD and NEP Output Files Reference

Official sources:

- GPUMD outputs: https://gpumd.org/gpumd/output_files/index.html
- NEP outputs: https://gpumd.org/nep/output_files/index.html

## `gpumd` Outputs

Most GPUMD output files are appended across runs. Restart-like files may be
overwritten. When analyzing results, check whether the file contains data from
multiple stages or previous executions.

| Output | Typical generating keyword | Use |
|--------|----------------------------|-----|
| `thermo.out` | `dump_thermo` | Global thermodynamic quantities. |
| `movie.xyz` | `dump_position` / trajectory dumps | Atomic trajectory in extended XYZ style. |
| `restart.xyz` | `dump_restart` | Restart structure, usually overwritten. |
| `force.out` | `dump_force` | Atomic forces. |
| `velocity.out` | `dump_velocity` | Atomic velocities. |
| `compute.out` | `compute` / `compute_temperature` | Time and group averaged quantities, temperature profiles, NEMD heat data. |
| `hac.out` | `compute_hac` | EMD heat-current autocorrelation and thermal conductivity data. |
| `kappa.out` | `compute_hnemd` | HNEMD thermal conductivity data. |
| `shc.out` | `compute_shc` | Spectral heat current data. |
| `heatmode.out` | `compute_gkma` | Modal heat current from GKMA. |
| `kappamode.out` | `compute_hnema` | Modal thermal conductivity from HNEMA. |
| `dos.out` | `compute_dos` | Phonon density of states data. |
| `sdc.out` | `compute_sdc` | Self-diffusion coefficient data. |
| `msd.out` | `compute_msd` | Mean-square displacement data. |
| `viscosity.out` | `compute_viscosity` | Viscosity and stress autocorrelation data. |
| `rdf.out` | `compute_rdf` | Radial distribution function. |
| `adf.out` | `compute_adf` | Angular distribution function. |
| `dipole.out` | `dump_dipole` | Predicted dipoles; requires compatible NEP. |
| `polarizability.out` | `dump_polarizability` | Predicted polarizability; requires compatible NEP. |
| `observer*.xyz` / observer outputs | `dump_observer` | Per-potential or averaged observer predictions. |
| active-learning selected structures | `active` | Structures selected by force uncertainty. |

Use exact filenames from the generated run directory when possible; some names
depend on GPUMD version and keyword options.

## NEP Outputs

The official docs state that `loss.out` is appended, while most other `nep`
output files are continuously overwritten.

| Output | Use |
|--------|-----|
| `loss.out` | Loss terms, regularization terms, and RMSE as a function of generation. |
| `nep.txt` | Trained NEP potential used by `gpumd` via `potential nep.txt`. |
| `nep.restart` | Restart file for training. |
| `energy_train.out` / `energy_test.out` | Target and predicted energies. |
| `force_train.out` / `force_test.out` | Target and predicted forces. |
| `virial_train.out` / `virial_test.out` | Target and predicted virials. |
| `stress_train.out` / `stress_test.out` | Target and predicted stress values. |
| `dipole_train.out` / `dipole_test.out` | Target and predicted dipoles. |
| `polarizability_train.out` / `polarizability_test.out` | Target and predicted polarizabilities. |
| `charge_train.out` / `charge_test.out` | Predicted charge values. |
| `bec_train.out` / `bec_test.out` | Target and predicted BEC values. |
| `descriptor.out` | Descriptor values in prediction mode. |

## Analysis Guards

- Do not judge an NEP model only from the presence of `nep.txt`; inspect
  `loss.out` and train/test prediction files.
- For runs with multiple `run` blocks, match output timestamps/row counts to the
  production block before reporting final properties.
- For appended files, remove old outputs or run in a clean directory when doing
  reproducibility checks.
- For thermal conductivity, report the method-specific output file:
  `hac.out` for EMD, `kappa.out` for HNEMD, `compute.out` for NEMD temperature
  and heat-transfer data, and `shc.out` for spectral decomposition.
