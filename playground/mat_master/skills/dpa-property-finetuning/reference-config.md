# reference-config.md — DeePMD property finetuning `input.json`

Companion to [SKILL.md](SKILL.md). DeepMD-kit version differences may rename fields; align with your installed `deepmd-kit` docs.

Bundled in MatMaster as `playground/mat_master/skills/dpa-property-finetuning/`. For remote training, follow **SKILL.md**: submit via **`bohrium-job`** with image `registry.dp.tech/dptech/deepmd-kit:3.1.2` on a GPU machine.

## Top-level keys

| Key | Role |
|-----|------|
| `model` | `type_map`, `descriptor`, `fitting_net` (single-task) or multitask structure |
| `learning_rate` | Usually `type: exp` with `start_lr`, `stop_lr`, `decay_steps` |
| `loss` | For property: `type: property`, `loss_func`, `metric` |
| `training` | `training_data`, `validation_data`, steps, logging, checkpoints |

Optional keys (multitask / advanced): `model_prob`, shared dict references — see multitask section.

## `model.type_map`

- Ordered list of element symbols (e.g. H through Og for universal DPA).
- Every integer in `type.raw` is an index into this list.
- **Must match the pretrained checkpoint** you pass to `--finetune`.

## `model.descriptor`

- For DPA-3: typically `"type": "dpa3"` with a `repflow` block (`e_rcut`, `a_rcut`, `nlayers`, `e_sel`, `a_sel`, etc.).
- **Copy from the pretrained model** branch you finetune (inspect checkpoint or use project `inspection_summary.json`).
- Changing `sel` / cutoffs without retraining the backbone can break compatibility.

## `model.fitting_net` (single-task property)

| Field | Typical value | Notes |
|-------|----------------|-------|
| `type` | `"property"` | Enables property head |
| `property_name` | `"property"` | Label array name under `set.*` (usually `property.npy`) |
| `intensive` | `true` / `false` | Per-structure intensive vs extensive scalar |
| `task_dim` | `1` | Number of scalar outputs per frame |
| `neuron` | `[240,240,240]` | Match or override; often inherited from energy head width |
| `activation_function` | `"silu"` / `"tanh"` | Match pretrained style when sharing backbone |
| `resnet_dt` | `true` | Common for DPA fitting nets |
| `dim_case_embd` | varies | Case embedding dim; copy from checkpoint if present |
| `seed` | int | Reproducibility |

If the pretrained fitting net used `numb_fparam` / `default_fparam`, copy those into the new property head when needed.

## `loss` (property)

```json
{
  "type": "property",
  "loss_func": "mae",
  "metric": ["mae", "rmse"]
}
```

- `loss_func`: `mae`, `mape`, etc. (per project / DeePMD version).
- `metric`: logged validation metrics.

## `training`

| Field | Notes |
|-------|------|
| `training_data.systems` | List of strings: paths to each `deepmd/npy` system directory |
| `training_data.batch_size` | Integer or e.g. `"auto:256"` |
| `validation_data.systems` | Same layout; often `batch_size: 1` |
| `numb_steps` | Total optimization steps |
| `save_freq` / `disp_freq` | Checkpoint and log frequency |
| `disp_file` | e.g. `lcurve.out` |
| `max_ckpt_keep` | Rotate checkpoints |
| `gradient_max_norm` | Clip gradients |
| `seed` | Global training seed |

Paths in `systems` are often **relative to the cwd** where `dp --pt train` runs.

## Minimal property training snippet (illustrative)

```json
{
  "model": {
    "type_map": ["H", "He"],
    "descriptor": { "type": "dpa3", "repflow": {} },
    "fitting_net": {
      "type": "property",
      "property_name": "property",
      "intensive": true,
      "task_dim": 1,
      "neuron": [240, 240, 240],
      "activation_function": "silu",
      "resnet_dt": true,
      "seed": 1
    }
  },
  "learning_rate": {
    "type": "exp",
    "decay_steps": 1000,
    "start_lr": 1e-4,
    "stop_lr": 1e-8
  },
  "loss": {
    "type": "property",
    "loss_func": "mae",
    "metric": ["mae", "rmse"]
  },
  "training": {
    "training_data": { "systems": ["./data/train/sys0"], "batch_size": "auto:256" },
    "validation_data": { "systems": ["./data/val/sys0"], "batch_size": 1 },
    "numb_steps": 200000,
    "save_freq": 2000,
    "disp_freq": 100,
    "disp_file": "lcurve.out"
  }
}
```

Replace `descriptor.repflow` with the real block from your checkpoint.

## Multitask: `finetune_head` notes

Production pattern (prop_pred_abx `gen_exp4_configs.py`):

- **Energy head** loaded from pretrained single-task checkpoint: use `finetune_head: "Default"` so weights map to the original energy fitting net.
- **New property head** on the same shared descriptor: use `finetune_head: "RANDOM"` so the property output layer is initialized for property labels. Using `Default` can tie bias adjustment to the pretrained energy head and produce **broken scales** on property data.

Shared descriptor is usually factored through a `shared_dict` / string indirection (`type_map_all`, `dpa3_descriptor`) depending on DeePMD multitask JSON schema — follow a working `input.json` from the same `deepmd-kit` version.

## INVAR reference `input.json`

See `guomingyu/PROPERTIES_PREDICTION/INVAR-2025/public_release/INVAR-DART/finetune/config/input.json` for a full DPA-3 descriptor + property fitting_net + MAPE loss example.

## Command-line flags (training)

- `dp --pt train input.json --finetune model.pt`
- `--model-branch BranchName` when the checkpoint stores multiple branches (e.g. `Omat24`).

In MatMaster, prefer running the same command **inside** a Bohrium job (`bohrium-job` skill, image `registry.dp.tech/dptech/deepmd-kit:3.1.2`, GPU machine) with stdout/stderr redirected to `log` (see `bohrium-job` SKILL.md).

## Sanity checks before long runs

1. One validation system loads in Python via `dpdata.LabeledSystem(path, fmt="deepmd/npy")` without error.
2. `property.npy` exists and shape matches `task_dim` and frame count.
3. `type_map.raw` line count and order match `model.type_map`.
4. `dp --pt train` dry run or 10-step smoke test if supported.
