# DPA Pretrained Models Reference

## Available Models

| Name | Params | URL | Default Head | Notes |
|------|--------|-----|-------------|-------|
| DPA2.4-7M | 6.6M | `https://bohrium.oss-cn-zhangjiakou.aliyuncs.com/13756/27666/store/upload/cd12300a-d3e6-4de9-9783-dd9899376cae/dpa-2.4-7M.pt` | Omat24 | 37-head shared fitting, 120GPU pretrain |
| DPA3.1-3M | 3.3M | `https://bohrium.oss-cn-zhangjiakou.aliyuncs.com/13756/27666/store/upload/18b8f35e-69f5-47de-92ef-af8ef2c13f54/DPA-3.1-3M.pt` | Omat24 | 16 layers, dynamic neighbor selection |
| DPA3.2-5M | 4.8M | `https://dp-storage-test2.oss-cn-zhangjiakou.aliyuncs.com/bohrium-test/bohrium/feedback/attachment/01KF3BF3TX9GVTC96Q0PCV01H3/DPA-3.2-5M.pt` | Omat24 | 24 layers, supports charge/spin fparam |

## Model Heads

DPA multi-head models trained on diverse datasets. The `--head` flag selects which output head to use:

| Head | Training Data | Recommended For |
|------|---------------|-----------------|
| `Omat24` | OMat24 inorganic dataset | Oxides, metals, ceramics, alloys — **default** |
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
| Organic molecules | DPA3.1-3M with `--head OMol25`, or DPA3.2-5M |
| Catalysis surfaces | DPA with `--head OC22` |
