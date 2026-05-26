# GPUMD Input Examples

## Example 1: EMD Thermal Conductivity

Equilibrium MD approach using heat-current autocorrelation (Green-Kubo).

```
potential  nep.txt

velocity   300

# Stage 1: Equilibration (NVT, 100 ps)
ensemble   nvt_nhc 300 300 100
dump_thermo 1000
run        100000

# Stage 2: Production (NVE, 1 ns)
ensemble   nve
compute_hac 5 250 10000
dump_thermo 1000
run        1000000
```

**Key points:**
- NVE is mandatory for EMD — thermostat corrupts heat-current autocorrelation.
- `compute_hac 5 250 10000`: sample every 5 steps, 250 correlation points, output every 10000 steps.
- Correlation time window = 5 * 250 * 1 fs = 1.25 ps (adjust for material).

## Example 2: HNEMD + SHC Decomposition

Non-equilibrium driving force + spectral decomposition.

```
potential  nep.txt

velocity   300

# Stage 1: Equilibration (NVT, 100 ps)
ensemble   nvt_nhc 300 300 100
dump_thermo 1000
run        100000

# Stage 2: Production (NVT + HNEMD drive, 2 ns)
ensemble   nvt_nhc 300 300 100
compute_hnemd 1000 0.00001 0 0
compute_shc 2 250 0 400 50.0
dump_thermo 1000
run        2000000
```

**Key points:**
- HNEMD uses NVT (not NVE) — the thermostat absorbs the driving-force heat.
- `compute_hnemd 1000 0.00001 0 0`: output every 1000 steps, drive force Fx = 1e-5 (1/A).
- `compute_shc 2 250 0 400 50.0`: sample every 2 steps, 250 correlation points, x-direction, 400 frequency bins, max 50 THz.

## Example 3: Liquid Transport (MSD + Viscosity + RDF)

Multi-property analysis for a liquid system.

```
potential  nep.txt

velocity   2000
time_step  0.5

# Stage 1: NPT equilibration (50 ps)
ensemble   npt_scr 2000 2000 100 0 0 0 2000
dump_thermo 1000
run        100000

# Stage 2: NVE production (500 ps)
ensemble   nve
compute_msd 5 500
compute_sdc 5 500
compute_viscosity 5 500 10000
compute_rdf 200 6.0
compute_adf 180 3.5
dump_thermo 1000
run        1000000
```

**Key points:**
- NPT equilibration for density relaxation at high temperature.
- NVE production is required for MSD, viscosity, and self-diffusion.
- Smaller timestep (0.5 fs) for high-temperature liquid stability.

## Example 4: NEP Training

Minimal `nep.in` for a two-element system.

```
type       2 Si O
cutoff     8 4
n_max      4 4
basis_size 12 12
l_max      4 2 0
neuron     40
lambda_e   1.0
lambda_f   1.0
lambda_v   0.1
batch      1000
population 50
generation 150000
```

**Key points:**
- `type 2 Si O` must match species in `train.xyz` exactly.
- Angular cutoff (4) <= radial cutoff (8).
- `neuron 40` is slightly larger than default for a binary system.
- Monitor `loss.out` convergence; increase `generation` if loss hasn't plateaued.

## Example 5: Active Learning with Multi-Potential Observer

```
potential  nep_v1.txt
potential  nep_v2.txt

velocity   500
time_step  1

# NVT with active-learning trigger
ensemble   nvt_ber 500 500 100
active     100 0.1
dump_observer 1000 observe
dump_thermo 1000
run        500000
```

**Key points:**
- Two `potential` lines: first drives the MD, second is the observer.
- `active 100 0.1`: check disagreement every 100 steps, save if max force difference > 0.1 eV/A.
- `dump_observer 1000 observe`: write per-potential predictions every 1000 steps.
- `observe` mode outputs individual potentials; use `average` for averaged predictions.

## Example 6: NEMD Thermal Conductivity (Source-Sink Method)

Non-equilibrium MD with heat source and sink groups.

**Prerequisites**: `model.xyz` must have a `group` column defining at least 3 groups:
- Group 0: bulk atoms
- Group 1: heat source slab
- Group 2: heat sink slab

See `references/model_xyz_format.md` for the extended XYZ format with group columns.

```
potential  nep.txt

velocity   300
time_step  1

# Stage 1: Equilibration (NVT, 200 ps)
ensemble   nvt_nhc 300 300 100
dump_thermo 1000
run        200000

# Stage 2: NEMD production (heat_nhc, 2 ns)
ensemble   heat_nhc 300 300 100 source 1 sink 2
compute_temperature group_method 0
dump_thermo 1000
run        2000000
```

