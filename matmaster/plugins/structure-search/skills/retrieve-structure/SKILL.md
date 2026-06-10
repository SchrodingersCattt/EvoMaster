---
name: retrieve-structure
description: "Obtain correct crystal structures for known materials from databases or generation tools. Use instead of manual construction when the material is known and no explicit Wyckoff/lattice parameters are given."
depends_on: mcp-mat-struct-db, mcp-mat-doc
---

<!-- multi-server: mat_struct_db, mat_sg, mat_sn, mat_doc -->

# Retrieve Structure Skill

Handles downloading, validating, and converting crystal structure files (CIF, POSCAR, XYZ).

## Capability Gate

- **STOP** if the target structure file already exists in the workspace and
  matches the required composition. No retrieval needed.
- **STOP** if the user provides complete crystallographic data (Wyckoff
  positions + lattice parameters + space group) and explicitly asks to build
  from those parameters. The structure can be constructed directly without
  database retrieval.

## Acquisition Paths

- **Database (MCP)**: `mat_struct_db_*` — search by formula, composition, material ID, prototype.
- **Structure generation (MCP)**: `mat_sg_*` — build from SMILES, Wyckoff positions, prototype, surfaces, supercells, defects.
- **Literature search**: `mat_sn_*` / `web-search` → download with `fetch_web_structure.py --url` or `--page`. For 403/paywall, try alternative open-access URLs.
- **Direct download**: `fetch_web_structure.py --url <link>` for known file URLs.
- **Web page extraction**: `fetch_web_structure.py --page <url>` to find and download structure files from HTML pages.
- **Gated databases (CCDC, ICSD)**: Report identifiers + crystal parameters from literature. Do not attempt download or reconstruction.

## Scripts

| Script | Usage |
|--------|-------|
| `assess_structure.py` | `--file structure.cif` → JSON: is_valid, dimensionality, formula, spacegroup, warnings |
| `convert_format.py` | `--input in.cif --output POSCAR --output-fmt vasp/poscar [--type-map O H] [--atom-style full]` |
| `fetch_web_structure.py` | `--url <direct_link>` or `--page <html_url>` → downloads structure files |
| `build_molecular_crystal_slab.py` | `--file input.cif --miller 1 1 0 --layers 4 [-o output.cif]` |
| `passivate_surface.py` | `slab.cif [-o passivated.cif] [--element Si] [--bond-length 1.48]` |

**convert_format.py key flags**: `--type-map` **required** for LAMMPS. `--atom-style` must match source file. `--frame N` for multi-frame.

## Validation (Mandatory)

Always run `assess_structure.py` on any new or transformed structure. Returns
dimensionality, formula, spacegroup, sanity checks. Do not skip this step.

## Molecular Crystal Slabs

For slab tasks where the input CIF contains discrete organic molecules (user
mentions organic crystal, pharmaceutical polymorph, or `assess_structure.py`
shows molecular formula with C/H/N/O and low symmetry), use
`build_molecular_crystal_slab.py` as first approach. Do NOT write custom
SlabGenerator code — it fragments molecules.

## Electronic Property Construction

For band structure, DOS, work function, or defect calculations → `references/electronic_property_construction.md`. Key rules:
- Band structure: **primitive cell** + `HighSymmKpath(structure)`.
- DOS: dense uniform k-mesh. Work function: slab ≥ 20 Å vacuum + dipole correction.
- Defects: supercell ≥ 10 Å between images.

## Hard Guards

- If no CIF/POSCAR delivered, report task as incomplete.
- For LAMMPS conversions, always provide `--type-map` and `--atom-style` when non-atomic.
- **Polymorph disambiguation**: When the database returns multiple candidates
  for the same composition, check `spacegroup` and `spacegroup_number` from
  `assess_structure.py` output to select the correct polymorph. Do not pick by
  atom count alone.
- **Deterministic workflow**: For known materials use database first → generation
  fallback. For novel structures: generation directly.
- **Save early**: Write intermediate structure files to disk before attempting
  further operations. If a downstream step fails, the intermediate is still
  deliverable.
- **Verify after every transformation**: re-run `assess_structure.py` and check
  (1) formula matches expectations, (2) atom count is correct, (3)
  dimensionality is right (3D for bulk, 2D for slab).
