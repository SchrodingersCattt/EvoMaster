# Stochastic DFT (SDFT)

SDFT uses stochastic orbitals for finite-temperature electronic structure. **Only PW basis is supported.**

**SCF** (finite-temperature Si2):
```
INPUT_PARAMETERS
calculation scf
esolver_type sdft
basis_type pw
ecutwfc 50
scf_thr 1.0e-6
scf_nmax 20
nbands_sto 64
nche_sto 100
method_sto 1
smearing_method fd
smearing_sigma 0.6
pseudo_dir /root/apns-pseudopotentials-v1/
```

**MD** (high-temperature Al):
```
INPUT_PARAMETERS
calculation md
esolver_type sdft
basis_type pw
ecutwfc 50
scf_thr 1.0e-6
scf_nmax 20
nbands_sto 64
nche_sto 20
method_sto 2
smearing_method fd
smearing_sigma 7.35
cal_force 1
md_nstep 10
md_dt 0.2
md_tfirst 1160400
pseudo_dir /root/apns-pseudopotentials-v1/
```

| Parameter | Purpose |
|-----------|---------|
| `esolver_type sdft` | Activate stochastic DFT solver |
| `basis_type pw` | **Mandatory** — SDFT only works with plane-wave basis |
| `nbands_sto` | Number of stochastic orbitals |
| `nche_sto` | Chebyshev expansion order |
| `method_sto` | Stochastic method (1 or 2) |
| `smearing_method fd` | Fermi-Dirac smearing (physically correct for finite-T) |
| `smearing_sigma` | Electronic temperature in Ry (e.g. 0.6 Ry ~ 95000 K) |
