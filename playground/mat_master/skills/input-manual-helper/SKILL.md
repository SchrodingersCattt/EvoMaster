---
name: input-manual-helper
description: "Parameter-dispatch engine for CP2K, QE, ABINIT, LAMMPS, ORCA, PySCF. LLM outputs overrides and paths; prepare_* MCP tools generate inputs (PySCF uses run_pyscf direct run). Route by engine capability; validate by physical-sense review. Structure files must be pymatgen-instanceable."
skill_type: operator
---

# Input Manual Helper Skill

Generate or adapt input files for computational software by **dispatching parameters and paths** to the appropriate prepare_* MCP tool. Do not hand-write or text-edit input file contents for software that has a prepare_* tool; use overrides and structure_file/template paths instead.

## Routing by engine

| Engine | Route type | input_file | structure_file |
|--------|------------|------------|----------------|
| ABINIT (program=abinit) | Direct generation | Optional | Must be pymatgen-readable |
| QE pw.x | Direct generation | Optional | Must be pymatgen-readable |
| CP2K | Placeholder injection | Optional (use cp2k/minimal_periodic.inp if user provides none) | pymatgen-readable |
| ORCA | Flexible | Optional (see ORCA modes below) | pymatgen-readable |
| LAMMPS | Data decoupled | Optional | pymatgen-readable (prepare generates .data) |
| PySCF | Direct run (no prepare) | N/A | XYZ recommended; pymatgen-readable fallback |

Use the MCP tool schema as the source of truth for parameters; the table above is context only.

### ORCA input modes

ORCA's `prepare_orca_job` supports three input modes:

| Mode | input_file | structure_file | Behaviour |
|------|------------|----------------|-----------|
| Template + structure | Provided (template with `{{COORD}}` placeholder) | Provided | Placeholder is replaced with actual coordinates |
| Template only | Provided (template with inline coordinates) | Optional / omitted | Parameters and inline coords are modified in place |
| Structure only | Omitted | Provided | Tool builds a minimal input from scratch using the structure; falls back to `orca/minimal_molecule.inp` as the base |

When `input_file` is omitted, use `orca/minimal_molecule.inp` as the fallback template or let the tool build from scratch if the schema supports it. When the user supplies an existing `.inp` with inline coordinates (no placeholder), pass it as `input_file` without a separate `structure_file`.

### PySCF (run_pyscf)

PySCF is invoked via the **run_pyscf** MCP tool (direct run). There is no prepare_* step and no validate_input gate; call `run_pyscf` with `structure_file` and parameters.

- **structure_file**: Path to molecular structure. XYZ is preferred; other formats readable by pymatgen (e.g. CIF, POSCAR-style) are supported.
- **task**: `"single_point"` | `"optimize"` | `"tddft"` (frequency not implemented).
- **charge** (int): Total charge. **spin** (int): 2S (unpaired electrons).
- **method**: `"DFT"` | `"HF"` | `"MP2"` | `"TDHF"`. For DFT, set **functional** (e.g. `"B3LYP"`). **basis**: e.g. `"def2-SVP"`.
- **properties** (optional list): Subset of `["energy","dipole","mo_energies","homo_energy","lumo_energy","gap","density_matrix","mulliken_population"]`; default `["energy"]`.
- **scf** (optional dict): SCF overrides, e.g. `max_cycle`, `conv_tol`, `level_shift`, `diis_space`.
- **response** (optional dict): For `task="tddft"`, e.g. `{"n_states": 10}`.
- **work_dir** (optional): Output directory; defaults to `structure_file.parent`. **log_file** (optional): Log filename; default `"pyscf.log"` in work_dir.

Returns: `success`, `code`, `command`, `stdout`, `stderr`, `log_file` (Path), `properties` (dict of computed values), `result_files` (e.g. `optimized_structure`, `tddft_summary`, `density_matrix`, `mulliken`). For MP2 single_point, see `energy_mp2_corr_h` / `energy_mp2_total_h`; for TDDFT, see `tddft_summary` and `n_excitations`.

## Structure file format

`structure_file` must be in a format pymatgen can instantiate: e.g. CIF (`.cif`), VASP POSCAR/CONTCAR (no extension or `.vasp`), XYZ (`.xyz`), Materials Project JSON. LAMMPS `.data` is produced by prepare_lammps_job from the structure file; do not pass .data as structure_file. Do not use proprietary or single-software-only formats as the generic structure input.

## Workflow

1. **Choose software and task type** — Determine which prepare_* tool (or run_pyscf for PySCF) applies from the routing table and MCP schema. For PySCF, skip steps 2–6 and call **run_pyscf** with structure_file and parameters only.
2. **Resolve template** — For CP2K, obtain an input template (user-provided or get_reference). For ORCA, determine the input mode: template+structure, template-only (inline coords), or structure-only (omit input_file). Use get_reference for a suitable ORCA template when needed (e.g. `orca/minimal_molecule.inp`, `orca/std_dft.inp`).
3. **Confirm structure_file** — Ensure the structure path exists and is pymatgen-instanceable; do not assume formats the engine cannot read.
4. **Build overrides** — Set physical parameters (cutoff, functional, k-points, etc.) via the overrides dict exposed by the prepare_* schema; do not inject them by editing the template text.
5. **Call prepare_*** — Invoke the prepare_* MCP tool with input_file (template path), structure_file (when applicable), and overrides.
6. **Validate once** — Run `validate_input.py --input_file <path> --software <name>`. Validation is **physical-sense review**: check that key parameters are in a reasonable range, functional matches the system, and required sections are present. If something looks wrong, use ask_human(mode="timeout"); on timeout, treat as pass and proceed. The script exits 0 so submit is allowed.

## Scripts

- **list_references.py** — List available reference templates by software.
- **get_reference** (via use_skill) — Fetch template content by name (e.g. `cp2k/minimal_periodic.inp`, `orca/minimal_molecule.inp`, `abinit/gs_scf.abi`, `lammps/gcmc_adsorption.lammps`).
- **validate_input.py** — Run after prepare. Reads the prepared file and exits 0; you perform a physical-sense review. If doubtful, ask_human; on timeout, pass. Do not skip this step when submitting jobs for software covered by the validation gate.

## Physical checks to consider (not a procedure)

- Cutoff energy and grid settings appropriate for the basis and system size.
- Functional choice consistent with the system (e.g. hybrid for band gaps, meta-GGA when needed).
- K-point sampling consistent with cell size and symmetry.
- Required blocks or keywords present and not contradictory (e.g. SCF convergence, geometry/MD settings).

Use domain judgment; do not follow a fixed checklist.

## Knowledge source

When a parameter or keyword is uncertain, use **official documentation** with a site-restricted search (e.g. site:manual.cp2k.org, site:docs.lammps.org). Do not re-query the same path that already returned no useful result.

## Principles

- **Do not** directly edit .inp, .in, .abi, or other input file text for software that has a prepare_* MCP tool; use overrides and template/structure paths only.
- **Do not** assume prepare tools have fixed capabilities; read the current MCP tool schema.
- **Gaussian / PSI4**: No prepare_* tool yet; use reference templates as the final input and do not apply the prepare-only workflow or the validation gate to them.
- **PySCF**: Use **run_pyscf** only; no prepare_* or validate_input. Structure_file (XYZ or pymatgen-readable) and parameters are passed directly to the tool.
