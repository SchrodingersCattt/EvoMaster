# LR-TDDFT (Linear-Response TDDFT)

LR-TDDFT computes optical absorption via Casida-like equations. Uses `esolver_type ks-lr`.

**Periodic system (Si2) — velocity gauge**:
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
esolver_type ks-lr
lr_nstates 10
lr_solver dav
abs_gauge velocity
pseudo_dir ./
orbital_dir ./
```

**Molecular system (H2O) — length gauge**:
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
esolver_type ks-lr
lr_nstates 5
lr_solver dav
abs_gauge length
pseudo_dir ./
orbital_dir ./
```

| Parameter | Purpose |
|-----------|---------|
| `esolver_type ks-lr` | Activate LR-TDDFT solver |
| `lr_nstates` | Number of excited states to compute |
| `lr_solver dav` | Iterative Davidson solver for Casida equation |
| `abs_gauge velocity` | Velocity gauge — for **periodic** systems (correct with PBC) |
| `abs_gauge length` | Length gauge — for **molecular/isolated** systems |

> **Gauge choice rule**: periodic systems MUST use `abs_gauge velocity` (length gauge is ill-defined with PBC). Molecular/cluster systems use `abs_gauge length`.
