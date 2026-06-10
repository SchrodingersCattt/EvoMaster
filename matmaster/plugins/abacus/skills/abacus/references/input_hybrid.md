# Hybrid Functional (HSE06 / PBE0)

**PW hybrid SCF**:
```
INPUT_PARAMETERS
calculation scf
basis_type pw
ntype 1
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
ntype 1
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

## HSE Band + DOS Workflow KPT Files

> ⚠ **DOS k-mesh must be dense even for expensive hybrid functionals.** Never reuse the SCF mesh for DOS — a sparse DOS mesh produces meaningless spectra.

**KPT-band** (line mode, example FCC):
```
K_POINTS
5
Line
0.500  0.500  0.500  40  // L
0.000  0.000  0.000  40  // Gamma
0.500  0.000  0.500  40  // X
0.625  0.250  0.625  20  // U|K
0.375  0.375  0.750  1   // K
```

**KPT-dos** (dense Gamma-centered uniform mesh, **minimum 8×8×8 for bulk**):
```
K_POINTS
0
Gamma
12 12 12 0 0 0
```

| Mesh use | Requirement |
|----------|-------------|
| SCF | Moderate mesh OK (e.g. 4×4×4) — HSE is expensive |
| Band NSCF | Line-mode k-path along high-symmetry directions |
| DOS NSCF | **Dense uniform mesh ≥ 8×8×8** — this is non-negotiable for smooth DOS |
