---
name: structure-manager
description: "Search, download, validate (assess_structure.py), and convert (convert_format.py) crystal structure files. Supports literature-based retrieval, database lookup, and direct URL download. Load this skill for any structure task beyond a simple DB formula query."
skill_type: operator
depends_on: mcp-mat-struct-db, mcp-mat-sg, mcp-mat-doc
---

<!-- multi-server: mat_struct_db, mat_sg, mat_sn, mat_doc -->

# Structure Manager Skill

Handles downloading structure files (from direct URLs or HTML pages), validating atomic structures (CIF, POSCAR, XYZ), and converting between file formats (dpdata).

Requires: pymatgen, numpy (assess_structure.py); dpdata (convert_format.py); requests (fetch_web_structure.py); beautifulsoup4 (optional, --page only: pip install beautifulsoup4).

## Acquisition Capabilities

The following are available for obtaining structures. Choose based on what identifier or context you have; if one path fails or returns no results, try another.

- **Literature-based search**: When the target structure is not in an open database, or the material class is unlikely to be there (molecular crystals, hybrid salts, MOFs, co-crystals, energetic perovskites, etc.):
  1. Search literature with available `mat_sn_*` search tools (e.g. `mat_sn_search-papers-enhanced`, `web-search`) to locate papers reporting the structure. If a tool returns errors or is unavailable, switch to a different available search tool or method — do not retry the same failing tool.
  2. For high-relevance URLs (paper HTML, SI page, open repository), fetch full page content with `extract_info_from_webpage` to extract: space group, lattice constants (a, b, c, α, β, γ), formula, Z, CCDC/ICSD identifiers, DOI. Do **not** rely on search snippets alone — crystal parameters are almost never in abstracts.
  3. If `extract_info_from_webpage` returns 403/paywall on a DOI URL, try alternative open-access URLs for the same paper (SI page, preprint, free full-text mirror) before falling back to snippets.
  4. If a direct CIF/POSCAR download link is found in the full page, use `fetch_web_structure.py --url`. If an HTML page with structure file links is found, use `fetch_web_structure.py --page`.
  5. If the structure is in a gated database (CCDC, ICSD) and no open CIF exists, report identifiers + crystal parameters (see "Structure identification" capability below).
  6. If all full-page fetches fail, what you have from snippets + DOI is still a valid partial result — report it honestly and set `task_completed=partial`.

- **Structure database (MCP)**: `mat_struct_db_*` — search by formula, composition, material ID, prototype.
- **Structure generation (MCP)**: `mat_sg_*` — build from SMILES, Wyckoff positions, prototype templates, surfaces, supercells, defects.
- **Direct file download**: `fetch_web_structure.py --url <link>` — HTTP GET a CIF/POSCAR from a known direct file URL.
- **Web page link extraction**: `fetch_web_structure.py --page <page_url>` — fetch an HTML page, extract all links whose path ends with a structure file extension (.cif/.vasp/.xyz/.res/.pdb/.mol2/.sdf), and download. Auto-downloads if exactly one match is found; returns the candidate list if multiple matches are found so you can pick one and call `--url`.
- **Structure identification (gated databases)**: When the structure resides in a copyrighted or access-gated database (CCDC, ICSD, etc.), extract and report from literature: database identifier (CCDC REFCODE / deposition number, ICSD collection code), space group, lattice constants (a, b, c, α, β, γ), formula, Z, source DOI/URL. Do not attempt to download from these databases or reconstruct the structure with MCP tools — the result would likely be silently wrong. **Delivery**: 1–3 structures → list inline in the finish message; 4+ → save to a JSON file (keys: `identifier`, `database`, `space_group`, `lattice`, `formula`, `Z`, `source_doi`) and reference the file path. Set `task_completed=partial`.

## Validation (Mandatory)

Always run `assess_structure.py` on any new structure regardless of how it was obtained. It returns:
- **Dimensionality**: 0D (Molecule), 1D (Wire), 2D (Slab), 3D (Bulk).
- **Sanity**: checks for overlapping atoms (< 0.5 Å), unreasonable bond lengths.
- **Formula**: chemical composition.

## Scripts

### 0a. γ-Al₂O₃ Builder
* **build_gamma_al2o3.py** — Build a γ-Al₂O₃ defect-spinel structure directly.
    * **Usage**: `python build_gamma_al2o3.py [-o gamma_al2o3.cif]`
    * **When to use**: Any task requesting γ-alumina construction. This script handles the defect-spinel vacancy pattern automatically. **Use this first** — do NOT write custom build scripts from scratch.
    * After building, relax with MLIP (`optimize_structure.py` from mlips skill) — relaxation is typically required to achieve physical force convergence.

