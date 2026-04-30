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
