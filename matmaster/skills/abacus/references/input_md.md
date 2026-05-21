# Molecular Dynamics INPUT Examples

**NVE (microcanonical)**:
```
INPUT_PARAMETERS
calculation md
basis_type lcao
ecutwfc 100
scf_thr 1.0e-6
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
symmetry 0
cal_force 1
cal_stress 1
md_type nve
md_nstep 100
md_dt 1.0
md_tfirst 300
init_vel 1
gamma_only 1
```

**NVT (canonical, Nose-Hoover chain)**:
```
INPUT_PARAMETERS
calculation md
basis_type lcao
ecutwfc 100
scf_thr 1.0e-6
scf_nmax 100
smearing_method gauss
smearing_sigma 0.01
symmetry 0
cal_force 1
cal_stress 1
md_type nvt
md_thermostat nhc
md_nstep 100
md_dt 1.0
md_tfirst 300
md_tfreq 1.0
init_vel 1
gamma_only 1
```

MD mandatory parameters:

| Parameter | Purpose |
|-----------|---------|
| `symmetry 0` | **CRITICAL** — symmetry breaks MD trajectories (forces get symmetrized incorrectly) |
| `cal_force 1` | Forces drive atomic motion |
| `md_type` | `nve`, `nvt`, `npt`, `langevin`, `msst` |
| `md_thermostat` | Required for NVT: `nhc` (Nose-Hoover chain), `anderson`, `berendsen`, `rescaling` |
| `md_nstep` | Number of MD steps |
| `md_dt` | Time step in fs |
| `md_tfirst` | Initial temperature (K) |

> **`symmetry 0` is NON-NEGOTIABLE for MD.** Without it, ABACUS symmetrizes forces at each step, producing unphysical trajectories. This applies to ALL MD types (NVE, NVT, NPT).
