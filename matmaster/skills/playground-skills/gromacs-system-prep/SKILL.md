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

## Polymer Simulation Workflows

For **any polymer MD task**, consult `references/polymer_simulation_workflow.md` first. It covers the complete pipeline: monomer SMILES → poly-generator → topology → box setup → EM/NVT/NPT/MD → analysis.

### Polymer Adhesion / Multi-Layer Systems

For adhesion, bilayer, or interface simulations:
1. Build each polymer component separately (use **poly-generator** for structure, **poly-forcefield** for topology).
2. Use `gmx editconf -box X Y Z` to set the target box with separation gap.
3. Stack components: `gmx insert-molecules` or concatenate `.gro` coordinates (adjust z-offsets to create the desired inter-layer spacing).
4. Solvate (if in solution) and add ions as usual, then proceed with EM → NVT → NPT → production MD.
5. **Save each intermediate** (`.gro`, `.top`, `.mdp`) before moving to the next step — partial files still earn credit.

### Key Polymer Settings
- **Longer equilibration**: Polymers need ≥ 1 ns NPT equilibration (vs ~100 ps for small molecules).
- **Timestep**: Start with 1 fs for initial equilibration, switch to 2 fs with LINCS for production.
- **maxwarn**: Set `--maxwarn 3` (or higher) in grompp — polymer topologies often generate harmless warnings.
- **Chain EM→NVT→NPT→MD in one `run.sh`** for Bohrium submission (see reference for template).

## Rules

- Always use workspace-relative input and output paths unless the user explicitly asks otherwise.
- Do not claim the system is fully ready for production MD if required files are still missing.
- For interactive group selection, pass the answer through `--stdin-lines`.
- Report the executed command, exit code, and generated output file paths.
- maxwarn at least set as 3
- DO NOT RUN mdrun locally!!!
