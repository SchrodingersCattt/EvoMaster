---
name: grompp-remote-mdrun
description: Run `gmx grompp` locally inside the current workspace, generate a `.tpr`, and prepare MCP submit arguments for remote `gmx mdrun`. Use when `.mdp` is generated locally and only the production `mdrun` should be submitted remotely.
skill_type: operator
---

# Grompp Remote Mdrun

Use the local script under `playground/mat_master/skills/grompp-remote-mdrun/scripts/prepare_remote_mdrun.py`.

## When to use

- The `.mdp`, `.gro`, and `.top` files are already in the current workspace.
- You want `gmx grompp` to run locally.
- You only want the generated `.tpr` to be uploaded and executed remotely with `gmx mdrun`.

## Workflow

1. Confirm the required local files already exist in the current workspace.
2. Run `prepare_remote_mdrun.py` to execute local `grompp` and generate the `.tpr`.
3. Read the returned `submit_tool` and `submit_args`.
4. Call the MCP submit tool named in `submit_tool` with the returned `submit_args`.

## Script

- `playground/mat_master/skills/grompp-remote-mdrun/scripts/prepare_remote_mdrun.py`

## Rules

- Do not call blocked async tool `mat_binary_calc_run_gromacs` for this workflow.
- Use `mat_binary_calc_submit_run_gromacs` with the returned `submit_args`.
- Keep all local input and output files inside the active workspace when possible.
- If local `gmx` is missing or `grompp` fails, stop and report the exact failure instead of pretending the remote submit is ready.
