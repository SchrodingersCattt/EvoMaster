---
name: input-manual-helper
description: "Task = write/demo input file for LAMMPS MSST VASP ABACUS Gaussian CP2K QE etc? MUST use_skill this first: list_manuals.py → peek_manual.py --tree → peek_manual.py --section → write → validate_input.py → fix loop. Do not skip validation."
skill_type: operator
---

# Input Manual Helper Skill

Use when you need to **write** or **validate** input files for computational software (VASP, Gaussian, CP2K, LAMMPS, ABACUS, etc.). The manuals are JSON files with parameter names, types, syntax templates, and descriptions.

## Workflow (MANDATORY — do not skip any step)

1. **Discover manuals**: Run `list_manuals.py` to see available software manuals.
2. **Check for reference templates**: For CP2K, look in `references/cp2k/` for a template matching your calculation type (scf_energy, band_structure, geo_opt, cell_opt, md_nvt). For Gaussian, look in `references/gaussian/` for a template (nlo_shg, opt_freq, td_dft, sp_energy, ts_irc). **Start from the template** — do NOT write CP2K or Gaussian files from scratch. Read the template with `get_reference` (reference_name = `cp2k/<filename>.inp` or `gaussian/<filename>.gjf`) and adapt it.
3. **Overview**: Run `peek_manual.py --software X --tree` to see the section hierarchy and parameter counts.
4. **Detail**: Run `peek_manual.py --software X --section Y` for each section you need (e.g. `--section "FORCE_EVAL/DFT/SCF"` for CP2K). For flat manuals (VASP), just use `peek_manual.py --software VASP` (auto mode). Use `--sections "S1,S2,S3"` to batch-query.
5. **Write** the input file by adapting the template with parameter info from steps 3-4.
6. **Validate**: Run `validate_input.py --input_file <path> --software X`. This catches misspelled tags, wrong sections, keywords-as-subsections, type mismatches, and hallucinated parameters.
7. **Fix loop**: If validation reports errors, fix the file and re-run `validate_input.py` (up to 3 iterations). Only finish when validation passes.

## Scripts

- **list_manuals.py** — Lists available manual JSON files. Output: `software|absolute_path` per line.
  - Usage: `python list_manuals.py [base_path]`
  - Default base_path: this skill's `data/` directory.

- **peek_manual.py** — Smart manual reader with structured, LLM-friendly output (replaces raw peek_file on manual JSONs).
  - `--software X --tree` — Show section hierarchy with param counts (overview).
  - `--software X --section "SECTION/PATH"` — Show params for a specific section in compact table format.
  - `--software X --sections "S1,S2,S3"` — **Batch-query** multiple sections in ONE call (comma-separated). Use this to avoid multiple round-trips.
  - `--software X --section "SEC" --tree` — Show the **subtree** of section SEC (not the full tree). Good for exploring a large section.
  - `--software X --section "SEC" --search "KW"` — Search only **within** section SEC.
  - `--software X --search "KEYWORD"` — Search param names and descriptions for a keyword.
  - `--software X` (no flag) — Auto mode: compact table if ≤80 params, else tree.
  - **Performance tip**: Use `--sections "S1,S2,S3"` to query multiple sections in one call instead of calling peek_manual.py multiple times.
  - Usage: `python peek_manual.py --software CP2K --sections "FORCE_EVAL/DFT/SCF,FORCE_EVAL/DFT/XC,FORCE_EVAL/SUBSYS"`

- **validate_input.py** — Validate a written input file against its software manual.
  - Checks: unknown tags (with "did you mean?" suggestions), section placement (CP2K), value type mismatches.
  - Supports: KEY_VALUE (VASP/ABACUS), HIERARCHICAL_BLOCK (CP2K), KEYWORD_LINE (LAMMPS), NAMELIST (QE), JSON (DeePMD/DP-GEN).
  - Usage: `python validate_input.py --input_file /path/to/INCAR --software VASP`
  - Exit code 0 = pass, 1 = errors found.

## Reference Templates

Reference templates are complete, working input files for common calculation types. **Always start from a template** instead of writing from scratch.

Available in `references/cp2k/`:
- `scf_energy.inp` — Single-point SCF energy (DFT/GPW)
- `band_structure.inp` — Band structure with k-path (metal example, correct SPECIAL_POINT syntax)
- `geo_opt.inp` — Geometry optimization (BFGS)
- `cell_opt.inp` — Variable-cell relaxation
- `md_nvt.inp` — NVT molecular dynamics (Nosé-Hoover)

Available in `references/gaussian/`:
- `nlo_shg.gjf` — Frequency-dependent first hyperpolarizability (SHG, β at 1064nm) with genecp for transition metals. Shows correct section ordering: basis → ECP → frequency.
- `opt_freq.gjf` — Geometry optimization + frequency calculation
- `td_dft.gjf` — TD-DFT excited states
- `sp_energy.gjf` — Single-point energy
- `ts_irc.gjf` — Transition state search + IRC (multi-step with --Link1--)

To use: `use_skill` with `action='get_reference'`, `reference_name='cp2k/band_structure.inp'` or `reference_name='gaussian/nlo_shg.gjf'`.

## When to use

- **Before writing** any software input file: always run peek_manual.py to get correct parameter names and syntax.
- **After writing** any software input file: always run validate_input.py to catch errors before finishing the task.
- **For debugging**: if a user reports that a written input file has errors, run validate_input.py first to identify the problems.

## Important

- **Do NOT** use `peek_file` on the raw manual JSONs — they can be multi-MB and will flood context. Always use `peek_manual.py` instead.
- **Do NOT** skip validation. The validate → fix loop is mandatory.
- **Do NOT** rely on web search or memory alone for parameter names — always check the manual via peek_manual.py.
- If validate_input.py reports an unknown tag and no suggestion, the tag may be valid but absent from the manual sample. Use your domain knowledge to decide, but always investigate first.
