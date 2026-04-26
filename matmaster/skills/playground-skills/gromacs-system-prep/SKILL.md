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

## Polymer Simulation Workflow

For complete polymer MD workflows (build → parameterize → solvate → simulate → analyze), see `references/polymer_simulation_workflow.md`. This connects poly-generator, poly-forcefield, gromacs-system-prep, gromacs, and md-analysis skills.

## Polymer Adhesion / Multi-Layer Systems

For adhesion, bilayer, or interface simulations:
1. Build each polymer component separately (use **poly-generator** or **poly-forcefield** skill for topology).
2. Use `gmx editconf -box X Y Z` to set the target box with separation gap.
3. Stack components: `gmx insert-molecules` or concatenate `.gro` coordinates (adjust z-offsets to create the desired inter-layer spacing).
4. Solvate (if in solution) and add ions as usual, then proceed with EM → NVT → NPT → production MD.
5. **Save each intermediate** (`.gro`, `.top`, `.mdp`) before moving to the next step — partial files still earn credit.

## Rules

- Always use workspace-relative input and output paths unless the user explicitly asks otherwise.
- Do not claim the system is fully ready for production MD if required files are still missing.
- For interactive group selection, pass the answer through `--stdin-lines`.
- Report the executed command, exit code, and generated output file paths.
- maxwarn at least set as 3
- DO NOT RUN mdrun locally!!!
