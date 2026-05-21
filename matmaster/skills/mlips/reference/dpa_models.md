# DPA Pretrained Models Reference

## Available Models

> URLs below are a snapshot. For the current canonical provenance (latest version, byte size, fresh download link) of any DPA checkpoint — and for models not listed here — invoke the **`aissq-explorer`** skill instead of hardcoding URLs.

| Name | Params | URL | Default Head | Notes |
|------|--------|-----|-------------|-------|
| DPA2.4-7M | 6.6M | `https://bohrium.oss-cn-zhangjiakou.aliyuncs.com/13756/27666/store/upload/cd12300a-d3e6-4de9-9783-dd9899376cae/dpa-2.4-7M.pt` | OMat24 | 37-head shared fitting, 120GPU pretrain |
| DPA3.1-3M | 3.3M | `https://bohrium.oss-cn-zhangjiakou.aliyuncs.com/13756/27666/store/upload/18b8f35e-69f5-47de-92ef-af8ef2c13f54/DPA-3.1-3M.pt` | OMat24 | 16 layers, dynamic neighbor selection |
| DPA3.2-5M | 4.8M | `https://dp-storage-test2.oss-cn-zhangjiakou.aliyuncs.com/bohrium-test/bohrium/feedback/attachment/01KF3BF3TX9GVTC96Q0PCV01H3/DPA-3.2-5M.pt` | OMat24 | 24 layers, supports charge/spin fparam |

## Model Heads

DPA multi-head models trained on diverse datasets. The `--head` flag selects which output head to use:

| Head | Training Data | Recommended For |
|------|---------------|-----------------|
| `OMat24` | OMat24 inorganic dataset | Oxides, metals, ceramics, alloys — **default** |
| `OMol25` | OMol25 organic molecule dataset | Drug-like compounds, organic ligands, small molecules |
| `OC22` | Open Catalyst 2022 | Surface catalysis, adsorbate-surface interactions |
| `Organic_Reactions` | Organic reaction dataset | Reaction barriers, transition states, organic mechanisms |
| `ODAC23` | Open DAC 2023 | Metal-organic frameworks, direct air capture materials |

## Charge & Spin (DPA3.2-5M only)

DPA3.2-5M was trained with `numb_fparam=2` in order `[charge, spin_multiplicity]`.

- **charge**: integer, total system charge in e (0 = neutral)
- **spin_multiplicity**: integer, 2S+1 (1 = singlet, 2 = doublet)

Other DPA versions (2.4, 3.1) do **not** use fparam — passing charge/spin has no effect and is ignored.

## Model Selection Guide

| Scenario | Recommended Model |
|----------|-------------------|
| General inorganic solid | DPA3.1-3M (best balance of speed and accuracy) |
| Charged / radical species | DPA3.2-5M (only model supporting charge/spin) |
| Cross-validation | Compare DPA with MACE-MP-0 or SevenNet-0 |
| Organic molecules | DPA3.2-5M with `--head OMol25` (OMol25 is NOT available on DPA3.1-3M) |
| Catalysis surfaces | DPA with `--head OC22` |

## Freezing DPA for LAMMPS

The ASE workflows in this skill load the multi-task `.pt` file directly and pick a head via `--head`. **LAMMPS cannot consume the raw multi-task `.pt`** — you must first freeze a single branch into a `.pth`. The procedure is identical for DPA2.4-7M, DPA3.1-3M, and DPA3.2-5M.

Requirements: `deepmd-kit >= 3.1.0` (verify with `dp --version`; the `mlips:dev-0421` image reports `v1.3.3.dev2445` which **is** the v3.x codebase — the version string comes from `git describe` against an ancient tag).

### 1. Show available branches

```bash
dp --pt show DPA-3.2-5M.pt model-branch
```

Expected output (DPA3-style):

```
Available model branches are ['OMat24', 'OMol25', 'OC22', 'Organic_Reactions',
'ODAC23', ..., 'RANDOM'], where 'RANDOM' means using a randomly initialized
fitting net.
```

DPA2.4-7M exposes many more branches (e.g. `Domains_Alloy`, `H2O_H2O_PD`, `Metals_Cu`, ...). Pick the branch whose training data best matches your system's chemistry.

### 2. Freeze the chosen branch

```bash
# both flags accepted; --model-branch is canonical in v3.1+
dp --pt freeze -c DPA-3.2-5M.pt -o frozen_model.pth --model-branch [head_name]

# equivalent
dp --pt freeze -c DPA-3.2-5M.pt -o frozen_model.pth --head [head_name]
```

Output `frozen_model.pth` is a **single-head** model usable in both LAMMPS and ASE.

### 3. Use in LAMMPS

```
pair_style  deepmd frozen_model.pth
pair_coeff  * *
```

**Type-map alignment (critical):** The frozen model preserves the full-periodic-table type_map from pretraining (H=1, He=2, ..., Fe=26, ..., Ni=28, ...). The LAMMPS data file atom types MUST match these indices. Two valid approaches:

- **Full-index approach** (recommended): declare ≥N atom types in the data file (where N = max atomic number used), assign Fe to type 26 and Ni to type 28 in the Masses section. Types 1-25 and 27 are unused but must be declared.
- **Compact approach** (advanced): freeze with `--type-map Fe Ni` to produce a model with only 2 types. Then Fe=1, Ni=2 in the data file. This overrides the default full type_map.

If you use compact types (1, 2) but freeze without `--type-map`, LAMMPS will silently map type 1 to H and type 2 to He — producing garbage results.

Run via `$PREFIX/bin/lmp -in in.lmp` (use the `lmp` binary shipped with the deepmd environment, not a system LAMMPS).

### 4. Optional: zero-shot bias adjustment

Before freezing, you can re-align the per-element energy bias of the pretrained model to your downstream system without retraining:

```bash
dp --pt change-bias DPA-3.2-5M.pt -s <your_system> --model-branch [head_name]
```

Then freeze the resulting checkpoint as in step 2.

### Common pitfalls

- **Missing `--model-branch` / `--head`**: freezing DPA3 without it fails (multi-task model needs a branch selection).
- **Using `dp < 3.1.0`**: older versions cannot freeze DPA3 checkpoints. Upgrade first.
- **Loading unfrozen `.pt` in LAMMPS**: not supported; always freeze to `.pth` first.
