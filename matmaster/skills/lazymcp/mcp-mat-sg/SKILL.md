---
name: mcp-mat-sg
description: 当需要构建、生成或修改原子结构时调用本 skill。支持从 SMILES 构建分子、Wyckoff/模板构建晶体、切表面 slab、超胞、缺陷、掺杂、交联聚合物等。
skill_type: mcp-loader
mcp_server: mat_sg
---

## build_surface_slab

- **Binary compounds** (ZnS, ZnO, GaAs, …): the `layers` parameter counts **cation-anion bilayers** (each bilayer = 2 atomic planes). A task requesting "N-layer slab" means pass `layers=N`. After building, verify: distinct z-planes = 2 × N, atom count = layers × atoms_per_bilayer × supercell multiplier.
- Always set `vacuum ≥ 15` Å. After building, verify vacuum = c − slab_thickness ≥ 15 Å.
- Polar surfaces (ZnS(001), ZnO(001)): note the polarity problem in the final answer and mention a mitigation strategy (symmetric termination, dipole correction, pseudo-hydrogen passivation, etc.).
- **Zinc blende (001)** (ZnS, GaAs, CdTe, InP, …): alternating cation-only / anion-only layers → **Tasker Type 3 (polar)**. Use `slab_size_mode="fractional_thickness"` with an even number of layers for symmetric termination (e.g. 8 or 10). After building, verify top and bottom layers have the same element composition. If asymmetric, try different `termination` parameter values (0, 1, or 2) until symmetric. Report polarity + mitigation in final answer.
- **Intermetallic surfaces** (ZnPd, CuAu, NiAl, …): first retrieve or build the bulk structure (e.g. from `mat_struct_db` or `build_bulk_structure_by_wyckoff`), then cut surface with `build_surface_slab`. Common structures: ZnPd = CsCl-type (Pm-3m, a≈3.15 Å). For (011) surface of cubic intermetallics, use `miller_index=[0,1,1]`, `slab_size_mode="thickness"`, `slab_size_value=12` (≈12 Å thick), `vacuum=15`.
- **Wurtzite (0001)** (ZnO, GaN, InN, …): hexagonal → 3-index Miller `[0,0,1]`. Polar Type 3. Use even layers for symmetric slab.
- **When the slab build fails or produces wrong composition**: try `repair=true` (or false if already true), and try different `termination` values. If MCP tool fails after 1 attempt, fall back to Python script (`build_slab_tasker_fix.py` from tasker-polar-surface skill) or direct ASE/pymatgen code.
- **Layer count compliance**: when a reference document, paper, or task specification states an exact number of layers, you MUST use that exact number. Do NOT substitute a different layer count because it seems "more reasonable." After building, verify the actual number of distinct z-planes matches the specification (for binary compounds: distinct z-planes = 2 × N for N bilayers). If the built slab has the wrong layer count, rebuild with corrected parameters before proceeding.
- **Molecular crystal slabs** (organic, MOF, co-crystal, etc.):
  **Preferred workflow** — use `build_molecular_crystal_slab.py` from `structure-manager` skill:
  ```
  python build_molecular_crystal_slab.py --file input.cif --miller H K L --layers N [--vacuum 20.0]
  ```
  This script automatically: (a) detects molecules via PBC-aware bond graph, (b) enumerates all slab terminations, (c) verifies molecule integrity for each, (d) selects the best termination. **Do NOT write custom slab-cutting Python scripts from scratch** — this wastes many turns.
  If the script fails or no termination preserves molecules, then fall back to manual pymatgen `SlabGenerator` with `in_unit_planes=True`, systematically trying different `shift` values. Key verification: (1) no covalent bond is broken across the slab boundary, (2) atom count = layers × unit-cell atoms, (3) no isolated molecular fragments exist.
- **Post-build structure verification**: after slab builds, verify with `get_structure_info`: (a) composition matches formula, (b) atom count is correct, (c) vacuum ≥ 15 Å. If any check fails, fix and re-verify before delivering.

## build_surface_interface (heterojunction / interface construction)

Build a heterostructure interface by stacking two slab structures.

### Workflow for heterojunction tasks
1. **Build both slabs separately**: use `build_surface_slab` (or `build_slab_tasker_fix.py`) to create each component slab with appropriate Miller index, thickness, and vacuum. Save them as separate files.
2. **Ensure lattice compatibility**: the two slabs must have compatible in-plane lattice parameters. If mismatch > 5%, use `make_supercell_structure` on one or both slabs to create commensurate supercells. Common strategy: find the smallest supercell ratio that brings mismatch below 5% (e.g. 2×2 of material A stacked on 3×3 of material B).
3. **Stack with `build_surface_interface`**: provide both slab files, set `interface_distance` (typically 2.0–3.0 Å for van der Waals, 1.5–2.5 Å for covalent), `max_strain` (default 0.2 = 20%, use 0.05 for stricter matching).
4. **Verify**: check the output structure has correct atom count (sum of both slabs), reasonable cell dimensions, and no overlapping atoms.