**Key points:**
- `heat_nhc 300 300 100 source 1 sink 2`: NHC thermostat at 300 K, source=group 1, sink=group 2.
- Groups must be defined in `model.xyz` with a `group:I:1` column.
- `compute_temperature group_method 0` outputs per-group temperatures for the temperature profile.
- Alternative ensembles: `heat_lan` (Langevin) or `heat_bdp` (BDP) with the same syntax.
- `compute_temperature` and `dump_thermo` must be re-specified — they reset after each `run`.

## Example 7: Phonon Density of States (DOS)

Vibrational density of states from velocity autocorrelation.

```
potential  nep.txt

velocity   300

# Stage 1: Equilibration (NVT, 100 ps)
ensemble   nvt_nhc 300 300 100
dump_thermo 1000
run        100000

# Stage 2: NVE production for DOS (200 ps)
ensemble   nve
compute_dos 5 200 50.0
dump_thermo 1000
run        200000
```

**Key points:**
- NVE is required for accurate DOS from velocity autocorrelation.
- `compute_dos 5 200 50.0`: sample every 5 steps, 200 correlation points, max 50 THz. Only 3 required args — no `num_omega` parameter.
- Frequency resolution = max_omega / num_omega = 50/400 = 0.125 THz.
- Correlation time window = 5 x 200 x 1 fs = 1 ps.
- For species-resolved DOS, append `group_method <idx> group_id <id>`.

## Example 8: Phonon Dispersion (Finite Displacement)

Compute phonon dispersion using GPUMD's built-in finite-displacement method.

**Required files:**
- `model.xyz`: primitive cell (e.g. 2-atom Si diamond)
- `kpoints.in`: high-symmetry k-path
- `run.in`: with `replicate` and `compute_phonon`

```
# kpoints.in — FCC Brillouin zone path: G-X-U|K-G-L-W-X
100
0.000 0.000 0.000  # G
0.500 0.000 0.500  # X
0.625 0.250 0.625  # U (equivalent to K)
0.000 0.000 0.000  # G
0.500 0.500 0.500  # L
0.500 0.250 0.750  # W
0.500 0.000 0.500  # X
```

```
# run.in
potential  nep.txt
replicate  8 8 8
compute_phonon 0.01
```

**Key points:**
- `replicate N1 N2 N3`: builds supercell from primitive cell. Supercell must be large enough that `2 * phonon_cutoff < box_length` in each direction. GPUMD will warn "Replicate in X >= N" if too small.
- `compute_phonon <delta>`: finite displacement (Å). Typical: 0.01. No `run` command needed — it exits after computing force constants.
- Output: `omega2.out` — each row is one k-point, columns are ω² eigenvalues (units: THz²). Frequency ν = √(ω²) / (2π) in THz.
- `kpoints.in` first line: number of points per segment. Subsequent lines: fractional k-coordinates.
- Number of branches = 3 × atoms_in_primitive_cell (e.g., Si diamond → 6 branches: 3 acoustic + 3 optical).

## Example 9: Multi-Potential Observer Mode

Evaluate multiple potentials on the same trajectory without active learning.

```
potential  nep_v1.txt
potential  nep_v2.txt

velocity   300
time_step  1

# NVE production — observe mode
ensemble   nve
dump_observer 1000 observe
dump_thermo 1000
run        100000
```

**Key points:**
- Two or more `potential` lines: first drives the dynamics, rest are observers.
- `ensemble nve` is required for unbiased observation (thermostat would mask force differences).
- `dump_observer <interval> observe`: writes per-potential energies/forces every N steps. Output: `observer.out`.
- `dump_observer <interval> average`: writes averaged predictions across all potentials. Use this for ensemble averaging rather than disagreement monitoring.
- Observer mode without `active` keyword: no configurations are saved, only statistics are recorded.

## Example 10: NEMD with Langevin Source/Sink (heat_lan)

Interface thermal transport using group-based heat source and sink.

**Prerequisites**: `model.xyz` must have a `group` column. Groups 0 and 8 are wall atoms (fixed), group 1 is heat source, group 7 is heat sink, groups 2-6 are transport region.

```
potential  nep.txt

velocity   300
time_step  1

# Stage 1: Equilibration (NVT, 200 ps)
ensemble   nvt_nhc 300 300 100
dump_thermo 1000
run        200000

# Stage 2: NEMD production (2 ns)
fix        0
fix        8
ensemble   heat_lan 300 300 100 source 1 sink 7
compute_temperature group_method 0
dump_thermo 1000
run        2000000
```

**Key points:**
- `heat_lan <T1> <T2> <Tcouple> source <g_src> sink <g_sink>`: Langevin NEMD. `<Tcouple>` in steps.
- `fix <group_id>`: freezes atoms in that group (wall boundary condition).
- Alternative ensembles: `heat_nhc` (Nose-Hoover) or `heat_bdp` (BDP) — same parameter syntax.
- `compute_temperature group_method 0`: writes per-group temperatures for temperature profile analysis.
- Source/sink groups must be defined in `model.xyz` group column.
