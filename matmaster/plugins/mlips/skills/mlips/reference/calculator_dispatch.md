# Multi-MLIP Calculator Dispatch Guide

## Scope

This skill is **DPA-first**. The reason the same scripts work across four MLIP families is that every family exposes an ASE `Calculator`, and `_calculator.py` hides the family-specific construction behind a single `build_calculator()` call. **The unified ASE interface is what is portable.** Default to DPA for all tasks; switch to another family only when explicitly requested or when a specific model is better suited.

## Supported Families

The `_calculator.py` module supports four MLIP families via a unified `build_calculator()` interface. Each family has a dedicated runtime image:

| Family | Package | Version (in image) | Calculator Class |
|--------|---------|---------------------|-----------------|
| **DP** (DPA) | `deepmd-kit` | v3.x dev† | `deepmd.calculator.DP` |
| **MACE** | `mace-torch` | 0.3.12 | `mace.calculators.mace_mp` |
| **SevenNet** | `sevenn` | 0.11.0 | `sevenn.calculator.SevenNetCalculator` |
| **MatterSim** | `mattersim` | 1.1.2 | `mattersim.forcefield.MatterSimCalculator` |

> †deepmd-kit reports `1.3.3.dev2445` via `git describe` (2445 commits after the ancient v1.3.3 tag). This **is** the v3.0.0+ PyTorch codebase — not a v1.x build.

> DPA tasks should always use the DPA image (`dpa-calculator:dpa-mlip-bd246adc`). Switch to the matching family image only when MACE/SevenNet/MatterSim is explicitly requested.

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

Do not infer MLIP family from image tag strings. The skill's built-in scripts use the explicit model mapping above; ad-hoc regression scripts should pass `--family` explicitly or probe imports/runtime availability instead of matching substrings such as `"dpa" in image`.

Pretrained models are mostly cached in the family images. If a new model must be downloaded, stage it outside the Bohrium compute job and upload or bundle it with the task instead of cold-downloading on the task node.

## Family-specific notes

### DP (DPA)

- Requires a domain-matched `--head` for multi-head models; do not rely on the inorganic default for specialized domains.
- Supports `--charge` and `--spin` via fparam (DPA3.2-5M only)
- Model files: `.pt` (PyTorch) or `.pb` (TensorFlow, legacy)

### MACE

- No `--head` flag (ignored)
- Default dtype: `float64` for accuracy
- **Use `MACE-MP-0`** (calls `mace_mp()` auto-download, works in Bohrium). Avoid `MACE-MPA-0` — it downloads from GitHub releases which times out inside Bohrium containers.
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

> **Default: DPA image.** Use a non-DPA family image only when MACE, SevenNet, or MatterSim is explicitly needed.

| Image | Families | When to use |
|-------|----------|-------------|
| `registry.dp.tech/dptech/dpa-calculator:dpa-mlip-bd246adc` | **DP only** | **Default for DPA ASE tasks** |
| `registry.dp.tech/dptech/dpa-calculator:mace-mlip-db5a4d45` | MACE | Only when the user explicitly requests MACE. Use `base` env. |
| `registry.dp.tech/dptech/dpa-calculator:sevennet-mlip-db5a4d45` | SevenNet | Only when the user explicitly requests SevenNet. Use `base` env. |
| `registry.dp.tech/dptech/dpa-calculator:mattersim-mlip-db5a4d45` | MatterSim | Only when the user explicitly requests MatterSim. Use `base` env. |

> If a package is missing, prepend `pip install <pkg> &&` before the script in `cmd`.
