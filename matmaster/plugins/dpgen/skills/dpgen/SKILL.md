---
name: dpgen
description: "Use for DP-GEN / DeePMD active-learning and Deep Potential training workflows: preparing and checking param.json and machine.json, train-explore-label iteration planning, model-deviation campaigns, FP labeling setup, and launch-readiness triage. NOT for pretrained MLIP inference/optimization/MD/phonon/NEB; use mlips for those."
---

# DP-GEN Skill

Use this skill when the user wants to build, repair, validate, or plan a DP-GEN workflow for DeePMD/Deep Potential potential training.

## Scope

Use this skill for:

- DP-GEN `param.json` and `machine.json` preparation or repair.
- DeePMD / Deep Potential training input setup.
- Active-learning train → explore/model-deviation → FP label → retrain workflow planning.
- LAMMPS exploration and CP2K/VASP/QE/ABACUS FP labeling configuration inside DP-GEN.
- Launch-readiness checks before a DP-GEN dry run or submission.
- Explaining DP-GEN configuration or validation errors in user-facing language.

Do not use this skill for one-off inference with an existing pretrained potential. For pretrained DPA/MACE/SevenNet/MatterSim optimization, MD, phonon, elastic, adsorption, or NEB calculations, use `mlips` instead.

## Mandatory JSON validation gate

Before declaring DP-GEN inputs ready, always validate both `param.json` and `machine.json` with the DP-GEN LSP CLI if it is available:

```bash
dpgen-lsp-tool check param.json --fail-on-blocking
dpgen-lsp-tool check machine.json --fail-on-blocking
```

Record the result in a validation artifact when the user asks for reports or readiness decisions. The artifact should include:

- `commands`: commands attempted.
- `files_checked`: checked files.
- `tool_available`: whether the CLI was available.
- `diagnostics`: parsed or summarized diagnostics.
- `expected_path_warnings_ignored`: path diagnostics that are expected because the user used placeholders such as `/path/to/...` or `path/to/xx`.
- `blocking_findings`: syntax, schema, or semantic findings that block launch.

If `dpgen-lsp-tool` is unavailable, do not claim the inputs are ready. State that validation is incomplete and explain how to install the checker.

## Installing the checker

Prefer the active environment used for the task:

```bash
uv pip install git+https://github.com/SchrodingersCattt/dpgen-lsp.git@main
```

or, for a user-level tool:

```bash
uv tool install git+https://github.com/SchrodingersCattt/dpgen-lsp.git@main
```

If `uv` is unavailable, use the project environment’s package installer. Rich DP-GEN schema checks may also require DP-GEN-side packages such as `dpgen`, `dargs`, and `dpdispatcher`.

Useful commands:

```bash
dpgen-lsp-tool capabilities
dpgen-lsp-tool check param.json --fail-on-blocking
dpgen-lsp-tool check machine.json --fail-on-blocking
dpgen-lsp-tool context param.json --line 1 --character 1
dpgen-lsp-tool hover param.json --line 1 --character 1
dpgen-lsp-tool complete param.json --line 1 --character 1
dpgen-lsp-tool symbols param.json
dpgen-lsp-tool fix param.json --line 1 --character 1
```

`fix` is advisory/preview only. Do not blindly auto-apply a fix without preserving the user’s scientific intent.

## Path placeholder rule

Users often do not know final paths yet and may use placeholders such as `/path/to/...` or `path/to/xx`.

- Missing-path diagnostics for these placeholders are expected preflight warnings.
- Classify them as `advisory`, `needs_replace`, or `non_blocking` unless the user explicitly asked for a runnable final package.
- Do not label placeholder path warnings as JSON syntax errors.
- Do not fabricate real filesystem paths, dataset paths, pseudopotential paths, image names, queues, hosts, or credentials.

## Repair rules

When repairing DP-GEN JSON:

1. Validate first and identify the smallest blocking issue.
2. Fix syntax errors with minimal edits.
3. Preserve scientific settings unless the user explicitly asks to redesign them.
4. Re-check the repaired file if the checker is available.
5. Separate syntax errors from schema/semantic issues and placeholder path warnings.

## Workflow planning rules

For train/explore/label/retrain plans:

- Identify the target chemistry, temperature and pressure ranges, exploration engine, FP engine, and descriptor/training family from the user’s request.
- For LAMMPS exploration + CP2K FP workflows, ensure the plan includes model-deviation selection, FP labeling queue, data merge, and retraining gates.
- If the user asks for an init dataset plan, specify structure classes, coverage targets, labeling method, and minimum metadata required, but do not invent unavailable datasets.
- For DFT settings from an external paper, use only provided excerpts or cite that details must be supplied; do not fabricate exact CP2K parameters from memory.

## Output expectations

When asked for readiness or validation reports, make them machine-readable where possible. Recommended fields:

```json
{
  "commands": [],
  "files_checked": [],
  "tool_available": true,
  "diagnostics": [],
  "expected_path_warnings_ignored": [],
  "blocking_findings": [],
  "readiness": "ready|blocked|needs_review",
  "reason": "short user-facing reason"
}
```

Keep final answers concise and user-facing. Mention that placeholder paths must be replaced before a real run.
