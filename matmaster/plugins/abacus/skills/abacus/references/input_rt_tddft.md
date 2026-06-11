# RT-TDDFT (Real-Time Time-Dependent DFT)

RT-TDDFT uses `calculation md` with `esolver_type tddft`. Two gauge choices:

**Length gauge** (`td_stype 0`):
```
INPUT_PARAMETERS
calculation md
esolver_type tddft
basis_type lcao
td_vext 1
td_stype 0
out_dipole 1
md_nstep 1000
md_dt 0.002
pseudo_dir /root/apns-pseudopotentials-v1/
orbital_dir /root/apns-orbitals-efficiency-v1/
```

**Velocity gauge** (`td_stype 1`):
```
INPUT_PARAMETERS
calculation md
esolver_type tddft
basis_type lcao
td_vext 1
td_stype 1
out_dipole 1
md_nstep 1000
md_dt 0.002
pseudo_dir /root/apns-pseudopotentials-v1/
orbital_dir /root/apns-orbitals-efficiency-v1/
```

| Parameter | Purpose |
|-----------|---------|
| `esolver_type tddft` | Activate RT-TDDFT propagator |
| `td_vext 1` | Apply time-dependent external field |
| `td_stype 0` | Length gauge (E-field couples to position) |
| `td_stype 1` | Velocity gauge (E-field couples to momentum) |
| `out_dipole 1` | **ALWAYS include** — outputs dipole moment for absorption spectrum extraction |
| `md_nstep`, `md_dt` | Propagation steps and time step (fs) |

> **`out_dipole` is mandatory for both gauges.** Without it, the absorption spectrum cannot be computed from the time-dependent dipole moment. This applies to BOTH length and velocity gauge.
