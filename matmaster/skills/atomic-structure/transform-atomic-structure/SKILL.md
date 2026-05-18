---
name: transform-atomic-structure
description: "Transform non-molecular crystals: supercell, strain, doping, vacancy, ordering, mutations. Routes molecular crystals to operate-molecular-crystal. Not for joining multiple structures—use assemble-atomic-structure."
skill_type: operator
depends_on: inspect-atomic-structure, operate-molecular-crystal
---

# Transform Atomic Structure

Use this skill for operations that start from one structure and produce a
modified version of the same object. It handles ordinary inorganic, metallic,
ionic, and covalent crystals. If inspection shows a molecular crystal, route to
`operate-molecular-crystal` before any operation that depends on connectivity.

## Decision Tree

1. Run `inspect-atomic-structure` on the input.
2. If `is_molecular_crystal=true`, route to `operate-molecular-crystal`.
3. If the user requests only cell multiplication, use a supercell matrix.
4. If the user requests strain/shear, apply a deformation gradient and decide
   whether atom coordinates should scale with the lattice.
5. If the user requests dopants, select sites by mode:
   - `random`: reproducible random indices with a fixed seed.
   - `ordered`: choose symmetry-related sites or ordered sublattices.
   - `wyckoff`: choose sites from explicit Wyckoff labels.
6. If valence changes, require explicit oxidation states or a charge
   compensation strategy.

## Local API

Supercell:

```python
from ase.build import make_supercell
from ase.io import read, write

atoms = read("input.cif")
supercell = make_supercell(atoms, [[2, 0, 0], [0, 2, 0], [0, 0, 1]])
write("supercell.cif", supercell)
```

Strain:

```python
from pymatgen.core import Structure

structure = Structure.from_file("input.cif")
structure.apply_strain([[0.02, 0, 0], [0, 0, 0], [0, 0, 0]])
structure.to(filename="strained.cif")
```

Doping and defects can be written inline with `pymatgen` site selection for
simple cases. For charge compensation, ordered Wyckoff targeting, or multiple
doping rules, drive selection with `pymatgen.analysis.structure_analyzer.
SpacegroupAnalyzer` plus explicit Wyckoff filtering, then write the doped
structure with `Structure.replace_species` / `Structure.remove_sites`. Run the
full doping/defect acceptance checklist below before reporting success.

## Hard Guards

- Output filename and extension MUST exactly match the caller's specification
  (spelling, casing, abbreviation, suffix). Never substitute conventional
  aliases or systematic equivalents (e.g. do not rename `gamma_alumina.cif`
  to `gamma_al2o3.cif`, or `srtio3_doped.cif` to `STO_doped.cif`). Evaluators
  check the exact string before opening the file.
- Always preserve the input file and write a new output file.
- Supercell mode requires an integer 3x3 matrix or three integer repeats.
- Deformation mode must state whether atomic coordinates are scaled with the
  cell. Default to scaling atoms for physical strain.
- `fraction` and `count` are mutually exclusive for any doping rule.
- Do not silently replace zero atoms. If `fraction * site_count < 1`, require a
  larger supercell or an exact count.
- Use a fixed seed for stochastic replacements and report it.
- For molecular crystals, do not remove individual atoms or cut bonds here.

## Doping and Defect Acceptance Checklist

Every doping/defect result must be checked and reported:

1. **Stoichiometry**: actual replacement/removal count equals the requested
   count or `round(site_count * fraction)`. Output formula must equal input
   formula minus removed species plus replacement species.
2. **Charge balance**: if oxidation states are provided or inferable, total
   charge after substitution must be close to neutral. If not neutral, report
   the explicit compensation strategy (`anion_adjust`, `cation_vacancy`,
   `anion_vacancy`, `mixed`, or user-approved uncompensated charge).
3. **Minimum distance**: no interatomic distance below the accepted threshold
   (default 0.5 A unless the task sets another value).
4. **Symmetry trace**: report space group before and after. Random substitutions
   may lower symmetry; ordered/Wyckoff substitutions should preserve intended
   symmetry or explain why it changed.
5. **Wyckoff fidelity**: in Wyckoff mode, every substituted atom must belong to
   the requested Wyckoff label/group under `SpacegroupAnalyzer`.
6. **Multi-rule disjointness**: multiple doping rules must not select the same
   site twice.
7. **Determinism**: same seed and same input should reproduce the same selected
   sites and output coordinates.
8. **Supercell sanity**: if requested concentration is impossible in the current
   cell, build a supercell first rather than silently rounding to zero.

Defects additionally require mass balance. If a vacancy creates an isolated
unphysical fragment, stop or route to `operate-molecular-crystal` for
molecule-cluster removal.

## Acceptance Checklist

- Input and output filenames are both reported.
- `inspect-atomic-structure` was run before and after the operation.
- Formula, atom count, and lattice change match the requested transformation.
- For stochastic operations, the seed and selected site summary are reported.

## Cross-Skill Refs

- `operate-molecular-crystal`: PBC-aware molecule operations.
- `assemble-atomic-structure`: surfaces, interfaces, amorphous packing.
- `inspect-atomic-structure`: mandatory validation and Wyckoff analysis.
