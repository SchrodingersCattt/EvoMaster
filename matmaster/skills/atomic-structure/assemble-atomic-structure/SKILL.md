---
name: assemble-atomic-structure
description: "Assemble multi-body structures from existing pieces: surface slab from bulk, adsorbate on surface, slab/slab interface, amorphous packing (PACKMOL), and geometric crosslink. For molecular-crystal slabs, route to operate-molecular-crystal. For polar Type-3 fixes, route to tasker-polar-surface."
skill_type: operator
depends_on: inspect-atomic-structure, operate-molecular-crystal, tasker-polar-surface
---

# Assemble Atomic Structure

Use this skill when multiple structural pieces are combined into one simulation
object: bulk to slab, adsorbate on slab, slab/slab interface, packed amorphous
cell, or crosslinked network. The output must be a concrete structure file.

## When to Use

- Cut a surface slab from a non-molecular bulk crystal.
- Place molecular adsorbates on surfaces.
- Stack two slabs into an interface.
- Pack molecules or polymer chains into an amorphous periodic cell.
- Build a geometric crosslink network from an existing packed cell.

If the bulk is a molecular crystal, use `operate-molecular-crystal` for slab
cutting because ASE/pymatgen slab cuts may break molecules across PBC.

## Decision Tree

1. Inspect all input structures first.
2. For ordinary inorganic/metal/covalent bulk slabs, use ASE or pymatgen slab
   generation.
3. For polar ionic surfaces, route through `tasker-polar-surface` before
   accepting the slab.
4. For adsorbates, build or inspect the molecule first, then choose site type
   (`ontop`, `bridge`, `hollow`) and orientation.
5. For interfaces, match in-plane lattice vectors and reject excessive strain.
6. For amorphous cells, use PACKMOL with exactly two of box size, density, and
   molecule counts.

## Local API

Slab:

```python
from ase.build import surface
from ase.io import read, write

bulk = read("bulk.cif")
slab = surface(bulk, (1, 0, 0), layers=6, vacuum=15.0)
write("slab.cif", slab)
```

Adsorbate site enumeration:

```python
from pymatgen.analysis.adsorption import AdsorbateSiteFinder
from pymatgen.core import Structure

slab = Structure.from_file("slab.cif")
finder = AdsorbateSiteFinder(slab)
sites = finder.find_adsorption_sites()["all"]
```

PACKMOL is available in the Bohrium remote image after this branch:

```bash
packmol < packmol.inp
```

For interface lattice matching, drive `pymatgen.analysis.interfaces` (e.g.
`SubstrateAnalyzer` / `CoherentInterfaceBuilder`) directly: enumerate matched
in-plane vectors, screen by strain budget, and fall back to a manual stacking
recipe if no candidate fits. Crosslink networks can be assembled with
`networkx`-driven bond candidates plus `ase.geometry` distance checks. Document
the recipe and the acceptance numbers; do not silently substitute heuristics.

## Hard Guards

- Output filename and extension MUST exactly match the caller's specification
  (spelling, casing, abbreviation, suffix). Never substitute conventional
  aliases (e.g. do not rename `ag111_k_water_interface.cif` to
  `ag_water.cif`, or `ceo2_111_trilayer.cif` to `ceo2_111.cif`). Evaluators
  check the exact string before opening the file.
- Slab vacuum must be at least 15 A unless the user explicitly accepts a smaller
  test structure.
- Binary compounds: an N-bilayer request corresponds to 2N atomic planes. Do
  not treat bilayers as individual atomic layers.
- Polar Type-3 surfaces (for example zinc blende (001), wurtzite (0001)) need
  even layers or symmetric terminations when possible. Try terminations before
  accepting a polar asymmetric slab.
- Interfaces must report in-plane strain. If any in-plane strain exceeds 20%,
  stop or ask the user to accept the mismatch.
- Amorphous packing must specify exactly two of `box_size`, `density`, and
  `molecule_numbers`.
- Crosslink generation is geometric only; coordinates are not relaxed and the
  result should be minimized before production MD.

## Acceptance Checklist

- Every input and output file path is reported.
- Formula and atom counts equal the sum of assembled components.
- Slab outputs report Miller index, layer interpretation, vacuum thickness, and
  termination choice if applicable.
- Adsorbate outputs report site type, anchor atom, height, and orientation.
- Interface outputs report strain and stacking axis.
- Amorphous outputs report molecule counts, box size, and resulting density.
- The final structure is inspected with `inspect-atomic-structure`.

## Cross-Skill Refs

- `operate-molecular-crystal`: molecular-crystal slabs and molecule-preserving
  operations.
- `build-atomic-structure`: build molecules, bulk cells, and polymer chains used
  as assembly inputs.
- `tasker-polar-surface`: required fallback for polar slabs.
