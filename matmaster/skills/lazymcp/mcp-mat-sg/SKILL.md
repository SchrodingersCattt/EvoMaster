---
name: mcp-mat-sg
description: 当需要构建、生成或修改原子结构时调用本 skill。支持从 SMILES 构建分子、Wyckoff/模板构建晶体、切表面 slab、超胞、缺陷、掺杂、交联聚合物等。
skill_type: mcp-loader
mcp_server: mat_sg
---

## build_surface_slab

- **Binary compounds** (ZnS, ZnO, GaAs, …): the `layers` parameter counts **cation-anion bilayers** (each bilayer = 2 atomic planes). A task requesting "N-layer slab" means pass `layers=N`. After building, verify: distinct z-planes = 2 × N, atom count = layers × atoms_per_bilayer × supercell multiplier.
- Always set `vacuum ≥ 15` Å. After building, verify vacuum = c − slab_thickness ≥ 15 Å.
- Polar surfaces (ZnS(001), ZnO(001)): note the polarity problem in the final answer and mention a mitigation strategy (symmetric termination, dipole correction, pseudo-hydrogen passivation, etc.). For polar Type 3 surfaces (zinc blende (001), wurtzite (0001)), prefer even layers and symmetric termination; try different `termination` values (0, 1, 2) until both surfaces have the same composition. See **tasker-polar-surface** skill for detailed Tasker classification and the `build_slab_tasker_fix.py` script.
- **When the slab build fails or produces wrong composition**: try `repair=true` (or false if already true), and try different `termination` values. If MCP tool fails after 1 attempt, fall back to `build_slab_tasker_fix.py` from tasker-polar-surface skill or direct ASE/pymatgen code.
- **Layer count compliance**: when a reference document, paper, or task specification states an exact number of layers, you MUST use that exact number. Do NOT substitute a different layer count because it seems "more reasonable." After building, verify the actual number of distinct z-planes matches the specification (for binary compounds: distinct z-planes = 2 × N for N bilayers). If the built slab has the wrong layer count, rebuild with corrected parameters before proceeding.
- **Molecular crystal slabs** (organic, MOF, co-crystal, etc.):
  **Preferred workflow** — use `build_molecular_crystal_slab.py` from `structure-manager` skill:
  ```
  python build_molecular_crystal_slab.py --file input.cif --miller H K L --layers N [--vacuum 20.0]
  ```
  This script automatically: (a) detects molecules via PBC-aware bond graph, (b) enumerates all slab terminations, (c) verifies molecule integrity for each, (d) selects the best termination. **Do NOT write custom slab-cutting Python scripts from scratch** — this wastes many turns.
  If the script fails or no termination preserves molecules, then fall back to manual pymatgen `SlabGenerator` with `in_unit_planes=True`, systematically trying different `shift` values. Key verification: (1) no covalent bond is broken across the slab boundary, (2) atom count = layers × unit-cell atoms, (3) no isolated molecular fragments exist.
- **Post-build verification**: after ANY structure build, verify ONCE with `get_structure_info`: composition, atom count, cell angles (cubic=90°, hex=90/90/120°), vacuum ≥ 15 Å for slabs. One check per structure — do not add separate verification passes.

## build_surface_adsorbate

Place adsorbate molecules on slab surfaces. Essential for catalysis workflows (CO2RR, HER, OER, etc.).

- **Simple placement**: provide `surface_path`, `adsorbate_path`, `height` (2.0 Å default), `shift` ([0.5, 0.5] for cell center).
- **Smart placement**: use `site_selector` with `site_type` ("ontop"/"bridge"/"hollow") and optional `target_element` (e.g. "Zn" to place above Zn atoms).
- **Enumerate all sites**: set `enumerate_all=true` to generate all symmetry-distinct configurations.
- **Adsorbate prep**: for simple molecules (CO, CO2, H2O, OH, OOH, H), build with `build_molecule_structures_from_smiles` first, or write XYZ manually via Bash.

