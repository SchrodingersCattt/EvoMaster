---
name: structure-manager
description: "Search, download, validate (assess_structure.py), and convert (convert_format.py) crystal structure files. Supports literature-based retrieval, database lookup, and direct URL download. Load this skill for any structure task beyond a simple DB formula query."
skill_type: operator
depends_on: mcp-mat-struct-db, mcp-mat-sg, mcp-mat-doc
---

<!-- multi-server: mat_struct_db, mat_sg, mat_sn, mat_doc -->

# Structure Manager Skill

Handles downloading, validating, and converting crystal structure files (CIF, POSCAR, XYZ).

## Acquisition Paths

- **Database (MCP)**: `mat_struct_db_*` — search by formula, composition, material ID, prototype.
- **Structure generation (MCP)**: `mat_sg_*` — build from SMILES, Wyckoff positions, prototype, surfaces, supercells, defects.
- **Literature search**: `mat_sn_*` / `web-search` → `extract_info_from_webpage` → `fetch_web_structure.py --url` or `--page`. For 403/paywall, try alternative open-access URLs.
- **Direct download**: `fetch_web_structure.py --url <link>` for known file URLs.
- **Web page extraction**: `fetch_web_structure.py --page <url>` to find and download structure files from HTML pages.
- **Gated databases (CCDC, ICSD)**: Report identifiers + crystal parameters from literature. Do not attempt download or reconstruction.

## Scripts

| Script | Usage |
|--------|-------|
| `assess_structure.py` | `--file structure.cif` → JSON: is_valid, dimensionality, formula, warnings |
| `convert_format.py` | `--input in.cif --output POSCAR --output-fmt vasp/poscar [--type-map O H] [--atom-style full]` |
| `fetch_web_structure.py` | `--url <direct_link>` or `--page <html_url>` → downloads structure files |
| `build_gamma_al2o3.py` | `[-o gamma_al2o3.cif]` → γ-Al₂O₃ defect-spinel structure |
| `build_molecular_crystal_slab.py` | `--file input.cif --miller 1 1 0 --layers 4 [-o output.cif]` |
| `passivate_surface.py` | `slab.cif [-o passivated.cif] [--element Si] [--bond-length 1.48]` |

**convert_format.py key flags**: `--type-map` **required** for LAMMPS. `--atom-style` must match source file. `--frame N` for multi-frame.

## Validation (Mandatory)

Always run `assess_structure.py` on any new structure. Returns dimensionality, sanity checks, and formula.

## Molecular Crystal Slabs — HARD RULE

For **any** molecular crystal slab task, **MUST** use `build_molecular_crystal_slab.py` as first approach. Do NOT write custom SlabGenerator code — it fragments molecules. The script handles PBC-aware molecule detection and multi-termination evaluation.

## Electronic Property Construction

For band structure, DOS, work function, or defect calculations, see `references/electronic_property_construction.md`. Key rules:
- Band structure: **primitive cell** + `HighSymmKpath(structure)`.
- DOS: dense uniform k-mesh. Work function: slab ≥ 20 Å vacuum + dipole correction.
- Defects: supercell ≥ 10 Å between images.

## Rules

* If no CIF/POSCAR delivered, `task_completed=partial`.
* For LAMMPS conversions, always provide `--type-map` and `--atom-style` when non-atomic.
* **Deliverable-first**: Prioritize producing actual structure files. Do not stop at planning/spec-generation.
* Structure identification must include database identifiers (CCDC REFCODE / ICSD code) when deposited.
* After obtaining any new structure, run `assess_structure.py`. **This is mandatory, not optional.**
* **Grounding depth**: For structural analysis, provide physical/chemical explanations, not just labels. Trace structural physics of each specific material.
* **Save early, save often**: Write intermediate structure files to disk before attempting further operations. If a downstream step fails, the intermediate is still deliverable.
* **Verify after every transformation**: After any operation that changes a structure (supercell, slab, defect, conversion), re-run `assess_structure.py` and verify: (1) formula matches expectations, (2) atom count is correct, (3) dimensionality is right (3D for bulk, 2D for slab).

## Deterministic Workflow Selection

When multiple equivalent approaches exist, **always** follow this fixed priority to ensure consistent results across runs:

### Bulk structures (known materials)
1. **MCP database lookup** (`mat_struct_db_search_by_formula` or `_by_composition`) → pick the first hit with matching space group.
2. **MCP structure generation** (`mat_sg_create_structure_from_wyckoff`) → if database has no match.
3. **pymatgen inline** (`Structure.from_spacegroup(...)`) → last resort.

### Surface slabs
1. **MCP `mat_sg_build_surface_slab`** — always try first with explicit `miller_index`, `slab_thickness`, `vacuum_thickness`.
2. **`build_molecular_crystal_slab.py`** — ONLY for molecular crystals (use when MCP slab builder fragments molecules).
3. **pymatgen SlabGenerator** — fallback if MCP fails for non-molecular systems.
4. **After slab creation**: always verify with `assess_structure.py`, check vacuum ≥ 15 Å, check atom count.

### Supercells
1. **MCP `mat_sg_make_supercell_structure`** — always try first.
2. **pymatgen `Structure.make_supercell()`** — fallback.

### Defects (vacancy, substitution, interstitial)
1. **MCP `mat_sg_create_point_defect`** — always try first.
2. **pymatgen manual** (remove/replace atom by index) → fallback.
3. **After defect creation**: verify formula (one atom fewer for vacancy), verify supercell dimensions.

### Format conversions
- **Always use `convert_format.py`** — do NOT write conversion code manually.
- For CIF → ABACUS STRU: `convert_format.py --input in.cif --output STRU --output-fmt abacus/stru`
- For CIF → POSCAR: `convert_format.py --input in.cif --output POSCAR --output-fmt vasp/poscar`

### Key anti-patterns (avoid)
- ❌ Writing custom `SlabGenerator` code when MCP tool exists and works.
- ❌ Trying multiple approaches in sequence without committing to one.
- ❌ Building a structure inline with pymatgen when `mat_sg_create_structure_from_wyckoff` could be used.
- ❌ Not saving the intermediate CIF before attempting further transformations.
