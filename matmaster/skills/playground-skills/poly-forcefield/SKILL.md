---
name: poly-forcefield
description: Generate an initial GROMACS topology for currently supported simple polymer-like systems using the local polyFF prototype. Use when the task is to parameterize a supported small molecule or linear alkane and export a `.top` file.
skill_type: operator
---

# Poly Forcefield

Use the current local prototype under `matmaster/skills/playground-skills/polyFF/`.
The public runnable entry for this skill is exposed at
`matmaster/skills/playground-skills/poly-forcefield/scripts/generate_gmx_top.py`,
which delegates to the current `polyFF` implementation.

## Current scope

- The currently documented runnable scope is linear alkane SMILES such as `CCCCCCCCCCC`.
- The main output is a GROMACS topology file (`.top`) for a single molecule.

## Workflow

1. Check whether the input is inside the current supported scope.
2. If it is out of scope, state the limitation clearly instead of pretending full force-field coverage exists.
3. For supported inputs, run the topology generator script and write outputs into the workspace.
4. Report the generated `.top` path and any obvious limitations in the result.

## Script path

- `matmaster/skills/playground-skills/poly-forcefield/scripts/generate_gmx_top.py`

## Example

```bash
python matmaster/skills/playground-skills/poly-forcefield/scripts/generate_gmx_top.py \
  --smiles CCCCCCCCCCC \
  --output workspace/c11.top \
  --molecule-name C11
```

## Rules

- Do not claim support for arbitrary polymers unless the local prototype actually supports them.
- If typing or parameter assignment fails, surface the failure and the probable unsupported chemistry.
- Always keep generated files inside the active workspace when possible.
- gaff用这个`matmaster/skills/playground-skills/polyFF/assets/gaff_min.dat`