### Common heterojunction examples
- **Type-II band alignment** (e.g. MoS2/WS2, GaAs/AlAs): both materials have same crystal structure → small mismatch → direct stacking.
- **Oxide/metal** (e.g. TiO2/Au, ZnO/Pt): larger mismatch typical → may need supercell matching.
- **Semiconductor junctions** (e.g. Si/Ge, GaAs/InP): similar structures → moderate mismatch.

### Parameters
- `material1_path`: path to first slab CIF/POSCAR
- `material2_path`: path to second slab CIF/POSCAR
- `stack_axis`: 2 (z-axis, default — standard for slab stacking)
- `interface_distance`: gap between slabs in Å (2.0–3.0 typical)
- `max_strain`: maximum allowed in-plane strain (0.05–0.2)
- `output_file`: output filename

### Fallback (if MCP tool fails)
Use Python with ASE: read both slabs, match in-plane cells with `np.linalg.solve`, stack by combining atoms and extending the c-axis. Or use pymatgen `CoherentInterfaceBuilder` for automated lattice matching.

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

## add_hydrogens / passivation

- Si/Ge surface passivation: target Si-H ≈ 1.48 Å along tetrahedral direction (~109.47°).
- **⚠ BOTH SURFACES MANDATORY**: Passivate **all** dangling bonds on **both** top and bottom surfaces of the slab. A slab has TWO exposed surfaces — failing to passivate either one is a critical error. Identify surface atoms on BOTH sides by checking z-coordinates: atoms near z_max (top) AND atoms near z_min (bottom) are surface atoms. Both sets must be fully passivated.
- **Key principle**: a surface Si atom with coordination N needs (4 − N) hydrogen atoms. For reconstructed surfaces like Si(100)-2×1, surface dimers have coordination 2 → each dimer atom needs 2 H atoms, not 1.
- **Passivation algorithm**:
  1. Compute coordination of every Si atom (Si-Si cutoff 2.6 Å, Si-H cutoff 1.8 Å).
  2. Identify ALL under-coordinated Si (coordination < 4) on BOTH top and bottom surfaces.
  3. For each, compute missing bond directions (tetrahedral angles relative to existing bonds) and add H atoms at 1.48 Å along those directions.
  4. Re-check coordination. Iterate until ALL surface Si reach coordination = 4.
- **Verification script** (mandatory): after saving the passivated structure, re-read it and print per-atom coordination for every Si atom. Flag any Si with coordination ≠ 4. **Separately verify top-surface and bottom-surface atoms.** The final answer MUST report mean Si coordination and confirm it equals 4.0.
- Save the passivated structure, then verify by re-reading and checking. Report: total H added, mean Si-H bond length, per-atom coordination check, top/bottom surface H counts, and confirmation that all surface Si atoms have coordination = 4.

## Complex / defective bulk structures

### γ-Al2O3 (defective spinel)
γ-Al2O3 has a **defective spinel structure** (based on MgAl2O4 Fd-3m framework, with Al on both tetrahedral and octahedral sites and ordered vacancies). Multiple structural models exist:

**Recommended approach** (fastest, most reliable):
1. **Try Materials Project / structure database first**: search `mat_struct_db` for `Al2O3` and filter for spinel-like structures (Fd-3m or related). If γ-Al2O3 is available (e.g. mp-3163 or similar), download and verify.
2. **If not in DB, build via Wyckoff positions** using the **Pinto/Digne model**:
   - Space group: Fd-3m (No. 227) or I4₁/amd (tetragonal distorted variant)
   - For Fd-3m cubic model (a ≈ 7.91 Å): Al on 8a (tetrahedral, occupancy ~1.0), Al on 16d (octahedral, occupancy ~0.833 — 5/6 occupied to maintain Al₂O₃ stoichiometry), O on 32e (u ≈ 0.26)
   - After Wyckoff expansion, remove excess Al from 16d sites to achieve Al:O ≈ 2:3 (±5%)
   - Use `build_bulk_structure_by_wyckoff` → then manually remove selected Al atoms from octahedral sites
3. **Verify**: (a) Al:O ratio ≈ 2:3, (b) cell angles ≈ 90°, (c) BOTH tetrahedral and octahedral Al environments exist, (d) min interatomic distance > 1.0 Å

**For relaxation** (if required): use MLIP — see "MLIP-based structure relaxation" section below. Save the **unrelaxed** structure with the task-required filename FIRST, then overwrite only if relaxation succeeds. This prevents losing the deliverable on timeout.

