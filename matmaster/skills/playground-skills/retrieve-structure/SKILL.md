---
name: retrieve-structure
description: "Trigger to acquire crystal structure files: retrieve from databases, literature, direct URL, or supplementary HTML; also format conversion and post-download validation."
skill_type: operator
depends_on: mcp-mat-struct-db, build-atomic-structure, transform-atomic-structure, assemble-atomic-structure, operate-molecular-crystal, mcp-mat-doc
---

<!-- multi-server: mat_struct_db, mat_sg, mat_sn, mat_doc -->

# Retrieve Structure Skill

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
| `build_molecular_crystal_slab.py` | `--file input.cif --miller 1 1 0 --layers 4 [-o output.cif]` |
| `passivate_surface.py` | `slab.cif [-o passivated.cif] [--element Si] [--bond-length 1.48]` |
| ~~`generate_kpoints.py`~~ | Moved to `matmaster/skills/vasp/scripts/generate_kpoints.py` |

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
* **Deterministic workflow**: When multiple equivalent approaches exist (e.g. database search vs generation), pick the most reliable one consistently. For known materials: database first → generation fallback. For novel structures: generation directly.
* **Save early, save often**: Write intermediate structure files to disk before attempting further operations. If a downstream step fails, the intermediate is still deliverable.
* **Verify after every transformation**: After any operation that changes a structure (supercell, slab, defect, conversion), re-run `assess_structure.py` and verify: (1) formula matches expectations, (2) atom count is correct, (3) dimensionality is right (3D for bulk, 2D for slab).
