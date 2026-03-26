---
name: md-analysis
description: Run local GROMACS trajectory analysis on workspace files. Use when the task is to analyze MD results with commands like `rmsd`, `rmsf`, `gyrate`, `msd`, `rdf`, `hbond`, or `energy`, and summarize the generated `.xvg` outputs.
skill_type: operator
---

# MD Analysis

Use the local wrapper under `playground/mat_master/skills/md-analysis/scripts/analyze_gmx.py`.

## When to use

- Analyze a finished trajectory or energy file already present in the workspace.
- Generate `.xvg` outputs for RMSD, RMSF, radius of gyration, MSD, RDF, hydrogen bonds, or energy terms.
- Produce a quick numeric summary from the generated `.xvg` file.

## Workflow

1. Confirm the required trajectory, structure, or energy files already exist in the workspace.
2. Use the structured analysis subcommands before falling back to generic GROMACS execution.
3. For commands that require group selection, pass the answer through `--stdin-lines`.
4. Keep output `.xvg` files inside the active workspace.
5. Report both the output file path and the summary statistics.

## Script

- `playground/mat_master/skills/md-analysis/scripts/analyze_gmx.py`

## Rules

- Always use files from the current workspace unless the user explicitly says otherwise.
- Do not claim physical conclusions from a failed or empty analysis output.
- If the `.xvg` file is empty or malformed, surface that clearly instead of fabricating statistics.
- Report the executed command, exit code, output file path, and summary stats.
