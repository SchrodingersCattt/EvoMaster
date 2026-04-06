---
name: dpa-property-finetuning
description: "Fine-tune pretrained DPA checkpoints for scalar property prediction with DeePMD-kit PyTorch (property head, MAPE/MAE loss). Use after deepmd/npy data exist. For dataset build/split/mixed IO use dpdata-dataset. Training must be submitted remotely via bohrium-job with image registry.dp.tech/dptech/deepmd-kit:3.1.2 on a GPU machine — not local dp --pt train unless user explicitly requests local debug."
skill_type: operator
---

# DPA property finetuning

## Pipeline

1. **Data**: Prepare `deepmd/npy` (or mixed) with **`dpdata-dataset`** (`pack_structure_to_npy.py`, `prep_property_npy.py`, splits, `fix_property_npy.py`).
2. **Config**: Start from a working property `input.json` (see [reference-config.md](reference-config.md)). Merge train/validation system paths with `gen_finetune_config.py`.
3. **Train (Bohrium)**: Package the working directory (template `input.json`, pretrained `.pt`, data paths consistent with job cwd) and submit with **`bohrium-job`**:
   - `use_skill` skill_name=`bohrium-job` action=`run_script` script_name=`submit_job.py`
   - `--input-dir` = directory containing `input.json`, lists, and relative data layout
   - `--image` = `registry.dp.tech/dptech/deepmd-kit:3.1.2`
   - `--machine` = a **GPU** SKU from `list_machines.py --type gpu` (do not use CPU-only defaults for finetune)
   - `--cmd` must redirect to `log`: e.g. `dp --pt train input.json --finetune /path/to/DPA-3.1-3M.pt --model-branch Omat24 > log 2>&1`
4. **Poll**: `use_skill` skill_name=`bohrium-job` action=`run_script` script_name=`poll_job.py` with `script_timeout` = max_polls * poll_interval (default 86400). **Do not** use built-in `monitor_job` for this path.
5. **Inference**: After checkpoints exist, `run_inference.py --model ... --list systems.txt`.

## Scripts (this skill)

| Script | Role |
|--------|------|
| `gen_finetune_config.py` | Inject `training.training_data.systems` / `validation_data.systems` from list files |
| `prep_property_npy.py` | Optional duplicate of dpdata-dataset helper: numpy arrays → one `deepmd/npy` system |
| `run_inference.py` | `DeepProperty` batch eval vs labels in data |

## References

- [reference-config.md](reference-config.md) — `input.json` keys, property `fitting_net`, multitask `finetune_head`
- INVAR: `public_release/INVAR-DART/finetune/config/input.json`

## Rules

- Default finetune execution path is **Bohrium** + **`deepmd-kit:3.1.2`** + **GPU**.
- Use **`bohrium-job`** `submit_job.py` then `poll_job.py`; follow log naming `> log 2>&1` in `--cmd`.
- Dataset manipulation belongs in **`dpdata-dataset`**, not here.