### 0b. Molecular Crystal Slab Cutting — **MANDATORY for molecular crystals**
* **build_molecular_crystal_slab.py** — Cut a surface slab from a molecular crystal (organic, MOF, co-crystal, hybrid salt, etc.) with automatic molecule integrity verification.
    * **Usage**: `python build_molecular_crystal_slab.py --file input.cif --miller 1 1 0 --layers 4 [-o output.cif] [--vacuum 20.0] [--bond-tolerance 0.45]`
    * **When to use**: Whenever the input structure is a molecular crystal (contains discrete molecules, not a purely covalent/ionic 3D network). The script: (a) detects molecules via covalent bond graph (PBC-aware), (b) enumerates all terminations from pymatgen SlabGenerator with `in_unit_planes=True`, (c) checks molecule integrity for each termination, (d) selects the best slab preserving intact molecules.
    * **Output JSON**: `{"success": true, "molecules_intact": true, "atom_count_matches_expected": true, "n_atoms": 576, "expected_atoms": 576, "output_file": "output.cif", ...}`
    * **Key checks reported**: molecule integrity (fragmented or not), atom count vs expected (layers × unit-cell atoms), number of terminations evaluated, molecule formula consistency.
    * **⚠ HARD RULE**: For **any** molecular crystal slab task, you **MUST** use this script as your FIRST approach. Do NOT write custom SlabGenerator or manual slab-cutting code — custom code almost always produces fragmented molecules because standard pymatgen `SlabGenerator` does not perform molecule-integrity checks. This script is the **only reliable method** for molecular crystal slabs. If the script fails, debug its parameters (--bond-tolerance, --layers) before resorting to custom code.

### 1. Download / Page Extraction
* **fetch_web_structure.py**
    * `--url <url>` — download a direct structure file link.
        * Usage: `python fetch_web_structure.py --url "https://example.com/file.cif"`
    * `--page <url>` — fetch an HTML page and extract structure file links.
        * Usage: `python fetch_web_structure.py --page "https://www.ccdc.cam.ac.uk/structures/..."`
        * If exactly 1 link found: auto-downloads and returns `{"success": true, "file": "..."}`.
        * If multiple links found: returns `{"success": false, "reason": "multiple_candidates", "candidates": [...]}` — pick one href and call `--url`.
        * If no structure links found: returns `{"success": false, "reason": "no_structure_links", "page_links_sample": [...]}` — inspect the sample to decide next step (sub-page, MCP browser for JS-rendered pages, ask user, etc.).
        * Requires beautifulsoup4. If missing: `{"success": false, "reason": "missing_dependency", "install": "pip install beautifulsoup4"}`.
    * All output is JSON to stdout.

### 2. Validation
* **assess_structure.py** (Sanity Check & Dimensionality)
    * **Usage**: `python assess_structure.py --file "structure.cif"`
    * **Output JSON**: `{"is_valid": true, "dimensionality": "2D-Slab", "formula": "Au4", "warnings": ["Vacuum padding < 10A"]}`
    * **Logic**:
        * **Bulk vs Slab**: Vacuum gap > 15Å in one direction -> Slab; in 3 directions -> Molecule.
        * **Sanity**: Fails if `min_dist < 0.5 Å` (hard overlap cutoff, PBC-aware).

