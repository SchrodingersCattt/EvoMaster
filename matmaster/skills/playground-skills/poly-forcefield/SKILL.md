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

- **Best supported**: linear alkane SMILES (e.g., `CCCCCCCCCCC`) and simple organic molecules with GAFF-compatible atom types.
- The script uses GAFF (General Amber Force Field) atom typing, which covers a wide range of organic functional groups: alkyl, ether, ester, amide, aromatic, hydroxyl, carboxyl, amine, etc.
- The main output is a GROMACS topology file (`.top`) for a single molecule.
- **Always try the script first** for any organic polymer or small molecule. If typing fails, the error will indicate which atom types are unsupported.

## Workflow

1. **Always attempt topology generation first** — do not pre-judge scope from SMILES alone. The GAFF typing engine handles many common organic chemistries beyond simple alkanes.
2. If typing or parameter assignment fails, surface the failure message and the unsupported chemistry clearly.
3. For supported inputs, run the topology generator script and write outputs into the workspace.
4. Report the generated `.top` path and any obvious limitations in the result.
5. For the full polymer simulation pipeline (topology → box → MD), see `gromacs-system-prep` skill and its `references/polymer_simulation_workflow.md`.

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
