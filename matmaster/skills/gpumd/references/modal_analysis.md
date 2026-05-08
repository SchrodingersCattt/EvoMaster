# GPUMD Modal Analysis Reference

Use this file for GKMA and HNEMA workflows.

Official sources:

- Heat transport theory: https://gpumd.org/theory/heat_transport.html
- `eigenvector.in`: https://gpumd.org/gpumd/input_files/eigenvector_in.html
- `compute_gkma`: https://gpumd.org/gpumd/input_parameters/compute_gkma.html
- `compute_hnema`: https://gpumd.org/gpumd/input_parameters/compute_hnema.html

## Required Input

Modal workflows require `eigenvector.in`. The official page for
`eigenvector.in` is currently sparse, so do not invent its contents. Require it
from the user or generate it only through a trusted phonon/lattice-dynamics
workflow.

## GKMA

Green-Kubo modal analysis computes modal heat current.

Syntax:

```text
compute_gkma <sample_interval> <first_mode> <last_mode> <bin_option> <size>
```

Arguments:

- `sample_interval`: modal heat current sampling interval in steps.
- `first_mode`, `last_mode`: mode range from `eigenvector.in`.
- `bin_option`: `bin_size` or `f_bin_size`.
- `size`: number of modes per bin for `bin_size`, or frequency-bin size in THz
  for `f_bin_size`.

Output:

- `heatmode.out`

Example:

```text
compute_gkma 10 1 27216 f_bin_size 1.0
```

## HNEMA

Homogeneous non-equilibrium modal analysis computes modal thermal conductivity.

Syntax:

```text
compute_hnema <sample_interval> <output_interval> <Fe_x> <Fe_y> <Fe_z> <first_mode> <last_mode> <bin_option> <size>
```

Arguments:

- `sample_interval`: sampling interval in steps; must divide `output_interval`.
- `output_interval`: output interval for averaged modal thermal conductivity.
- `Fe_x`, `Fe_y`, `Fe_z`: driving-force components in Angstrom^-1.
- `first_mode`, `last_mode`: mode range from `eigenvector.in`.
- `bin_option`: `bin_size` or `f_bin_size`.
- `size`: mode count or frequency-bin size depending on `bin_option`.

Output:

- `kappamode.out`

Example:

```text
compute_hnema 10 1000 0.000008 0 0 1 27216 f_bin_size 1.0
```

## Guards

- `compute_gkma` and `compute_hnema` cannot be used in the same run; the keyword
  that appears last is used.
- Both workflows can be memory intensive; memory requirements can be comparable
  to the size of `eigenvector.in`.
- `heatmode.out` can become many GB for long runs, small sampling intervals, or
  many bins.
- HNEMA should use a global thermostat such as `nvt_nhc`, like HNEMD.
- Do not add modal workflows to ordinary EMD/HNEMD tasks unless the user asks
  for modal decomposition.
