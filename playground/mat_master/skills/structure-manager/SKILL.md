---
name: structure-manager
description: "Skill for DOWNLOADING structures from a URL and VALIDATING atomic structures. Use when: 1) Download a CIF/POSCAR from a direct URL (fetch_web_structure.py). 2) Check validity (assess_structure.py: sanity check, dimensionality, formula). Structure DB search and building (SMILES/prototypes) use MCP tools; no Materials Project API or local DB scripts."
skill_type: operator
---

# Structure Manager Skill

Handles downloading structure files from URLs and validating atomic structures (CIF, POSCAR, XYZ) before use. Database search and structure building are done via MCP tools.

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

## When to use

* "Search for / get me the crystal structure of X." -> Use MCP structure/database tools (not this skill).
* "I have a CIF URL, download it." -> `fetch_web_structure.py`
* "Check if this structure is reasonable." -> `assess_structure.py`
* "Build from SMILES or prototype." -> Use MCP Structure Generator.

## Tool (via use_skill)

- **run_script** with **script_name**: `fetch_web_structure.py` or `assess_structure.py`; **script_args**: e.g. `--url "https://.../file.cif"` or `--file structure.cif`.

## Rules

* **Do not** use Materials Project API or any DB API from this skill; structure search is via MCP.
* Always run `assess_structure.py` after obtaining a new structure (from URL or MCP).
* If `assess_structure` reports "Slab" for a task intended to be "Bulk", warn the user.
