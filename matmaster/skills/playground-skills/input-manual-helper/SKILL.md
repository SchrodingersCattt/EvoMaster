---
name: input-manual-helper
description: "Use when preparing, adapting, validating, or packaging runnable input files for computational chemistry or materials-science engines before local or Bohrium execution, especially when no MCP submit tool exists or the user asks for engine-specific input artifacts."
skill_type: operator
---

# Input Manual Helper

Use this skill as a thin input-preparation router. It decides whether existing
inputs are ready, runs the local generation/diagnosis scripts when needed, and
packages the run directory with a manifest that downstream execution can trust.

## When To Use

Use this skill when the user asks to:
- Generate or adapt input files for ABACUS, CP2K, QE, ABINIT, LAMMPS, ORCA, GROMACS, or PySCF.
- Validate an existing engine input before execution.
- Package an input directory for Bohrium submission.
- Convert task intent plus structure files into runnable engine artifacts.

Do not use it for VASP INCAR-only tasks, Gaussian/PSI4 template-only work, pure
postprocessing, or MCP tools that already provide a dedicated submit workflow.

## Core Rule

Never hand-write supported engine inputs as the first move. Resolve this skill's
directory, use the scripts in `scripts/`, diagnose the result, and record the
decision in `input_prep_manifest.json`.

Read the full workflow contract before acting:
`references/workflow_contract.md`

Use this route table to choose engine-specific handling:
`references/engine_routes.md`

## Standard Workflow

1. Resolve `skill_dir` to this skill directory.
2. Check for a complete ready-to-run user input. If it is ready and no changes
   are requested, skip rendering and package the existing directory.
3. Choose engine and task type using `references/engine_routes.md`.
4. For rendered engines, run:
   `uv run python <skill_dir>/scripts/render_input.py --software <engine> --task <task> ...`
5. Diagnose the primary input with JSON output:
   `uv run python <skill_dir>/scripts/diagnose_input.py --software <engine> --input <file> --json_out diagnosis.json`
6. Handle diagnosis errors or blockers before submission. Warnings may proceed
   only when the manifest records a concise rationale.
7. Gather required auxiliary files into one input directory.
8. Write `input_prep_manifest.json`.
9. Submit only if `submit_ready` is true and the relevant engine skill supplies
   or confirms the Bohrium image and command.

## Required Manifest

Every prepared run directory must include `input_prep_manifest.json` with:

```json
{
  "software": "cp2k",
  "task": "scf",
  "input_dir": "run_cp2k_scf",
  "generated_files": ["input.inp"],
  "user_provided_files": ["structure.cif"],
  "diagnostics": {
    "file": "diagnosis.json",
    "errors": 0,
    "warnings": 1,
    "blockers": 0
  },
  "auxiliary_files": [],
  "assumptions": ["PBE used because no functional was specified"],
  "submit_ready": true,
  "bohrium_command": "mpirun -np 32 cp2k.popt -i input.inp > log 2>&1"
}
```

If the input is not submit-ready, set `submit_ready` to false and explain the
blocking issue in `assumptions` or an adjacent report.

## Engine Boundaries

This skill owns workflow, script invocation, diagnosis, packaging, and manifest
creation. Engine-specific physical rules stay in the engine skill or in the
local validator implementation. When engine details are uncertain, consult the
engine skill first, then official documentation.

PySCF is the exception to rendered input generation: write a Python script
directly, but still check syntax, charge/spin, structure path, output fields,
and write the manifest.

## Common Mistakes

- Rendering over a complete ready input when the user asked only to run it.
- Calling `diagnose_input.py` without saving machine-readable diagnostics.
- Treating parser success as physical validity.
- Submitting while diagnosis has unhandled errors or blockers.
- Copying image, machine, or command defaults from memory instead of checking
  the relevant engine skill.
