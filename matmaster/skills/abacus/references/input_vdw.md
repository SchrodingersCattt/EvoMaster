# van der Waals Corrections (Grimme D2 / D3)

**D2** (requires `c6.txt` file with C6 coefficients):
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
vdw_method d2
vdw_C6_file c6.txt
pseudo_dir /root/apns-pseudopotentials-v1/
orbital_dir /root/apns-orbitals-efficiency-v1/
```

**D3(BJ)** (Becke-Johnson damping, no extra file needed):
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
vdw_method d3_bj
pseudo_dir /root/apns-pseudopotentials-v1/
orbital_dir /root/apns-orbitals-efficiency-v1/
```

| `vdw_method` | Damping | Extra file | Notes |
|--------------|---------|------------|-------|
| `d2` | Grimme-D2 | `vdw_C6_file c6.txt` | Requires user-provided C6 file |
| `d3_bj` | D3 Becke-Johnson | none | **Standard D3 in ABACUS** — use this for "D3" |
| `d3_0` | D3 zero-damping | none | Less common; only use if explicitly requested |

> **When a task says "D3" without specifying damping, always use `d3_bj`** (Becke-Johnson). This matches the ABACUS 25_vdw official example. Only use `d3_0` if the task explicitly says "zero-damping" or "D3(0)".
