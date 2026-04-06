---
name: gromacs-system-prep
description: Prepare local GROMACS systems from workspace files. Use when the task is to build a simulation box, solvate a system, add ions, make index groups, or generate standard `.mdp` files for EM/NVT/NPT/MD.
skill_type: operator
---

# GROMACS System Prep

Use the local wrapper under `matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py`.

## When to use

- Build or resize a simulation box with `editconf`.
- Solvate an existing structure with `solvate`.
- Add ions with `genion`.
- Create or edit index groups with `make_ndx`.
- Write standard `.mdp` files for `em`, `nvt`, `npt`, or `md`.

## Workflow

1. Confirm the required input files already exist in the current workspace.
2. Keep all generated files inside the active workspace.
3. Use the structured subcommands instead of a raw GROMACS command whenever possible.
4. For `genion`, make sure the needed `.tpr` already exists before running.
5. If the local `gmx` executable is missing, stop and report that the local GROMACS environment is unavailable.

## Script

- `matmaster/skills/playground-skills/gromacs-system-prep/scripts/prepare_gmx.py`

## Rules

- Always use workspace-relative input and output paths unless the user explicitly asks otherwise.
- Do not claim the system is fully ready for production MD if required files are still missing.
- For interactive group selection, pass the answer through `--stdin-lines`.
- Report the executed command, exit code, and generated output file paths.
- maxwarn at least set as 3
- DO NOT RUN mdrun locally!!!
