# nep.in Keyword Reference

## Required Files

| File | Description |
|------|-------------|
| `nep.in` | Parameter file (this reference). |
| `train.xyz` | Training structures in extended XYZ format. |
| `test.xyz` | (Optional) Validation structures; same format as `train.xyz`. |

## Mandatory Keywords

| Keyword | Syntax | Notes |
|---------|--------|-------|
| `type` | `type <N> <El1> <El2> ...` | N = number of species. Elements must match `train.xyz`. Order matters — determines internal type mapping. |

## Descriptor Parameters

| Keyword | Syntax | Default | Notes |
|---------|--------|---------|-------|
| `cutoff` | `cutoff <radial> <angular>` | `8 4` | Cutoff radii in A. Radial >= angular. Valid range: 3-10. |
| `n_max` | `n_max <n_rad> <n_ang>` | `4 4` | Radial/angular basis expansion order. Range: 0-19. |
| `basis_size` | `basis_size <b_rad> <b_ang>` | `12 12` | Radial/angular basis size. Range: 1-19. |
| `l_max` | `l_max <l3> [l4] [l5]` | `4 2 0` | Max angular momentum for 3/4/5-body. l3: 0-4, l4: 0-2, l5: 0-1. |

### Descriptor Guidelines

- `cutoff`: angular cutoff should be <= radial cutoff. Larger cutoffs = more neighbors = slower training.
- `n_max`: increasing improves expressiveness but costs memory. For small datasets (< 5000 structures), 4-6 is usually sufficient.
- `l_max`: `4 2 0` is a good default. Adding l5 (`4 2 1`) improves accuracy for complex chemistries but is expensive.

## Neural Network

| Keyword | Syntax | Default | Notes |
|---------|--------|---------|-------|
| `neuron` | `neuron <N>` | `30` | Hidden-layer size. Range: 1-120. Larger = more expressive but slower. |

## Loss Function Weights

| Keyword | Syntax | Default | Notes |
|---------|--------|---------|-------|
| `lambda_e` | `lambda_e <w>` | `1.0` | Energy weight. |
| `lambda_f` | `lambda_f <w>` | `1.0` | Force weight. |
| `lambda_v` | `lambda_v <w>` | `0.1` | Virial/stress weight. Set to 0.0 if no virial data. |

Tip: if virial data is absent from `train.xyz`, set `lambda_v 0.0` explicitly — otherwise training penalizes zero virials.

## Training Schedule

| Keyword | Syntax | Default | Notes |
|---------|--------|---------|-------|
| `batch` | `batch <N>` | `1000` | Batch size (structures per step). |
| `population` | `population <N>` | `50` | Population size for evolutionary algorithm. Range: 10-100. |
| `generation` | `generation <N>` | `100000` | Number of generations. Range: 10000-1000000. |

### Training Schedule Guidelines

- `batch`: should be <= number of training structures. For datasets < 1000 structures, use the dataset size.
- `generation`: 100000 is a good starting point. Monitor loss convergence — increase if not converged.
- `population`: 50 is the standard. Smaller values (20-30) speed up training but may reduce solution quality.

## Optional Keywords

| Keyword | Syntax | Default | Notes |
|---------|--------|---------|-------|
| `version` | `version <N>` | `4` | NEP version. Use 4 for current release. |
| `zbl` | `zbl <r_inner> <r_outer>` | none | Add ZBL repulsive potential for close-range interactions. Useful for radiation damage / high-energy collisions. |
| `lambda_shear` | `lambda_shear <w>` | `0.0` | Shear-modulus regularization weight. |
| `lambda_zbl` | `lambda_zbl <w>` | `0.0` | ZBL loss weight. |

## Output Files

Training produces:

| File | Description |
|------|-------------|
| `nep.txt` | Trained potential (used by `gpumd` via `potential nep.txt`). |
| `loss.out` | Per-generation loss values (energy, force, virial RMSE). |
| `energy_train.out` / `energy_test.out` | Predicted vs reference energies. |
| `force_train.out` / `force_test.out` | Predicted vs reference forces. |
| `virial_train.out` / `virial_test.out` | Predicted vs reference virials. |
