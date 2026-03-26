---
name: poly-generator
description: Build polymers or oligomers locally from monomer SMILES and sequence rules, then optionally generate 2D figures, Markdown reports, and 3D structure files. Use when the task is about polymer assembly, copolymer sequence expansion, polymer SMILES generation, or polymer structure visualization.
skill_type: operator
---

# Poly Generator

Use the local wrappers under `playground/mat_master/skills/poly-generator/scripts/`.

## Use cases

- Build a linear polymer or oligomer from monomer SMILES.
- Expand explicit, block, or random copolymer sequences.
- Generate a combined 2D figure and Markdown report.
- Export a 3D structure file from the generated polymer SMILES.

## Workflow

1. Collect monomer aliases and SMILES such as `A`, `B`, `C`.
2. Prefer marker-based stitching when the SMILES already contains `[*]`.
3. If chemistry cannot be inferred safely, stop and ask for reaction SMARTS or corrected monomer SMILES.
4. Run `build_polymer.py` first to generate the polymer SMILES and expanded sequence.
5. If the build succeeds, run `generate_2d.py` for a preview image and Markdown report.
6. If the user wants a structure file, run `generate_3d.py`.

## Script paths

- `playground/mat_master/skills/poly-generator/scripts/build_polymer.py`
- `playground/mat_master/skills/poly-generator/scripts/generate_2d.py`
- `playground/mat_master/skills/poly-generator/scripts/generate_3d.py`
- `playground/mat_master/skills/poly-generator/python_path.txt`

## Rules

- Do not silently modify the user's monomer core.
- Prefer `[*]` markers over guessed chemistry.
- Before calling `run_script`, first inspect `build_polymer.py` usage and then fill arguments strictly according to the script interface. Do not invent shorthand flags.
- `build_polymer.py` accepts `--monomers`, `--sequence`, `--mode`, `--counts`, `--fractions`, `--degree`, `--seed`, `--reaction-name`, `--reaction-smarts`, `--reaction-template`, `--list-reactions`, and `--output-json`.
- `generate_2d.py` accepts `--monomers`, `--sequence`, `--mode`, `--counts`, `--fractions`, `--degree`, `--seed`, `--output-dir`, `--prefix`, `--title`, `--polymer-title`, `--panel-title`, `--reaction-name`, `--reaction-smarts`, and `--reaction-template`.
- `generate_3d.py` accepts `--smiles`, `--output`, `--engine`, `--format`, and `--random-seed`.
- Do not use unsupported arguments such as `--monomer`, `--n`, or `--out-dir`.
- Do not invent cross-script arguments such as `--input-json` for `generate_2d.py` or `generate_3d.py`.
- `--monomers` must be a JSON object string or a JSON file path.
- For a homopolymer with 10 repeats, use `--sequence A10` together with `--monomers`, instead of inventing `--n 10`.
- If writing an output file, pass a full file path with `--output-json`, not a directory path.
- After `build_polymer.py` succeeds, reuse its returned `polymer_smiles` for `generate_3d.py`. Do not rerun `build_polymer.py` just to obtain the same SMILES again in the same turn.
- `generate_2d.py` does not consume `polymer.json`; it needs the original monomer/sequence arguments again.
- If the build script returns `needs_user_input=true`, ask the user for the missing chemistry instead of guessing.
- Always report the polymer SMILES, the expanded sequence, and every generated file path.
