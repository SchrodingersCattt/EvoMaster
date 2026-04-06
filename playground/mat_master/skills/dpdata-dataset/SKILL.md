---
name: dpdata-dataset
description: "Build and manipulate DeePMD deepmd/npy datasets with dpdata. Use when you have structures (CIF/JSON) or raw numpy arrays plus scalar labels and need LabeledSystem export, train/valid splits, deepmd/npy/mixed IO, mixed-to-per-system conversion, or property.npy repair. Not for alloy composition heuristics, template supercell generation, finetune config, or Bohrium job submission — use other skills for those."
skill_type: operator
---

# dpdata dataset (deepmd/npy)

## Scope

This skill covers **dpdata-only** dataset plumbing for DeePMD-kit property workflows:

- `pymatgen.Structure` + scalar label → `deepmd/npy`
- Raw `coord`/`box`/`types` arrays → `deepmd/npy` (`prep_property_npy.py`)
- Load/split `deepmd/npy` or `deepmd/npy/mixed` into train/valid trees
- K-fold style splits over per-system mixed directories
- Expand one mixed directory into per-system `deepmd/npy` folders
- Copy `energy.npy` → `property.npy` when the pipeline uses energy as the property channel

**Out of scope** (do not use this skill for):

- Mass/molar fraction normalization, FCC/BCC/HCP template choice, random substitution supercells — upstream structure generation
- Editing `input.json` or running `dp --pt train` — use **`dpa-property-finetuning`**
- Submitting jobs — use **`bohrium-job`**

## Handoff

After datasets are ready, use **`dpa-property-finetuning`** for `input.json`, finetune semantics, and remote training via **`bohrium-job`**.

## Scripts

| Script | Role |
|--------|------|
| `pack_structure_to_npy.py` | One structure file (CIF or pymatgen JSON) + `--property` + `--type-map` → one `deepmd/npy` system dir |
| `prep_property_npy.py` | Numpy arrays + type map → one `deepmd/npy` system (`energy.npy` + `property.npy`) |
| `split_train_valid.py` | Split systems under a parent dir into `train/` and `valid/` (mixed or plain) |
| `split_5fold.py` | 5-fold val sets from `datasets/*` mixed dirs; optional holdout test fraction |
| `mixed_to_npy.py` | One `deepmd/npy/mixed` dir → per-system `deepmd/npy` under `--out-root` |
| `fix_property_npy.py` | For each `set.*/` under train/valid/test trees, copy `energy.npy` → `property.npy` if missing |
| `split_and_organize.py` | Copy or symlink named system dirs into `train/` and `val/` from ID list files |

### Examples (via use_skill)

    use_skill dpdata-dataset run_script pack_structure_to_npy.py --structure ./struct.cif --property 12.3 --type-map ./type_map.txt --out ./datasets/sys0
    use_skill dpdata-dataset run_script split_train_valid.py --datasets-root ./datasets --ratio 0.1
    use_skill dpdata-dataset run_script mixed_to_npy.py --mixed-dir ./valid --out-root ./valid_npy
    use_skill dpdata-dataset run_script fix_property_npy.py

## Reference

- [reference-dpdata-api.md](reference-dpdata-api.md)
- INVAR-style pipelines: `iter00.finetune`, `iter03.finetune` under `PROPERTIES_PREDICTION/INVAR-2025`

## Tool (via use_skill)

- `get_info` for full guidance
- `get_reference` with `reference_name=reference-dpdata-api.md`
- `run_script` with `script_name` and `script_args` as above
