---
name: mcp-mat-sg
description: 当需要构建、生成或修改原子结构时调用本 skill。支持从 SMILES 构建分子、Wyckoff/模板构建晶体、切表面 slab、超胞、缺陷、掺杂、交联聚合物等。
skill_type: mcp-loader
mcp_server: mat_sg
---

## build_surface_slab

- **Binary compounds** (ZnS, ZnO, GaAs, …): the `layers` parameter counts **cation-anion bilayers** (each bilayer = 2 atomic planes). A task requesting "N-layer slab" means pass `layers=N`. After building, verify: distinct z-planes = 2 × N, atom count = layers × atoms_per_bilayer × supercell multiplier.
- Always set `vacuum ≥ 15` Å. After building, verify vacuum = c − slab_thickness ≥ 15 Å.
- **Polar surfaces** (ZnS(001), ZnO(001)): note the polarity problem in the final answer and mention a mitigation strategy (symmetric termination, dipole correction, pseudo-hydrogen passivation, etc.). For polar Type 3 surfaces (zinc blende (001), wurtzite (0001)), prefer even layers and symmetric termination; try different `termination` values (0, 1, 2) until both surfaces have the same composition.
- **When the slab build fails or produces wrong composition**: try `repair=true` (or false if already true), and try different `termination` values. If MCP tool still fails after 2 attempts, for **Type 3 polar surfaces** (zinc blende (001), wurtzite (0001)) use `build_slab_tasker_fix.py` from tasker-polar-surface skill: `python build_slab_tasker_fix.py -i <bulk> -m <h> <k> <l> -L <2×N_bilayers> -v 15 -o <output>`, then validate with `check_slab_tasker.py --file <output> --tasker_type 3`. For other surfaces, fall back to direct ASE/pymatgen inline code.
- **Layer count compliance**: when a reference document, paper, or task specification states an exact number of layers, you MUST use that exact number. Do NOT substitute a different layer count because it seems "more reasonable." After building, verify the actual number of distinct z-planes matches the specification (for binary compounds: distinct z-planes = 2 × N for N bilayers). If the built slab has the wrong layer count, rebuild with corrected parameters before proceeding.
- **Molecular crystal slabs** (organic, MOF, co-crystal, etc.): use `build_molecular_crystal_slab.py` from **structure-manager** skill (`python build_molecular_crystal_slab.py --file input.cif --miller H K L --layers N`). Do NOT write custom slab-cutting scripts from scratch.
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
- **Chemical/physical grounding is MANDATORY**: for every disordered site, explain the bonding environment, valence state, or coordination geometry — not just occupancy numbers. Cover: why the species can substitute (ionic radius, charge similarity), the coordination environment (e.g., octahedral/tetrahedral, bond lengths), and how the ordered replica preserves charge balance. Write 2-3 focused sentences per disordered site.
- On timeout → fall back to pymatgen `OrderDisorderedStructureTransformation`.
- **Batch processing (N ≥ 3 structures)**: process breadth-first — save each ordered CIF immediately before moving to the next. Budget ~2 MCP attempts per structure; if slow, fall back to pymatgen `OrderDisorderedStructureTransformation`. If approaching timeout, finish immediately with whatever files are saved. A summary table of filenames with zero chemistry explanation = fail even if all files delivered.

## Surface passivation (semiconductor slabs)

> **Scope**: this section covers **adding H to under-coordinated surface atoms on existing slabs** or completing valence on organic molecular crystals. It does NOT apply to building **inorganic hydride bulk crystals** (SiH₄, Si₂H₆, GeH₄, B₂H₆, etc.) from scratch — for those, use standard MCP crystal building tools (`build_bulk_structure_by_wyckoff`, `mat_struct_db_*`) or direct ASE/pymatgen construction.

Use `passivate_surface.py` from **structure-manager** skill for semiconductor slab passivation (Si, Ge, etc.). Pass `-o <output>`. Passivate BOTH top and bottom surfaces. Si-H ≈ 1.48 Å, Ge-H ≈ 1.53 Å. After passivation, verify with `assess_structure.py`.

> **Organic / molecular crystal hydrogenation** (adding H to complete valence on C, N, etc. in molecular crystals): do NOT use `passivate_surface.py` — it is designed for semiconductor surfaces only. Instead, use OpenBabel (`obabel input.cif -O output.cif -h`) or write inline pymatgen/ASE code to place H atoms at standard bond lengths (C-H ≈ 1.09 Å, N-H ≈ 1.01 Å) with tetrahedral (sp3, 109.5°) or trigonal planar (sp2, 120°) geometry. Verify the output formula matches the expected hydrogenated composition. Do not hydrogenate carbonyl/ester O atoms — only C and N with incomplete valence.

## Complex / defective bulk structures

- **γ-Al₂O₃**: run `build_gamma_al2o3.py` from **structure-manager** skill as the FIRST step (`python build_gamma_al2o3.py -o gamma_al2o3.cif`) — do NOT write custom build scripts. Save the CIF immediately, then relax with MLIP (`optimize_structure.py` from **mlips** skill). Relaxation is typically required for γ-Al₂O₃ tasks; target max force < 0.1 eV/Å.
- **Other complex oxides** (spinel, perovskite, garnet): use `build_bulk_structure_by_wyckoff` or fetch from `mat_struct_db`.
