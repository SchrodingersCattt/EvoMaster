# Multi-MLIP Calculator Dispatch Guide

## Scope

This skill is **DPA-first**. The reason the same scripts work across four MLIP families is that every family exposes an ASE `Calculator`, and `_calculator.py` hides the family-specific construction behind a single `build_calculator()` call. **The unified ASE interface is what is portable.** Default to DPA for all tasks; switch to another family only when explicitly requested or when a specific model is better suited.

## Supported Families

The `_calculator.py` module supports four MLIP families via a unified `build_calculator()` interface. Each family has a dedicated runtime image:

| Family | Package | Version (in image) | Calculator Class |
|--------|---------|---------------------|-----------------|
| **DP** (DPA) | `deepmd-kit` | v3.x dev† | `deepmd.calculator.DP` |
| **MACE** | `mace-torch` | 0.3.16 | `mace.calculators.mace_mp` |
| **SevenNet** | `sevenn` | 0.12.1 | `sevenn.calculator.SevenNetCalculator` |
| **MatterSim** | `mattersim` | 1.2.5 | `mattersim.forcefield.MatterSimCalculator` |

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


## Family-specific notes

### DP (DPA)

- Requires a domain-matched `--head` for multi-head models; do not rely on the inorganic default for specialized domains.
- Supports `--charge` and `--spin` via fparam (DPA3.2-5M only)
- Model files: `.pt` (PyTorch) or `.pb` (TensorFlow, legacy)

### MACE

- No `--head` flag (ignored)
- Default dtype: `float64` for accuracy
- **Use `MACE-MP-0`** (calls `mace_mp()` auto-download, works in Bohrium). Avoid `MACE-MPA-0` — it downloads from GitHub releases which times out inside Bohrium containers.
- Custom MACE `.model` files: pass the local path. Do not assume other families use `.model`; DPA uses `.pt` / `.pb`, and SevenNet checkpoints are commonly `.pth`.

### SevenNet

- Cached named models include `sevennet-0`, `sevennet-0_22may2024`, `sevennet-l3i5`, `sevennet-mf-0`, `sevennet-mf-ompa`, `sevennet-omat`, `sevennet-omni`, `sevennet-omni-i8`, and `sevennet-omni-i12`; use the matching package-recognized name for `--model`.
- `7net-mf-ompa` automatically sets `modal="omat24"`
- No head or fparam support
- Local SevenNet checkpoint files commonly use `.pth`; the generic suffix fallback maps `.pth` to DP, so prefer package-recognized SevenNet model names unless `_calculator.py` has an explicit SevenNet model entry or you write a custom SevenNet script.

### MatterSim

- Cached models include `MatterSim-v1-1M` and `MatterSim-v1-5M`.
- `MatterSim-v1-5M` loads `MatterSim-v1.0.0-5M.pth` by default in the built-in dispatcher.
- No head or fparam support

## Adding a new MLIP family

1. Add model entries to `KNOWN_MODELS` in `_calculator.py`
2. Write an `_init_{family}()` function
3. Register it in `_FAMILY_INIT`
4. Update this reference document

## Docker images

The family images are built from a shared `mlip-base` image, then extended with the family-specific runtime and cached pretrained models.

> **Default: DPA image.** Use a non-DPA family image only when MACE, SevenNet, or MatterSim is explicitly needed. Do not infer MLIP family from image tag strings.

| Image | Families | When to use |
|-------|----------|-------------|
| `registry.dp.tech/dptech/dpa-calculator:dpa-mlip-bd246adc` | **DP only** | **Default for DPA ASE tasks** |
| `registry.dp.tech/dptech/dpa-calculator:mace-mlip-db5a4d45` | MACE | Only when the user explicitly requests MACE. Use `base` env; includes mace-torch 0.3.16. |
| `registry.dp.tech/dptech/dpa-calculator:sevennet-mlip-db5a4d45` | SevenNet | Only when the user explicitly requests SevenNet. Use `base` env; includes sevenn 0.12.1. |
| `registry.dp.tech/dptech/dpa-calculator:mattersim-mlip-db5a4d45` | MatterSim | Only when the user explicitly requests MatterSim. Use `base` env; includes mattersim 1.2.5. |

> If a lightweight Python package is missing, prepend `pip install <pkg> &&` before the script in `cmd`; this fallback is for packages only, not pretrained model checkpoints.
> For Non-DPA pretrained models, most are cached in the family images. If a new model must be downloaded, stage it outside the Bohrium compute job and upload or bundle it with the task. DO NOT cold-download on the task node.
