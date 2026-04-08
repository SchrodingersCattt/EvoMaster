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

## generate_ordered_replicas

- Report **both** the original disordered formula **and** the ordered replica expanded formula (integer stoichiometry). Write formulas as concatenated strings without spaces: `H144C48N24Cl24O96` (NOT `H144 C48 N24 Cl24 O96`). If CIF returns space-separated elements, concatenate them.
- For each replica, explain: (a) which sites are disordered and how, (b) how the ordered config was chosen (valence/charge balance/connectivity), (c) what changed. A bare filename list without chemical reasoning = fail.
- On timeout → fall back to pymatgen `OrderDisorderedStructureTransformation`.

## add_hydrogens / passivation

- Si/Ge surface passivation: target Si-H ≈ 1.48 Å along tetrahedral direction (~109.47°). Passivate **all** dangling bonds on **both** top and bottom surfaces.
- **Key principle**: a surface Si atom with coordination N needs (4 − N) hydrogen atoms. For reconstructed surfaces like Si(100)-2×1, surface dimers have coordination 2 → each dimer atom needs 2 H atoms, not 1.
- After passivation: compute coordination numbers for ALL surface atoms using TWO cutoffs:
  - Si-Si bonds: cutoff 2.6 Å
  - Si-H bonds: cutoff 1.8 Å
  - Total coordination = (Si-Si neighbors) + (Si-H neighbors). Must equal 4 for every surface Si.
- If ANY surface atom has coordination < 4 after first passivation pass, compute the missing bond directions (tetrahedral angles relative to existing bonds) and add H atoms at 1.48 Å along those directions. Re-check coordination. Iterate until ALL surface Si reach coordination = 4.
- **Verification script** (mandatory): after saving the passivated structure, re-read it and print per-atom coordination for every Si atom. Flag any Si with coordination ≠ 4. The final answer MUST report mean Si coordination and confirm it equals 4.0.
- Save the passivated structure, then verify by re-reading and checking. Report: total H added, mean Si-H bond length, per-atom coordination check, and confirmation that all surface Si atoms have coordination = 4.