## get_structure_info

Inspect a structure file for lattice parameters, composition, atom count, volume, etc. Use `level="basic"` for quick scan or `level="detailed"` for full analysis. **Always inspect structures after building to verify correctness.**

## generate_ordered_replicas

- Report **both** the original disordered formula **and** the ordered replica expanded formula (integer stoichiometry). Write formulas as concatenated strings without spaces: `H144C48N24Cl24O96` (NOT `H144 C48 N24 Cl24 O96`). If CIF returns space-separated elements, concatenate them.
- For each replica, explain: (a) which sites are disordered and how, (b) how the ordered config was chosen (valence/charge balance/connectivity), (c) what changed. A bare filename list without chemical reasoning = fail.
- **Chemical/physical grounding is MANDATORY**: for every disordered site, explain the **bonding environment**, **valence state**, **coordination geometry**, or **crystallographic reason** that makes that disorder physically meaningful. Your explanation MUST go beyond listing site labels and occupancy numbers — you must discuss at least ONE of: (a) why the disordered species can substitute for each other (ionic radius, charge, electronegativity), (b) what bonds/coordination surround the site (e.g., "tetrahedral coordination by 4 O²⁻ at 1.95 Å"), (c) how the ordered replica preserves charge neutrality and valence. **Bad** example (will fail): "Site 8f has 0.6 Mn and 0.4 Fe occupancy, so we chose Mn." **Good** example: "Site 8f is octahedrally coordinated by 6 O²⁻ (M–O 2.05–2.12 Å). Mn²⁺ (0.83 Å) and Fe²⁺ (0.78 Å) have similar ionic radii and both prefer octahedral coordination, enabling random substitution. The ordered replica selects Mn on all 8f sites, preserving the +2 charge balance with the surrounding oxide framework." Always tie back to bonding, coordination, or charge balance.
- On timeout → fall back to pymatgen `OrderDisorderedStructureTransformation`.
- **Batch processing (multiple disordered structures)**: when the task requires ordering N ≥ 3 structures, process ALL in ONE consolidated script or MCP call loop. Do NOT spend more than ~3 minutes per structure. Strategy:
  1. Call `generate_ordered_replicas` for each structure sequentially.
  2. If the MCP tool is slow on one structure: set a mental budget of 2 MCP attempts per structure, then fall back to local pymatgen `OrderDisorderedStructureTransformation`.
  3. Write chemical/physical grounding compactly — 2-3 sentences per disordered site, not paragraphs. Focus on: coordination geometry, ionic radius comparison, and charge balance. Skip lengthy derivations.
  4. Save each ordered CIF immediately after generation (breadth-first: get ALL outputs saved before polishing any single one).
  5. Assemble the final summary table at the end, not after each structure.
  6. **If approaching timeout**: call finish immediately with saved CIF files + whatever grounding exists. Delivered files with brief grounding > perfect grounding with no deliverable.

## add_hydrogens / passivation

**MANDATORY**: use `passivate_surface.py` from **structure-manager** skill — it handles detection, placement, and verification in one call. Pass `-o <output>` to produce the file directly. Do NOT write custom passivation scripts from scratch.
- **⚠ BOTH surfaces mandatory**: always passivate top AND bottom. Si-H ≈ 1.48 Å, Ge-H ≈ 1.53 Å.
- After passivation, verify with `assess_structure.py` and report: H count per surface, mean bond length, all surface atoms coordination = 4.

## Complex / defective bulk structures

- **γ-Al₂O₃**: run `build_gamma_al2o3.py` from **structure-manager** skill (`python build_gamma_al2o3.py [-o gamma_al2o3.cif]`). Save CIF first, then optionally relax with MLIP.
- **Other complex oxides** (spinel, perovskite, garnet): use `build_bulk_structure_by_wyckoff` or fetch from `mat_struct_db`.
