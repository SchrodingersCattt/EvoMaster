---
name: input-manual-helper
description: "Write/validate input files for CP2K, QE, ABINIT, LAMMPS, ORCA, etc. Flow: query strategy → rough draft → hand to prepare_* MCP tools for final input → validate once and refine only if needed. Do not hardcode MCP capabilities; use tool schema as source of truth."
skill_type: operator
---

# Input Manual Helper Skill

Write or validate input files for computational software (CP2K, QE, ABINIT, LAMMPS, ORCA, VASP, Gaussian, etc.). The workflow is principle-based: produce a rough draft from references, delegate to the appropriate prepare_* MCP tool for path/asset binding and (where supported) workflow generation, then validate and refine only when necessary.

## Workflow (three phases)

### 1. Draft

Use a **query strategy** to obtain a rough input:

- Run `list_references.py` (optionally `--software X`) to discover available templates.
- Fetch templates via `get_reference` (e.g. `reference_name='cp2k/task_opt.inp'`) and merge/adapt as needed (coordinates, cell, elements, k-points, placeholders for structure/pseudo paths).
- Prefer templates over constructing complex blocks from the manual. Placeholders for paths or assets are fine; the prepare tool will resolve them.

For software with no template, draft from domain knowledge and minimal manual lookup; avoid repeatedly querying the manual line-by-line to build the whole file.

### 2. Finalize

Call the **prepare_* MCP tool** for the target software (e.g. mat_binary_calc_prepare_cp2k_job, prepare_abinit_job, prepare_lammps_job). Capabilities and parameters are defined by the MCP server — **always check the tool’s schema/description**; do not assume a fixed list of features.

- **Workflow-aware tools**: Some prepare tools can generate full multi-step inputs (e.g. DFPT, NLO/SHG, phonons) via a workflow parameter (e.g. `workflow_type`). Before calling, check the tool schema for such parameters and use them instead of manually assembling multiple datasets or input files.

### 3. Validate & refine

Run **one** validation pass (e.g. `validate_input.py --input_file <path> --software X`). Refine only if:

- Exit code is non-zero or the report shows errors; or
- The user has stated extra constraints that are not yet reflected.

Use the validation report (line numbers and messages) to fix the file or adjust prepare parameters and re-run prepare/validate until pass.

**Validation fallback**: If `validate_input.py` reports "Manual is empty" or "No parser available" for a given software, the manual or parser for that code is not yet set up. Do not block the flow: treat the MCP prepare tool output and domain knowledge as the quality source, and note the limitation. Do not retry validation in a loop when the manual is known to be missing.

## Scripts

- **list_references.py** — Discover reference templates by software; use for the draft phase.
- **get_reference** (via use_skill) — Fetch template content by name (e.g. `cp2k/task_opt.inp`, `abinit/gs_scf.abi`, `lammps/msst_shock.lammps`).
- **validate_input.py** — Validate prepared input; exit 0 = pass, 1 = errors. Use after the prepare step.
- **peek_manual.py** — Targeted manual lookup when validation flags a section or when a specific parameter is uncertain. Use sparingly; avoid building whole sections from repeated manual queries.
- **list_manuals.py** — List available manual JSON files (`software|path`).

## Reference templates

Templates live under `references/<software>/`. Run `list_references.py` to see what is available.

### CP2K naming

- **task_*** — What to calculate (e.g. SCF, GEO_OPT, BAND). Pair with a **method_*** for non-PBE functionals.
- **method_*** — How (XC, ADMM, HF blocks). Merge into a task_ template.
- **No prefix** — Standalone; use as-is.

Do not construct &HF, &SCREENING, &ADMM, &GW, etc. from the manual; get them from method_* templates.

### Other software

ABINIT, LAMMPS, ORCA, Gaussian, PSI4, etc. have standalone or minimal templates; see `_co_templates.json` hints. For ABINIT NLO/SHG, use the base GS template and pass `workflow_type='nlo'` to prepare_abinit_job instead of hand-editing multiple datasets.

## Principles

- **Do not** use `peek_file` on manual JSONs (large); use `peek_manual.py`.
- **Do not** skip validation when the manual/parser exists; run it once after prepare.
- **Do not** assume prepare tools are fixed in capability; read the current MCP tool schema.
- **Do not** repeatedly query the manual for the same section that returns "No params found"; use templates or domain knowledge instead.
- When the manual is incomplete or missing, rely on prepare output and domain knowledge rather than blocking on validation.
