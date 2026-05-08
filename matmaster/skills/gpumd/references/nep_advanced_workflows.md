# NEP Advanced Workflows

Use this file for NEP prediction, descriptor output, model type selection, type
weighting, and fine-tuning.

Official sources:

- `prediction`: https://gpumd.org/nep/input_parameters/prediction.html
- `model_type`: https://gpumd.org/nep/input_parameters/model_type.html
- `output_descriptor`: https://gpumd.org/nep/input_parameters/output_descriptor.html
- `fine_tune`: https://gpumd.org/nep/input_parameters/fine_tune.html
- `type`: https://gpumd.org/nep/input_parameters/type.html
- `type_weight`: https://gpumd.org/nep/input_parameters/type_weight.html

## Prediction Mode

`prediction` tells `nep` to evaluate an existing model without optimization.

```text
prediction <mode>
```

Modes:

- `0`: optimization/training mode, default.
- `1`: prediction mode.

Requirements:

- `nep.in`
- `nep.txt`
- `train.xyz`

The official docs state that prediction mode evaluates only structures in
`train.xyz`; `test.xyz` is not used for prediction mode.

Minimal example:

```text
type 2 Si O
prediction 1
```

Guards:

- Do not use prediction mode when the user asks to train a new model.
- Make sure `nep.txt` and `type` species match the structures in `train.xyz`.

## Descriptor Output

`output_descriptor` only works in prediction mode.

```text
output_descriptor <mode>
```

Modes:

- `0`: do not output descriptors, default.
- `1`: output per-structure descriptors.
- `2`: output per-atom descriptors.

Example:

```text
type 1 Si
prediction 1
output_descriptor 2
```

Output:

- `descriptor.out`

## Model Type

`model_type` selects the target model family:

```text
model_type <type_value>
```

Values:

- `0`: potential, default.
- `1`: dipole.
- `2`: polarizability.

Training data requirements:

- Potential model: `energy` and `force` data; optional `virial`/`stress`.
- Dipole model: structure-level `dipole="dx dy dz"` in `train.xyz`.
- Polarizability model: structure-level `pol="pxx pxy pxz pyx pyy pyz pzx pzy pzz"`.

Guards:

- Do not set `model_type 1` or `model_type 2` unless the dataset contains the
  corresponding labels.
- Do not expect energy/force fitting behavior for dipole or polarizability
  models; the official training data docs say energy, virial, stress, and force
  are ignored in these modes.

## Type and Type Weight

`type` is mandatory:

```text
type <number_of_species> <El1> <El2> ...
```

Element symbols are case-sensitive and must be real chemical symbols.

`type_weight` changes relative force weights for species:

```text
type_weight <w1> <w2> ...
```

Rules:

- `type_weight` must appear after `type`.
- It must provide exactly `N_typ` non-negative weights.
- It is useful for imbalanced datasets, such as dilute impurity atoms in a host.

Example:

```text
type 2 Fe C
type_weight 1.0 5.0
```

## Fine-Tuning From a Foundation Model

`fine_tune` starts from an existing foundation model:

```text
fine_tune <nep_model_file> <nep_restart_file>
```

The official docs mention the foundation model:

```text
GPUMD/potentials/nep/nep89_20250409
```

Example pattern:

```text
fine_tune nep89_20250409.txt nep89_20250409.restart
type <your types>

# fixed by the foundation model
version    4
zbl        2
cutoff     6 5
n_max      4 4
basis_size 8 8
l_max      4 2 1
neuron     80

# tunable
lambda_1   0
lambda_e   1
lambda_f   1
lambda_v   1
batch      5000
population 50
save_potential 1000 0
generation 5000
```

Guards:

- Fine-tuning requires both the foundation `nep` model file and restart file.
- Do not alter architecture-defining parameters that the foundation model fixes:
  `version`, `zbl`, `cutoff`, `n_max`, `basis_size`, `l_max`, and `neuron`.
- Keep `type` aligned with the fine-tuning dataset and the foundation model's
  supported elements.
