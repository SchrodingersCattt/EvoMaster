---
name: structure-manager
description: "Skill for searching, obtaining, validating, and converting atomic structures. Capabilities: (1) Literature-based structure search — search papers (mat_sn_*), fetch full pages (extract_info_from_webpage), extract crystal data (space group, lattice, CCDC/ICSD ID), attempt CIF download or report identifiers. (2) Download a CIF/POSCAR from a direct file URL (fetch_web_structure.py --url). (3) Scan an HTML page for structure file links and download (fetch_web_structure.py --page). (4) Validate: dimensionality, sanity, formula (assess_structure.py). (5) Convert between formats: CIF/POSCAR/LAMMPS/XYZ/etc. (convert_format.py). For any structure retrieval beyond a trivial open-DB formula lookup, call use_skill get_info first."
skill_type: operator
---

# Structure Manager Skill

Handles downloading structure files (from direct URLs or HTML pages), validating atomic structures (CIF, POSCAR, XYZ), and converting between file formats (dpdata).

Requires: pymatgen, numpy (assess_structure.py); dpdata (convert_format.py); requests (fetch_web_structure.py); beautifulsoup4 (optional, --page only: pip install beautifulsoup4).

## Acquisition Capabilities

The following are available for obtaining structures. Choose based on what identifier or context you have; if one path fails or returns no results, try another.

- **Literature-based search**: When the target structure is not in an open database, or the material class is unlikely to be there (molecular crystals, hybrid salts, MOFs, co-crystals, energetic perovskites, etc.):
  1. Search literature with `mat_sn_search-papers-enhanced` / `mat_sn_web-search` to locate papers reporting the structure.
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

* "Get / search / find / retrieve the crystal structure of X" → Try MCP database tools (`mat_struct_db_*`) first for simple inorganic formulas. If not found, or if the material is complex (organic, hybrid, molecular crystal, MOF, co-crystal, energetic salt, etc.), use the literature-based search path: `mat_sn_*` → `extract_info_from_webpage` → `fetch_web_structure.py` / report identifiers.
* "Build from SMILES or prototype" → use MCP structure generator (`mat_sg_*`).
* "I have a direct CIF/POSCAR URL, download it" → `fetch_web_structure.py --url`.
* "Get the structure from a journal SI or open repository page" → `fetch_web_structure.py --page`.
* "Get the crystal structure of X" where X is in CCDC/ICSD → report database identifier (REFCODE / collection code) + crystallographic parameters (space group, lattice constants, formula, Z) from literature; do not attempt to download or reconstruct.
* "Check if this structure is reasonable" → `assess_structure.py`.
* "Convert this CIF to POSCAR" / "Convert POSCAR to LAMMPS data" → `convert_format.py`.

## Tool (via use_skill)

- **run_script** with **script_name**: `fetch_web_structure.py`, `assess_structure.py`, or `convert_format.py`; **script_args**: as in the usage examples above.

## Rules

* If no CIF/POSCAR file is delivered to the user, `task_completed` must be `partial`, never `true` — even if you found crystal parameters from literature.
* After obtaining any new structure (any method), run `assess_structure.py`. If it reports "Slab" for a Bulk task, warn the user.
* For LAMMPS conversions, **always** provide `--type-map`. If the source .lmp uses a non-atomic atom_style, **always** provide `--atom-style`.
* On `missing_dependency` from any script, install the package on the remote session before retrying.
