# run.in Keyword Reference

Official source: https://gpumd.org/gpumd/input_files/run_in.html

`run.in` defines the simulation protocol. GPUMD executes commands one by one.
Blank lines and lines starting with `#` are ignored. Other lines follow:

```text
keyword parameter_1 parameter_2 ...
```

## Ordering Rules

A `run.in` is read **top-to-bottom, sequentially**. Within each block the order is:

1. `potential` (one or more lines; must appear before anything else)
2. Global setup: `velocity`, `time_step`, `neighbor`, `correct_velocity`
3. Ensemble: `ensemble ...`
4. Fixes: `fix`
5. Computes: `compute_*`
6. Dumps: `dump_*`
7. `run N` (executes the block)

A new `ensemble` line starts a new block. Multiple `run` blocks are allowed.

---

## Static / Immediate Actions

These commands execute immediately rather than waiting for a later `run`:

| Keyword | Use |
|---------|-----|
| `minimize` | Minimize the full system before MD. |
| `compute_cohesive` | Compute cohesive energy curve. |
| `compute_elastic` | Compute elastic constants. |
| `compute_phonon` | Compute phonon dispersions. |

Only include these if the user asks for static calculations. Do not mix them
into a standard MD workflow unless the task explicitly needs them.

## Potential

```
potential  <path>            # NEP potential file (relative path)
potential  <path>            # second potential → enables observer / active learning
```

- At least one `potential` line is **mandatory** and must be the first keyword.
- Two or more `potential` lines: first is the driver; the rest are observers.

## Global Setup

| Keyword | Syntax | Notes |
|---------|--------|-------|
| `velocity` | `velocity <T> [seed <seed>]` | Initialize velocities at temperature T (K). If `model.xyz` has `vel:R:3`, those velocities are used instead. |
| `time_step` | `time_step <dt> [max_distance_per_step]` | Timestep in fs. Default 1.0. Optional distance cap is in Angstrom. |
| `neighbor` | `neighbor <skin>` | Neighbor-list skin distance in A. Default auto. |
| `correct_velocity` | `correct_velocity <interval>` | Remove COM drift every N steps. |

## Ensembles

| Ensemble | Syntax | Use |
|----------|--------|-----|
| `nve` | `ensemble nve` | Microcanonical. **Required** for EMD, MSD, viscosity, SHC production. |
| `nvt_nhc` | `ensemble nvt_nhc <T1> <T2> <Tcouple>` | Nose-Hoover chain thermostat. |
| `nvt_ber` | `ensemble nvt_ber <T1> <T2> <Tcouple>` | Berendsen thermostat (equilibration / active learning). |
| `nvt_lan` | `ensemble nvt_lan <T1> <T2> <Tcouple>` | Langevin thermostat. |
| `nvt_bdp` | `ensemble nvt_bdp <T1> <T2> <Tcouple>` | Bussi-Donadio-Parrinello thermostat. |
| `npt_scr` | `ensemble npt_scr <T> <T> <Tcouple> <P> <P> <P> <Pcouple> ...` | Stochastic cell rescaling. Good for liquid equilibration. |
| `npt_ber` | `ensemble npt_ber <T> <T> <Tcouple> <Px> <Py> <Pz> <Pcouple>` | Berendsen NPT. |
| `heat_nhc` | `ensemble heat_nhc <T> <T> <Tcouple> source <g1> sink <g2>` | NHC-based NEMD heat source/sink. |
| `heat_lan` | `ensemble heat_lan <T> <T> <Tcouple> source <g1> sink <g2>` | Langevin NEMD heat source/sink. |
| `heat_bdp` | `ensemble heat_bdp <T> <T> <Tcouple> source <g1> sink <g2>` | BDP NEMD heat source/sink. |

- `<T1> <T2>`: start and end temperatures (K). Use same value for constant T.
- `<Tcouple>`: thermostat coupling time (steps). Typical: 100.
- `<Pcouple>`: barostat coupling time (steps). Typical: 1000-2000.
- `source <g1> sink <g2>`: atom group indices for heat source/sink.

## Fix

```
fix  <group_id>             # freeze group (wall / boundary atoms)
```

## Compute Keywords

All `compute_*` keywords must appear **before** the `run` in the same block.

