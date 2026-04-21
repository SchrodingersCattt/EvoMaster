# Multi-MLIP Calculator Dispatch Guide

## Scope

This skill is **DPA-first**. The reason the same scripts work across four MLIP families is that every family exposes an ASE `Calculator`, and `_calculator.py` hides the family-specific construction behind a single `build_calculator()` call. **The unified ASE interface is what is portable.** Default to DPA for all tasks; switch to another family only when explicitly requested or when a specific model is better suited.

## Supported Families

The `_calculator.py` module supports four MLIP families via a unified `build_calculator()` interface. The **multi-family image** (`mlips:dev-0421`) ships all four preinstalled:

| Family | Package | Version (in image) | Calculator Class |
|--------|---------|---------------------|-----------------|
| **DP** (DPA) | `deepmd-kit` | 1.3.3.dev2445 | `deepmd.calculator.DP` |
| **MACE** | `mace-torch` | 0.3.12 | `mace.calculators.mace_mp` |
| **SevenNet** | `sevenn` | 0.11.0 | `sevenn.calculator.SevenNetCalculator` |
| **MatterSim** | `mattersim` | 1.1.2 | `mattersim.forcefield.MatterSimCalculator` |

> If using the DPA-only image (`dpa-calculator:e13a296f`) and you need MACE/SevenNet/MatterSim, either switch to the multi-family image or prepend `pip install <pkg> &&` in `cmd`.

## How `build_calculator()` resolves models

```
model_name_or_path
    │
    ├─ Known name (e.g. "DPA3.1-3M", "MACE-MP-0") → lookup KNOWN_MODELS → download if URL → dispatch
    │
    ├─ URL (http/https) → download to cache → default family = DP
    │
    └─ Local path
        ├─ .pt / .pth / .pb → family = DP
        ├─ .model → family = MACE
        └─ other → family = DP (fallback)
```

## Family-specific notes

### DP (DPA)

- Requires `--head` for multi-head models (default: `Omat24`)
- Supports `--charge` and `--spin` via fparam (DPA3.2-5M only)
- Model files: `.pt` (PyTorch) or `.pb` (TensorFlow, legacy)

### MACE

- No `--head` flag (ignored)
- Default dtype: `float64` for accuracy
- Named models (`MACE-MP-0`) use `mace_mp()` auto-download
- Custom `.model` files: pass the local path

### SevenNet

- Named models (`SevenNet-0`, `7net-mf-ompa`) resolved by the `sevenn` package
- `7net-mf-ompa` automatically sets `modal="omat24"`
- No head or fparam support

### MatterSim

- Only one model currently: `MatterSim-v1-5M`
- Auto-downloads `MatterSim-v1.0.0-5M.pth`
- No head or fparam support

## Adding a new MLIP family

1. Add model entries to `KNOWN_MODELS` in `_calculator.py`
2. Write an `_init_{family}()` function
3. Register it in `_FAMILY_INIT`
4. Update this reference document

## Docker images

| Image | Families | Notes |
|-------|----------|-------|
| `registry.dp.tech/dptech/dp/native/prod-19853/mlips:dev-0421` | **All four** (DP, MACE, SevenNet, MatterSim) + lammps | Python 3.12, torch 2.4+cu121, ASE 3.23, phonopy, pymatgen |
| `registry.dp.tech/dptech/dpa-calculator:e13a296f` | DP only | Smaller, faster to pull when only DPA is needed |

> If a package is missing, prepend `pip install <pkg> &&` before the script in `cmd`.
