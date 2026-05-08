# Input Preparation Workflow Contract

This contract defines what the input-manual-helper skill must produce before a
calculation is submitted locally or through Bohrium.

## Ready Input Check

Before rendering anything, inspect the workspace for a complete user-provided
input directory. Treat it as ready only when all of these hold:

- The primary input file exists and matches the requested engine.
- Required companion files are present or intentionally unnecessary.
- The user did not request structural or parameter changes.
- The file names referenced inside the input match files in the directory.

If the input is ready, do not call the renderer. Diagnose and package the
existing files instead.

## Script Resolution

Resolve the script path from the skill directory, not from the user's current
workspace:

```bash
uv run python <skill_dir>/scripts/render_input.py --software <engine> --task <task> --output <input_file>
uv run python <skill_dir>/scripts/diagnose_input.py --software <engine> --input <input_file> --json_out diagnosis.json
uv run python <skill_dir>/scripts/write_manifest.py --software <engine> --task <task> --input-dir <run_dir> --diagnosis <run_dir>/diagnosis.json
```

Use `--format json` when JSON should also be printed to stdout. Use
`--json_out` whenever the result must be saved as an artifact.

## Diagnostic Gate

Diagnosis output must be reviewed before submission.

- `error`: blocks submission until fixed, downgraded with evidence, or confirmed
  by the user.
- `blocker`: blocks submission. If a validator does not expose a dedicated
  blocker field, treat severe file-missing or contradiction diagnostics as
  blockers in the manifest.
- `warning`: may proceed only with a short rationale in the manifest.
- `info`: record only when useful.

The scripts may exit nonzero when errors are found. That is a signal to repair
or pause, not a reason to discard the diagnosis.

## Auxiliary Files

Gather all required files into one input directory before submission:

- Generated primary input files.
- User-provided structure, topology, model, or coordinate files.
- Pseudopotentials, basis sets, orbital files, force-field files, or scripts.
- Any generated fixed input file chosen after diagnosis.

The manifest must list missing files explicitly and set `submit_ready` to false.

## Manifest Schema

Write `input_prep_manifest.json` in the prepared input directory. Prefer
`scripts/write_manifest.py` so diagnostics are normalized and required fields
are not omitted.

```json
{
  "software": "engine name",
  "task": "task type",
  "input_dir": "directory submitted or prepared",
  "generated_files": ["files created by scripts or this workflow"],
  "user_provided_files": ["files reused from the workspace"],
  "diagnostics": {
    "file": "diagnosis.json",
    "errors": 0,
    "warnings": 0,
    "blockers": 0
  },
  "auxiliary_files": ["pseudopotentials, basis, topology, scripts, etc."],
  "assumptions": ["short scientific or operational assumptions"],
  "submit_ready": true,
  "bohrium_command": "command planned for execution"
}
```

Use an empty list for absent file groups. Do not omit fields.
Keep `bohrium_command` present even when `submit_ready` is false; use an empty
string if no command is valid until blockers are fixed.

## Submission Rule

Submit only when `submit_ready` is true. If the run is submitted, the final
answer should name the manifest, primary input file, diagnosis file, and the
job id or submission status.
