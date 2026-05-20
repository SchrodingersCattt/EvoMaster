# DeePKS Workflow (Two Stages)

DeePKS has two distinct stages — do NOT confuse them:

**Stage 1 — Generate Bessel descriptor projectors** (PW, `calculation gen_bessel`):
```
INPUT_PARAMETERS
calculation gen_bessel
basis_type pw
ecutwfc 50
pseudo_dir /root/apns-pseudopotentials-v1/
bessel_descriptor_lmax 2
bessel_descriptor_rcut 6.0
bessel_descriptor_ecut 60
bessel_descriptor_smooth 1
bessel_descriptor_sigma 0.1
```

**Stage 2 — SCF with trained DeePKS model** (LCAO, `deepks_scf 1`):
```
INPUT_PARAMETERS
calculation scf
basis_type lcao
ecutwfc 100
scf_thr 1.0e-7
scf_nmax 100
pseudo_dir /root/apns-pseudopotentials-v1/
orbital_dir /root/apns-orbitals-efficiency-v1/
deepks_scf 1
deepks_model model.ptg
```

| Parameter | Stage | Purpose |
|-----------|-------|---------|
| `calculation gen_bessel` | 1 | Generate Bessel projector basis |
| `bessel_descriptor_lmax` | 1 | Max angular momentum for projectors |
| `bessel_descriptor_rcut` | 1 | Radial cutoff (Bohr) |
| `deepks_scf 1` | 2 | Enable DeePKS correction during SCF |
| `deepks_model xxx.ptg` | 2 | Path to trained PyTorch model file (.ptg) |

> **Common mistake**: confusing stage 2 (inference) with label generation. For inference, use `deepks_scf 1` + `deepks_model`. For generating training labels, use `deepks_out_labels 1` + `deepks_scf 0` — but that is a separate workflow not needed for production calculations.
