# GPUMD Heat Transport Workflows

Official source: https://gpumd.org/theory/heat_transport.html

Use this file when generating or reviewing `run.in` for thermal transport.

## EMD: Green-Kubo Thermal Conductivity

Purpose: compute lattice thermal conductivity from the heat-current
autocorrelation function.

Official keyword/output:

- Keyword: `compute_hac`
- Output: `hac.out`

Practical `run.in` structure:

```text
potential nep.txt
velocity 300

ensemble nvt_nhc 300 300 100
dump_thermo 1000
run 100000

ensemble nve
compute_hac 5 250 10000
dump_thermo 1000
run 1000000
```

Guards:

- Use NVT/NPT only for equilibration.
- Use NVE for production; thermostats corrupt equilibrium correlations.
- `compute_hac` must appear before the production `run`.
- `hac.out` may contain decomposed heat-current components; sum components only
  when that matches the user's requested analysis.

## NEMD: Source-Sink Thermal Transport

Purpose: generate a non-equilibrium steady state with local thermostats.

Official notes:

- Thermal conductance can be estimated from heat flux and source-sink
  temperature difference.
- The official theory page recommends Langevin source-sink thermostatting, i.e.
  `heat_lan`, based on Li2019.
- Output for temperature/heat-transfer data is commonly `compute.out`.

Practical requirements:

- `model.xyz` must define source and sink group labels.
- `run.in` must use `source <group_id> sink <group_id>`.
- Use `compute_temperature group_method <idx>` for temperature profiles.

Template:

```text
potential nep.txt
velocity 300

ensemble nvt_nhc 300 300 100
dump_thermo 1000
run 200000

ensemble heat_lan 300 300 100 source 1 sink 2
compute_temperature group_method 0
dump_thermo 1000
run 2000000
```

Guards:

- Do not reference source/sink groups unless `model.xyz` has a matching
  `group:I:<n>` property.
- For finite systems, use the length between source and sink for apparent
  thermal conductivity unless the user specifies another analysis convention.

## HNEMD

Purpose: compute thermal conductivity using a homogeneous driving force. It is
physically equivalent to EMD in the linear-response regime but often converges
faster.

Official keyword/output:

- Keyword: `compute_hnemd`
- Output: `kappa.out`
- Recommended ensemble: global Nose-Hoover chain thermostat, `nvt_nhc`.

Template:

```text
potential nep.txt
velocity 300

ensemble nvt_nhc 300 300 100
dump_thermo 1000
run 100000

ensemble nvt_nhc 300 300 100
compute_hnemd 1000 0.00001 0 0
dump_thermo 1000
run 2000000
```

Guards:

- Use a small driving force to stay in the linear-response regime.
- Do not switch production to NVE for HNEMD; HNEMD needs a thermostat to absorb
  driven heat.

## SHC: Spectral Heat Current

Purpose: decompose non-equilibrium heat current by frequency. Can be used with
NEMD or HNEMD workflows.

Official keyword/output:

- Keyword: `compute_shc`
- Output: `shc.out`

Template with HNEMD:

```text
potential nep.txt
velocity 300

ensemble nvt_nhc 300 300 100
run 100000

ensemble nvt_nhc 300 300 100
compute_hnemd 1000 0.00001 0 0
compute_shc 2 250 0 400 50.0
dump_thermo 1000
run 2000000
```

Guards:

- `compute_shc` takes `sample_interval`, `Nc`, transport direction, number of
  frequency points, and `max_omega`.
- `sample_interval` must be between 1 and 10, and `Nc` must be between 100 and
  1000 in the official v5.2 syntax.
- Keep `max_omega` below the Nyquist limit for the chosen sampling interval and
  timestep.
- For group-resolved SHC, the referenced groups must already exist in
  `model.xyz`.
- If `group <grouping_method> -1` is used, GPUMD calculates all nonzero groups
  in that grouping method, which can be expensive.

## Modal Methods: GKMA and HNEMA

Purpose: decompose heat current or thermal conductivity into vibrational modes.

Official related files/keywords:

- Input: `eigenvector.in`
- GKMA keyword/output: `compute_gkma` -> `heatmode.out`
- HNEMA keyword/output: `compute_hnema` -> `kappamode.out`

Guards:

- Do not generate modal-analysis workflows unless the user provides or requests
  eigenvectors.
- GKMA syntax is
  `compute_gkma <sample_interval> <first_mode> <last_mode> <bin_option> <size>`.
- HNEMA syntax is
  `compute_hnema <sample_interval> <output_interval> <Fe_x> <Fe_y> <Fe_z> <first_mode> <last_mode> <bin_option> <size>`.
- HNEMA, like HNEMD, should use a global thermostat such as `nvt_nhc`.
- `compute_gkma` and `compute_hnema` cannot be used in the same run; the last one
  is used.
- These workflows are more specialized than EMD/HNEMD/NEMD; ask for missing
  eigenvector or phonon-preparation details if absent.

## HNEMDEC

Purpose: multicomponent transport using Onsager coefficients.

Official keyword/output:

- Keyword: `compute_hnemdec`
- Output: `onsager.out`

Use only when the user explicitly asks for multicomponent HNEMDEC or Onsager
coefficient calculations.
