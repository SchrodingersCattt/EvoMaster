---
name: structure-manager
description: "Skill for DOWNLOADING structures from a URL, VALIDATING atomic structures, and CONVERTING between file formats. Use when: 1) Download a CIF/POSCAR from a direct URL (fetch_web_structure.py). 2) Check validity (assess_structure.py: sanity check, dimensionality, formula). 3) Convert between formats: CIF/POSCAR/LAMMPS/XYZ/etc. (convert_format.py). Structure DB search and building (SMILES/prototypes) use MCP tools; no Materials Project API or local DB scripts."
skill_type: operator
---

# Structure Manager Skill

Handles downloading structure files from URLs, validating atomic structures (CIF, POSCAR, XYZ), and converting between file formats (dpdata). Database search and structure building are done via MCP tools.

## Workflow

1. **Acquisition** (use MCP):
   * **Database search**: Use MCP structure/database tools to search for structures (no Materials Project API or local search script).
   * **Build**: Use MCP Structure Generator for SMILES or crystal prototypes.
   * **Download from URL**: When you have a direct link, use `fetch_web_structure.py` to download the file.
2. **Validation (Mandatory)**:
   * **Assess**: ALWAYS run `assess_structure.py` on any new structure. It returns:
       * **Dimensionality**: 0D (Molecule), 1D (Wire), 2D (Slab), 3D (Bulk).
       * **Sanity**: Checks for overlapping atoms (< 0.5 Å), unreasonable bond lengths.
       * **Formula**: Chemical composition.

## Scripts

### 1. Download
* **fetch_web_structure.py**
    * **Usage**: `python fetch_web_structure.py --url "http://.../file.cif"`
    * **Description**: Downloads a structure file from a direct URL. Use MCP browser/search to find the URL first; this script does not call any external DB API (no Materials Project, etc.).

### 2. Validation
* **assess_structure.py** (Sanity Check & Dimensionality)
    * **Usage**: `python assess_structure.py --file "structure.cif"`
    * **Output JSON**: `{"is_valid": true, "dimensionality": "2D-Slab", "formula": "Au4", "warnings": ["Vacuum padding < 10A"]}`
    * **Logic**:
        * **Bulk vs Slab**: Vacuum gap > 15Å in one direction -> Slab; in 3 directions -> Molecule.
        * **Sanity**: Fails if `min_dist < 0.7 * covalent_radii_sum`.

### 3. Format Conversion
* **convert_format.py** (dpdata-based)
    * **Formats**: CIF, POSCAR, LAMMPS data/dump, XYZ, extXYZ, Gaussian, GROMACS, ABACUS, DeePMD, etc.
    * **Output JSON**: `{"success": true, "output": "POSCAR", "info": {"atom_names": ["O","H"], "natoms": 3, ...}}`
    * **Common usage**:
        * CIF -> POSCAR: `python convert_format.py --input struct.cif --output POSCAR --output-fmt vasp/poscar`
        * POSCAR -> LAMMPS data: `python convert_format.py --input POSCAR --output data.lmp --output-fmt lammps/lmp --type-map O H`
        * LAMMPS dump -> POSCAR: `python convert_format.py --input dump.lammpstrj --output POSCAR --output-fmt vasp/poscar --type-map O H`
        * LAMMPS full-style: `python convert_format.py --input data.lmp --output POSCAR --input-fmt lammps/lmp --output-fmt vasp/poscar --type-map O H --atom-style full`
    * **Key flags**:
        * `--type-map El1 El2 ...` — **REQUIRED** for LAMMPS formats. Maps integer atom types to element symbols (type 1=El1, type 2=El2).
        * `--atom-style atomic|charge|full|...` — LAMMPS column layout. Default `atomic`. Must match the source file; mismatched style = **silent misparse**.
        * `--frame N` — Select frame index (default 0). Use -1 for last frame.
    * **CIF handling**: dpdata has no native CIF reader. The script automatically loads CIF via pymatgen (preferred) or ASE, then passes the structure into dpdata.
    * **LAMMPS atom_style notes**:
        * **Reading**: Always specify `--atom-style` to match the source file (e.g., `full` for molecular systems). Auto-detection can fail silently.
        * **Writing**: For non-atomic styles (full, charge), the script goes through ASE's `lammps-data` writer (with `specorder=type_map` to guarantee correct type numbering). dpdata's own writer only supports atomic style. Charges default to 0.0 if not present in source.

## When to use

* "Search for / get me the crystal structure of X." -> Use MCP structure/database tools (not this skill).
* "I have a CIF URL, download it." -> `fetch_web_structure.py`
* "Check if this structure is reasonable." -> `assess_structure.py`
* "Convert this CIF to POSCAR." / "Convert POSCAR to LAMMPS data." -> `convert_format.py`
* "Build from SMILES or prototype." -> Use MCP Structure Generator.

## Tool (via use_skill)

- **run_script** with **script_name**: `fetch_web_structure.py`, `assess_structure.py`, or `convert_format.py`; **script_args**: e.g. `--url "https://.../file.cif"`, `--file structure.cif`, or `--input POSCAR --output data.lmp --output-fmt lammps/lmp --type-map O H`.

## Rules

* **Do not** use Materials Project API or any DB API from this skill; structure search is via MCP.
* Always run `assess_structure.py` after obtaining a new structure (from URL or MCP).
* If `assess_structure` reports "Slab" for a task intended to be "Bulk", warn the user.
* For LAMMPS conversions, **always** provide `--type-map`. If the source .lmp uses a non-atomic atom_style, **always** provide `--atom-style`.
