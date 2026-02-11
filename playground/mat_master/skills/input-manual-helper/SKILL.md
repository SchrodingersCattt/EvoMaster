---
name: input-manual-helper
description: "Write/validate input files for CP2K/Gaussian/ORCA/VASP/LAMMPS/QE/etc. Step 1: run list_references.py to see available templates. Step 2: fetch templates via get_reference. CP2K naming: task_* (what) + method_* (how) + no-prefix (standalone). For non-PBE CP2K tasks, fetch BOTH task_* AND method_*. Step 3: merge & adapt. Step 4: validate_input.py. NEVER construct &HF/&SCREENING/&ADMM from peek_manual.py."
skill_type: operator
---

# Input Manual Helper Skill

Write or validate input files for computational software (VASP, Gaussian, CP2K, LAMMPS, ABACUS, ORCA, etc.).

## Workflow (MANDATORY)

### Phase 1: Discover & Compose from Reference Templates (PRIMARY)

1. **Run `list_references.py`** to see available templates. Use `--software X` to filter.
   - Output shows templates grouped by prefix: `task_*`, `method_*`, standalone.

2. **Decompose the task** into features and pick templates by name:
   - CP2K prefix naming: `task_*` = what to calculate, `method_*` = functional/method, no prefix = standalone.
   - If the task needs a non-PBE functional, fetch BOTH a `task_*` AND a `method_*`.
   - If there's an exact standalone match, just fetch that one.

3. **Fetch templates** via `get_reference` for each selected template (e.g. `reference_name='cp2k/task_band.inp'`).

4. **Merge & adapt**: use `method_*` as base, graft `task_*` sections in. Replace coordinates, cell, elements, basis sets, k-paths, project name.

### Phase 2: Validate (MANDATORY)

5. **Validate**: `validate_input.py --input_file <path> --software X`.

6. **Consult manual only if needed**: `peek_manual.py` for specific parameters that validation flagged. Do NOT use it to write HF/ADMM/GW sections — the templates are authoritative.

7. **Fix loop**: fix errors, re-validate (up to 3 iterations). Finish when exit code 0.

### Anti-Pattern

Do NOT fetch one template then query peek_manual.py to construct advanced CP2K sections (&HF, &SCREENING, &INTERACTION_POTENTIAL, &WF_CORRELATION, &GW, &RI_RPA). These are NOT in the manual JSON. Always get them from templates.

## Scripts

- **list_manuals.py** — List available manual JSON files. Output: `software|path` per line.
- **list_references.py** — List available reference templates grouped by prefix. Use `--software X` to filter.
- **peek_manual.py** — Smart manual reader (replaces raw peek_file on manual JSONs).
  - `--software X --tree` — Section hierarchy overview.
  - `--software X --sections "S1,S2,S3"` — Batch-query multiple sections.
  - `--software X --search "KEYWORD"` — Search by keyword.
- **validate_input.py** — Validate input file against manual. Exit 0 = pass, 1 = errors.
  - Usage: `validate_input.py --input_file <path> --software X`

## Reference Templates

Templates are in `references/<software>/`. Run `list_references.py` to discover them dynamically.

### CP2K Naming Convention

- **`task_*`** — Task skeleton (WHAT to calculate). Uses basic PBE. **Pair with `method_*`** for non-PBE functionals.
- **`method_*`** — Method/functional (HOW to calculate). Contains XC, ADMM, HF sections. **Merge into a `task_*`**.
- **No prefix** — Complete standalone. Use as-is.

ORCA, Gaussian, PSI4 templates are all standalone (no merging needed).

## Expert Knowledge

`data/sob_expert_knowledge.json` — expert tips on functional/basis/dispersion selection, ORCA method hierarchy, CP2K ADMM tips, PSI4 SAPT tips. Query via `peek_manual.py --software sob_expert_knowledge`.

## Important

- Do NOT use `peek_file` on manual JSONs (multi-MB). Use `peek_manual.py`.
- Do NOT skip validation.
- Do NOT construct advanced CP2K sections from the manual. Use `method_*` templates.
- Do NOT call peek_manual.py repeatedly for sections that returned "No params found".
