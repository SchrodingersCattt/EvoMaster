# run.in Keyword Reference

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
| `velocity` | `velocity <T>` | Initialize Maxwell-Boltzmann velocities at temperature T (K). |
| `time_step` | `time_step <dt>` | Timestep in fs. Default 1.0. Use 0.5 for light elements / high T. |
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
| `compute_shc` | `compute_shc <sample_interval> <Nc> <transport_dir> <num_omega> <max_omega> [group_method] [group_id]` | Spectral heat current decomposition. |
| `compute_msd` | `compute_msd <sample_interval> <Nc> [group_method] [group_id]` | Mean square displacement. |
| `compute_sdc` | `compute_sdc <sample_interval> <Nc> [group_method] [group_id]` | Self-diffusion coefficient (velocity autocorrelation). |
| `compute_viscosity` | `compute_viscosity <sample_interval> <Nc> <output_interval>` | Green-Kubo viscosity. |
| `compute_dos` | `compute_dos <sample_interval> <Nc> <max_omega> [group_method] [group_id]` | Phonon density of states. 3 required args only. |
| `compute_rdf` | `compute_rdf <Nbins> <r_cut> [r_min]` | Radial distribution function. |
| `compute_adf` | `compute_adf <Nbins> <r_cut> [r_min]` | Angular distribution function. |
| `compute_temperature` | `compute_temperature <group_method>` | Per-group temperature output (for NEMD profiles). |

### Key Parameters

- `sample_interval`: sample every N steps. Typical: 2-10.
- `Nc`: correlation length in samples. Controls time window: `Nc * sample_interval * dt` = correlation time.
- `output_interval`: write averaged results every N steps. Typical: 1000-10000.
- `transport_dir`: 0=x, 1=y, 2=z.
- `max_omega`: max angular frequency in THz for SHC/DOS. `compute_shc` also takes `num_omega` (number of frequency bins); `compute_dos` does NOT take `num_omega`.
- **Nyquist constraint for `compute_dos`/`compute_shc`**: `max_omega` must be < `pi / (sample_interval * time_step)`. Example: `sample_interval=5`, `time_step=1` fs -> max_omega < 628 THz. With `sample_interval=10`, max_omega < 314 THz. Violating this causes "Velocity sampling rate < Nyquist frequency" error.

## Dump Keywords

All `dump_*` keywords must appear **before** the `run` in the same block.

| Keyword | Syntax | Purpose |
|---------|--------|---------|
| `dump_thermo` | `dump_thermo <interval>` | Write T, KE, PE, stress, etc. every N steps. |
| `dump_position` | `dump_position <interval> [group_method] [group_id]` | Write atomic positions to `movie.xyz`. |
| `dump_force` | `dump_force <interval> [group_method] [group_id]` | Write forces. |
| `dump_velocity` | `dump_velocity <interval>` | Write velocities. |
| `dump_dipole` | `dump_dipole <interval>` | Write dipole moments (requires dipole-trained NEP). |
| `dump_polarizability` | `dump_polarizability <interval>` | Write polarizability tensors (requires pol-trained NEP). |
| `dump_observer` | `dump_observer <interval> [observe\|average]` | Multi-potential observer output. `observe`: per-potential. `average`: averaged. |

## Active Learning

```
active  <check_interval> <threshold>
```

- Requires >= 2 `potential` lines.
- Checks inter-potential force disagreement every `check_interval` steps.
- Saves configuration when max force disagreement > `threshold` (eV/A).

## Run

```
run  <N>
```

Execute N timesteps with the current ensemble and active compute/dump settings. `compute_*` and `dump_*` are **reset** after each `run` — they must be re-specified for subsequent blocks.