| Keyword | Syntax | Purpose |
|---------|--------|---------|
| `compute_hac` | `compute_hac <sample_interval> <Nc> <output_interval>` | Heat-current autocorrelation (EMD thermal conductivity). |
| `compute_hnemd` | `compute_hnemd <output_interval> <Fx> <Fy> <Fz>` | Homogeneous NEMD driving force. |
| `compute_shc` | `compute_shc <sample_interval> <Nc> <transport_dir> <num_omega> <max_omega> [group <grouping_method> <group_id>]` | Spectral heat current decomposition. |
| `compute_gkma` | `compute_gkma <sample_interval> <first_mode> <last_mode> <bin_option> <size>` | GKMA modal heat current. |
| `compute_hnema` | `compute_hnema <sample_interval> <output_interval> <Fe_x> <Fe_y> <Fe_z> <first_mode> <last_mode> <bin_option> <size>` | HNEMA modal thermal conductivity. |
| `compute_msd` | `compute_msd <sample_interval> <Nc> [group <group_method> <group> \| all_groups <group_method>] [save_every <interval>]` | Mean square displacement and SDC from MSD derivative. |
| `compute_sdc` | `compute_sdc <sample_interval> <Nc> [optional_args]` | Self-diffusion coefficient from velocity autocorrelation. |
| `compute_viscosity` | `compute_viscosity <sample_interval> <correlation_steps>` | Green-Kubo viscosity. |
| `compute_dos` | `compute_dos <sample_interval> <Nc> <max_omega> [group <group_method> <group>] [num_dos_points <points>]` | Phonon density of states. |
| `compute_rdf` | `compute_rdf <cutoff> <num_bins> <interval>` | RDF for all atom pairs in recent GPUMD versions. |
| `compute_adf` | `compute_adf <interval> <num_bins> <rc_min> <rc_max>` or local triplets | Angular distribution function. |
| `compute_temperature` | `compute_temperature <group_method>` | Per-group temperature output (for NEMD profiles). |

### Key Parameters

- `sample_interval`: sample every N steps. Typical: 2-10.
- `Nc`: correlation length in samples. Controls time window: `Nc * sample_interval * dt` = correlation time.
- `output_interval`: write averaged results every N steps when the keyword has an output-interval argument. Typical: 1000-10000.
- `transport_dir`: 0=x, 1=y, 2=z.
- `max_omega`: max angular frequency in THz for SHC/DOS. `compute_shc` also takes `num_omega` (number of frequency bins); `compute_dos` does NOT take `num_omega`.
- **Nyquist constraint for `compute_dos`/`compute_shc`**: `max_omega` must be < `pi / (sample_interval * time_step)`. Example: `sample_interval=5`, `time_step=1` fs -> max_omega < 628 THz. With `sample_interval=10`, max_omega < 314 THz. Violating this causes "Velocity sampling rate < Nyquist frequency" error.
- `compute_rdf` official order is cutoff first, then number of bins, then interval.
- `compute_adf` global form uses interval first, then number of angle bins, then `rc_min` and `rc_max`.

## Dump Keywords

All `dump_*` keywords must appear **before** the `run` in the same block.

| Keyword | Syntax | Purpose |
|---------|--------|---------|
| `dump_thermo` | `dump_thermo <interval>` | Write T, KE, PE, stress, etc. every N steps. |
| `dump_position` | `dump_position <interval> [group <grouping_method> <group_id>] [precision single\|double]` | Write atomic positions to `movie.xyz`. |
| `dump_force` | `dump_force <interval> [group <grouping_method> <group_id>]` | Write forces. |
| `dump_velocity` | `dump_velocity <interval> [group <grouping_method> <group_id>]` | Write velocities. |
| `dump_exyz` | `dump_exyz <interval> [has_velocity] [has_force] [has_potential] [separated]` | Extended XYZ output to `dump.xyz`. |
| `dump_xyz` | `dump_xyz <grouping_method> <group_id> <interval> <filename> [properties...]` | Custom extended XYZ output. |
| `dump_dipole` | `dump_dipole <interval>` | Write dipole moments (requires dipole-trained NEP). |
| `dump_polarizability` | `dump_polarizability <interval>` | Write polarizability tensors (requires pol-trained NEP). |
| `dump_observer` | `dump_observer <observe\|average> <interval_thermo> <interval_exyz> <has_velocity> <has_force>` | Multi-potential observer output. |
| `dump_restart` | `dump_restart <interval>` | Periodically update restart file. |

## Active Learning

```
active <interval> <has_velocity> <has_force> <has_uncertainty> <threshold>
```

- Requires >= 2 NEP `potential` lines.
- Checks committee force uncertainty every `interval` steps.
- Saves configurations to `active.xyz` when max force uncertainty exceeds
  `threshold` (eV/A).
- Writes time and uncertainty to `active.out` for every checked step.

## Run

```
run  <N>
```

Execute N timesteps with the current ensemble and active compute/dump settings. `compute_*` and `dump_*` are **reset** after each `run` — they must be re-specified for subsequent blocks.
