# Multi-MLIP Calculator Dispatch Guide

## Supported Families

The `_calculator.py` module supports four MLIP families via a unified `build_calculator()` interface:

| Family | Package | Calculator Class | GPU? |
|--------|---------|-----------------|------|
| **DP** (DPA) | `deepmd-kit` | `deepmd.calculator.DP` | Yes |
| **MACE** | `mace-torch` | `mace.calculators.mace_mp` | Yes |
| **SevenNet** | `sevenn` | `sevenn.SevenNetCalculator` | Yes |
| **MatterSim** | `mattersim` | `mattersim.forcefield.MatterSimCalculator` | Yes |

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

## Docker images by family

| Family | Known working image |
|--------|-------------------|
| DP (DPA) | `registry.dp.tech/dptech/dpa-calculator:cd96ac21` |
| All families | `registry.dp.tech/dptech/dp/native/prod-375/lambench:v2.9` (LAMBench image) |

> Use `Bohrium(action="list_images", keyword="lambench")` to find the latest multi-MLIP image.
> The DPA-specific image is smaller and faster to pull when only DPA is needed.
