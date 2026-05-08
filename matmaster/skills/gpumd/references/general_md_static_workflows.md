# GPUMD General MD and Static Workflows

Use this file for GPUMD tasks that are not primarily heat-transport workflows.

Official sources:

- `run.in`: https://gpumd.org/gpumd/input_files/run_in.html
- `velocity`: https://gpumd.org/gpumd/input_parameters/velocity.html
- `time_step`: https://gpumd.org/gpumd/input_parameters/time_step.html
- `ensemble`: https://gpumd.org/gpumd/input_parameters/ensemble.html
- `minimize`: https://gpumd.org/gpumd/input_parameters/minimize.html
- `compute_elastic`: https://gpumd.org/gpumd/input_parameters/compute_elastic.html
- `compute_phonon`: https://gpumd.org/gpumd/input_parameters/compute_phonon.html
- `kpoints.in`: https://gpumd.org/gpumd/input_files/kpoints_in.html
- `dump_xyz`: https://gpumd.org/gpumd/input_parameters/dump_xyz.html

## Universal `run.in` Rules

- `potential` must appear before minimization, static calculations, or MD blocks.
- `run.in` is executed top-to-bottom.
- A normal MD block is `ensemble ...`, optional controls/outputs, then `run N`.
- Use one and only one `ensemble` keyword for each `run`.
- `time_step` propagates to later runs; most output/compute keywords do not.
- If `model.xyz` contains `vel:R:3`, GPUMD uses those velocities and ignores the
  temperature requested by `velocity`.
- If neither `vel:R:3` nor `velocity` is provided, GPUMD initializes velocities
  at 300 K by default.

## Energy Minimization / Relaxation

Official syntax:

```text
minimize <method> <force_tolerance> <maximal_number_of_steps> <box_change> <hydrostatic_strain>
```

Common forms:

```text
potential nep.txt
minimize fire 1.0e-5 1000
```

Cell relaxation with FIRE:

```text
potential nep.txt
minimize fire 1.0e-5 1000 1
```

Hydrostatic cell relaxation:

```text
potential nep.txt
minimize fire 1.0e-5 1000 1 1
```

To write the relaxed structure, use a one-step zero-timestep dump after
minimization:

```text
potential nep.txt
minimize fire 1.0e-5 1000 1

ensemble nve
time_step 0
dump_xyz -1 0 1 relaxed.xyz
run 1
```

Guards:

- `minimize` must occur after `potential`.
- Box optimization currently applies to FIRE, not steepest descent.
- A negative force tolerance means the minimization will run for the full
  maximum number of steps.

## Standard NVT / NPT Equilibration

NVT baseline:

```text
potential nep.txt
velocity 300 seed 12345
time_step 1.0

ensemble nvt_nhc 300 300 100
dump_thermo 1000
run 100000
```

NPT baseline:

```text
potential nep.txt
velocity 300
time_step 1.0

ensemble npt_scr 300 300 100 0 0 0 1000
dump_thermo 1000
run 200000
```

Guards:

- Temperature is in K and pressure is in GPa.
- Thermostat coupling is roughly `tau_T / dt`; a typical value is 100.
- Barostat coupling is roughly `tau_p / dt`; a typical value is 1000.
- Use a smaller timestep, such as 0.5 fs, for light elements, high temperature,
  or unstable early trajectories.

## Elastic Constants

Official syntax:

```text
compute_elastic <strain_value>
```

Template:

```text
potential nep.txt
minimize fire 1.0e-5 1000 1
compute_elastic 0.01
```

Output:

- `elastic.out`

Guards:

- `compute_elastic` must occur after `potential`.
- Relax the structure first unless the task explicitly asks for elastic
  constants at the current geometry.
- A strain value around `0.01` is the official example; adjust only when the user
  specifies a finite-difference amplitude.

## Phonon Dispersion

Official syntax:

```text
compute_phonon <displacement>
```

Required extra input:

- `kpoints.in`

Example `kpoints.in`:

```text
0 0 0 G
0.5 0 0 M
0.333 0.333 0 K
0 0 0 G
```

Template:

```text
potential nep.txt
compute_phonon 0.01
```

Outputs:

- `D.out`
- `omega2.out`

Guards:

- `compute_phonon` should occur after all `potential` keywords.
- The official docs say `replicate` keywords must be written ahead in `run.in`
  for phonon calculations when a supercell is needed.
- For many-body potentials, the force-constant cutoff often needs to be twice
  the potential cutoff; ensure the box is large enough in every direction.
- Use blank lines in `kpoints.in` to separate path segments.

## RDF / ADF / Structure Statistics

RDF syntax:

```text
compute_rdf <cutoff> <num_bins> <interval>
```

ADF global syntax:

```text
compute_adf <interval> <num_bins> <rc_min> <rc_max>
```

Example:

```text
potential nep.txt
velocity 1000
time_step 0.5

ensemble nvt_nhc 1000 1000 100
compute_rdf 8.0 400 1000
compute_adf 1000 50 0.0 3.0
dump_thermo 1000
run 500000
```

Outputs:

- `rdf.out`
- `adf.out`

Guards:

- Do not use the old RDF argument order `num_bins cutoff interval`; v5.2 docs
  use cutoff first.
- ADF local triplets require atom type indices, not element symbols.
