---
name: composition-optimization
description: Orchestrates multi-component composition optimization for target properties. Use when users ask for alloy/composition search, DART GA optimization, seed composition design, or composition-only requests that must be converted to explicit structures. Handles branches for missing initial data (deep-survey + lit-data-organizer), optional surrogate models, and composition-to-structure heuristics required by DPA.
skill_type: operator
---

# Composition Optimization Skill

A routing skill for composition-design workflows with explicit decision branches:

1. Objective/constraint normalization
2. Initial candidate acquisition
3. Surrogate-aware optimization routing (DART GA when available)
4. Composition-to-structure conversion when only formula/composition is provided
5. Validation and result packaging

## When to use

- "Optimize alloy/composition for target property."
- "Use genetic algorithm or run_dart_ga for composition search."
- "I only have composition/formula, please generate usable structures."
- "Build initial candidates from literature, then optimize."

## Workflow

1. **Normalize the task**
   - Extract objective(s), constraints, and search space from user input.
   - Record whether the user provided:
     - initial candidate data
     - surrogate model
     - explicit structures

2. **Prepare initial candidates**
   - If user provided candidate compositions, clean and standardize them.
   - If not provided:
     - If user asks for comprehensive report/file output, use `deep-survey`.
     - Build a canonical evidence table with `lit-data-organizer` before sampling seeds.

3. **Route by surrogate-model availability**
   - If a surrogate model is provided and DART GA tool is available, run GA optimization.
   - If no surrogate model is provided, do not force DART GA; return a staged fallback:
     - build initial candidate set
     - estimate properties with available screening tools
     - optionally request/propose surrogate model training before GA

4. **Composition -> structure heuristic (mandatory when structure is missing)**
   - Use the heuristic in [composition_to_structure_heuristics.md](reference/composition_to_structure_heuristics.md).
   - Generate candidate structures via `mat_struct_db_*` / `mat_sg_*` tools.
   - Ensure each structure has explicit lattice, coordinates, and atom-type mapping for downstream DPA tools.
   - Validate each new structure using `structure-manager` (`assess_structure.py`).

5. **Report results**
   - Provide ranked candidate compositions and associated structures.
   - Include source/provenance of each candidate (user input, DB, generated heuristic, literature evidence).
   - Explicitly disclose assumptions and approximations.

## Decision contract

| Inputs | Required path |
|---|---|
| Initial data: Yes, Surrogate: Yes | Normalize -> optional structure check -> run DART GA |
| Initial data: Yes, Surrogate: No | Normalize -> structure generation/validation if needed -> screening/fallback (no forced DART GA) |
| Initial data: No, Surrogate: Yes | Collect evidence -> lit-data-organizer -> seeds -> composition->structure if needed -> run DART GA |
| Initial data: No, Surrogate: No | Collect evidence -> lit-data-organizer -> seeds -> composition->structure if needed -> screening/fallback |

## Tool usage notes

- This skill is guidance-only (operator type); it has no runnable scripts. Do NOT call `action=run_script` for this skill.
- To load the workflow, call `use_skill` with `action=get_info`.
- DART GA tool names are server-prefixed at runtime; prefer:
  - `mat_compdart_submit_run_dart_ga` if registered
  - otherwise, detect available tool ending with `_run_dart_ga`
- If user already provides surrogate model URL/path, pass it directly as `targets[*].model_path` in the first DART GA attempt.
- Do not download/unzip/inspect `.pt`/`.zip` surrogate files with local shell before first CompDART attempt, unless CompDART returns explicit model-format incompatibility and asks for conversion.
- Structure-generation and validation stack:
  - `mat_struct_db_*` / `mat_sg_*` for candidate structures
  - `structure-manager` -> `assess_structure.py` for sanity checks

## Rules

- Do not run DART GA when surrogate model is absent unless user explicitly requests an exploratory run and acknowledges the limitation.
- Do not fabricate structure details when only composition is given; use heuristic generation and mark uncertainty.
- For direct mode, avoid heavy deep-survey unless user explicitly asks for report/file output.
- When calculation tools need file/path arguments, follow OSS URL requirements from global tool rules.