### 3. Surface Passivation
* **passivate_surface.py** — Add H to saturate dangling bonds on slab surfaces (both top and bottom).
    * `python passivate_surface.py slab.cif [-o passivated.cif] [--element Si] [--bond-length 1.48] [--cutoff 2.6] [--target-coordination 4] [--surface-fraction 0.25]`
    * Identifies under-coordinated surface atoms, places H along missing tetrahedral directions, verifies result.
    * Default for Si (Si-H 1.48 A, Si-Si cutoff 2.6 A). Adjust `--element`, `--bond-length`, `--cutoff` for other materials (e.g. Ge-H 1.53 A).
    * **Passivation must produce a structure file**: This script **must output a POSCAR/CIF file** (via `-o`). Do not stop after writing a specification JSON — the task is not complete until the actual passivated structure file exists in the workspace and has been validated with `assess_structure.py`.
    * **Both surfaces**: By default the script passivates both top and bottom surfaces. If the task asks for both-surface passivation, use the default. If only one surface is needed, the script handles this internally — just run it and report which surfaces were passivated.
    * **Typical workflow (execute, don't just plan)**:
      1. Build or obtain the slab (e.g. Si(100) via ASE/pymatgen)
      2. Run `passivate_surface.py` with `-o <output_file>` — **produces the passivated structure**
      3. Run `assess_structure.py` on the output to validate
      4. Report: number of H atoms added, representative Si-H bond length, coordination check results

### 4. Format Conversion
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

## Structure Construction for Electronic Property Calculations

For band structure, DOS, work function, or defect electronic structure, consult **`references/electronic_property_construction.md`** for detailed guidance (primitive vs conventional cell, k-path tables, supercell sizing, heterostructure band alignment).

Key rules:
- **Band structure**: use **primitive cell** + pymatgen `HighSymmKpath(structure)` for k-path.
- **DOS**: dense uniform k-mesh (not line-mode); primitive or supercell both fine.
- **Work function**: slab with vacuum >= 20 A + dipole correction.
- **Defects**: supercell >= 10 A between images; Gamma-only or sparse k-mesh.
- After construction, verify with `assess_structure.py` (dimensionality, atom count, vacuum).

## When to use

* "Get / search / find / retrieve the crystal structure of X" → Try MCP database tools (`mat_struct_db_*`) first for simple inorganic formulas. If not found, or if the material is complex (organic, hybrid, molecular crystal, MOF, co-crystal, energetic salt, etc.), use the literature-based search path: `mat_sn_*` → `extract_info_from_webpage` → `fetch_web_structure.py` / report identifiers.
* "Build from SMILES or prototype" → use MCP structure generator (`mat_sg_*`).
* "Build a crystal structure from space group + lattice + Wyckoff positions" → use `mat_sg_build_bulk_structure_by_wyckoff` (see mcp-mat-sg skill). Fallback: pymatgen `Structure.from_spacegroup()`.
* "Build a heterostructure / interface" → use `mat_sg_build_surface_interface` (see mcp-mat-sg skill). Build each slab separately first, save each file, then stack. Fallback: ASE/pymatgen.
* "Build a structure for band structure / DOS / electronic property calculation" → see "Structure Construction for Electronic Property Calculations" above. Use primitive cell for band structure, dense k-mesh for DOS.
* "I have a direct CIF/POSCAR URL, download it" → `fetch_web_structure.py --url`.
* "Get the structure from a journal SI or open repository page" → `fetch_web_structure.py --page`.
* "Get the crystal structure of X" where X is in CCDC/ICSD → report database identifier (REFCODE / collection code) + crystallographic parameters (space group, lattice constants, formula, Z) from literature; do not attempt to download or reconstruct.
* "Check if this structure is reasonable" → `assess_structure.py`.
* "Convert this CIF to POSCAR" / "Convert POSCAR to LAMMPS data" → `convert_format.py`.
* **"Cut a surface slab from a molecular crystal"** → `build_molecular_crystal_slab.py`. **MANDATORY** for any molecular crystal (organic, MOF, co-crystal, hybrid, energetic) slab task. Do NOT write custom SlabGenerator or inline Python slab-cutting code for molecular crystals — custom code fragments molecules and wastes turns. The script handles PBC-aware molecule detection and multi-termination evaluation automatically.

## Tool (via Skill)

- **run_script** with **script_name**: `fetch_web_structure.py`, `assess_structure.py`, or `convert_format.py`; **script_args**: as in the usage examples above.

## Rules

* If no CIF/POSCAR file is delivered to the user, `task_completed` must be `partial`, never `true` — even if you found crystal parameters from literature.
* **Grounding depth for structure analysis**: When reporting on structural properties (disorder, defects, coordination, composition), always provide **physical/chemical explanations**, not just labels. For disordered structures: explain which specific crystallographic sites are disordered and why (e.g., split positions for metal cations, rotational disorder of organic ligands, partial occupancy of guest molecules), how occupancy patterns relate to symmetry constraints, and what changes structurally when building the ordered replica. A table row saying "contains fractional occupancy" is insufficient — the grounding must trace the structural physics of each specific material.
* Structure identification must include database identifiers (CCDC REFCODE / ICSD collection code) when the structure has been deposited. If the paper you fetched does not contain them, search for the original experimental paper that first reported the structure.
* After obtaining any new structure (any method), run `assess_structure.py`. If it reports "Slab" for a Bulk task, warn the user.
* For LAMMPS conversions, **always** provide `--type-map`. If the source .lmp uses a non-atomic atom_style, **always** provide `--atom-style`.
* On `missing_dependency` from any script, install the package on the remote session before retrying.
* **Deliverable-first execution**: Always prioritize producing actual structure files (CIF, POSCAR, etc.) in the workspace. Do not stop at planning or spec-generation steps — a JSON spec describing what to build is not a deliverable. If a script fails, retry with adjusted parameters or fall back to inline Python, but always aim to write the final structure file before finishing. For multi-component construction tasks (e.g., bulk + slab + adsorbate), build each component as a separate file and verify each exists.