### Other complex oxide structures
- **Spinel** (MgAl2O4, Fe3O4, etc.): Fd-3m, cations on 8a (tetrahedral) + 16d (octahedral), anions on 32e.
- **Perovskite** (SrTiO3, BaTiO3, etc.): Pm-3m ideal, Pnma/P4mm distorted. Use `build_bulk_structure_by_wyckoff` or fetch from DB.
- **Garnet** (Y3Al5O12, etc.): Ia-3d. Large unit cell (~160 atoms). Prefer DB fetch.

## MLIP-based structure relaxation

When a task requires relaxing a structure with MLIP/DPA models:
- Copy `_calculator.py` from `matmaster/skills/mlips/scripts/` into the working directory.
- Write a self-contained Python script using:
  ```python
  from _calculator import build_calculator
  calc = build_calculator("DPA3.1-3M")  # or with head="OC22" for catalysis
  ```
- Use ASE's `LBFGS` optimizer with `ExpCellFilter` for cell+position relaxation (or `BFGS` for positions-only).
- Submit to Bohrium with **image `registry.dp.tech/dptech/dpa-calculator:e13a296f`** and **machine `c16_m64_1 * NVIDIA 4090`**.
- **Do NOT waste turns searching for calculator backends** (no `glob.glob("/opt/**/*.pb")`, no trying multiple import paths). Use `build_calculator` from `_calculator.py` directly — it handles model discovery.
- **Save-early pattern**: save the unrelaxed structure under the task-required filename BEFORE submitting relaxation. Overwrite only if relaxation succeeds and produces physically reasonable results (fmax < threshold, no broken bonds). This ensures a deliverable exists even if relaxation times out.

## Geometry verification (after any structure build)

After building or modifying any structure, verify key geometric properties before delivering:
- **Bond angles**: for tetrahedral sp3 centers (C, Si, Ge), angles should be near 109.47°. For planar sp2 centers, near 120°. If angles deviate by more than 5°, diagnose and fix.
- **Cell angles**: for cubic systems all angles must be 90°; for hexagonal α=β=90°, γ=120°. If the built structure has wrong cell angles, the construction method or parameters are incorrect — rebuild.
- **Forces/energy**: if a relaxation step is included and max force exceeds the threshold (typically 0.05 eV/Å for production), the relaxation did not converge. Report this and either continue relaxation or note the limitation.
- **Atom count**: verify total atoms match expectations from composition × supercell size.

## CO2RR / catalysis surface+adsorbate workflow

For CO2RR, HER, OER, and similar catalysis structure-preparation tasks:
1. **Build or fetch bulk** → verify composition with `get_structure_info`.
2. **Cut slab** → use exact layer count from the reference/task specification. Verify with `get_structure_info`.
3. **Add adsorbate** → use `build_surface_adsorbate` with appropriate site and height. For CO2RR common adsorbates: HCOO (formate, bidentate), CO (atop), COOH (carboxyl), H, OH, H2O.
4. **Save each intermediate** (bulk, slab, slab+adsorbate) as separate files — the task often requires multiple deliverables.
5. **Token economy**: do NOT search the web for standard adsorbate molecules (CO, OH, H2O, HCOO). Build them directly with `build_molecule_structures_from_smiles` or write XYZ coordinates in one Bash call.
6. **Adsorbate geometry**: verify the adsorbate-surface distance is physically reasonable (typically 1.5–2.5 Å for chemisorption, 2.5–3.5 Å for physisorption). Report key distances in the final answer.

### MLIP-based adsorption energy calculations
When the task requires computing adsorption energies (E_ads) with MLIP/DPA models:
- Write ONE consolidated Python script that loops over all surfaces × adsorbates, computes E_ads = E(slab+ads) − E(slab) − E(gas), and prints a complete results table.
- The script MUST use `from _calculator import build_calculator` (copy `_calculator.py` from `matmaster/skills/mlips/scripts/` into the input directory).
- For catalysis surfaces use head `OC22`: `build_calculator("DPA3.1-3M", head="OC22")`.
- Submit to Bohrium with **image `registry.dp.tech/dptech/dpa-calculator:e13a296f`** and **machine `c16_m64_1 * NVIDIA 4090`**. Do NOT use ABACUS/CP2K/other images — they lack ASE and deepmd-kit.
- If the job fails (e.g. missing module), check the image first. ASE-dependent scripts require the DPA image.
- **Time budget**: these tasks involve structure building + Bohrium submission + result parsing. Submit the computation job as early as possible — minimize turns spent on setup/verification before submission. Save intermediate structures early so partial credit is secured even if the computation times out.
