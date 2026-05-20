# Hybrid Functional (HSE06 / PBE0)

**PW hybrid SCF**:
```
INPUT_PARAMETERS
calculation scf
basis_type pw
ecutwfc 50
scf_thr 1.0e-7
scf_nmax 200
dft_functional hse06
exx_hybrid_alpha 0.25
pseudo_dir /root/apns-pseudopotentials-v1/
```

**LCAO hybrid SCF**:
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 200
dft_functional hse06
exx_hybrid_alpha 0.25
pseudo_dir /root/apns-pseudopotentials-v1/
orbital_dir /root/apns-orbitals-efficiency-v1/
```

Valid `dft_functional` values for hybrid: `hse06` (or `hse`, equivalent when compiled with LIBXC), `pbe0`, `b3lyp`. Prefer `hse06` for clarity.

| Parameter | Purpose |
|-----------|---------|
| `dft_functional hse06` | HSE06 screened hybrid (25% exact exchange, range-separated) |
| `dft_functional pbe0` | PBE0 global hybrid (25% exact exchange) |
| `exx_hybrid_alpha` | Exact-exchange fraction (default 0.25 for HSE06/PBE0; set explicitly for reproducibility) |
