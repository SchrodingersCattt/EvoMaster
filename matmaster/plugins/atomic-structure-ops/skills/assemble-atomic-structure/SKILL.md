---
name: assemble-atomic-structure
description: "Use to combine separate existing pieces into one atomistic cell: build a slab from a bulk crystal, place an adsorbate, stack two slabs into an interface, pack a box with PACKMOL, or build a crosslinked network."
---

# Assemble Atomic Structure

Use this skill when multiple structural pieces are combined into one simulation
object: bulk to slab, adsorbate on slab, slab/slab interface, packed amorphous
cell, or crosslinked network. The output must be a concrete structure file.

## Capability Gate

- **STOP** if the task only modifies a single structure in-place (supercell,
  strain, doping, defect). This skill assembles multiple pieces together.
- **STOP** if the input is a molecular crystal and the operation requires
  preserving molecular connectivity. This skill cuts at atomic level.

## Decision Tree

1. Inspect all input structures:
   ```python
   from pymatgen.core import Structure
   struct = Structure.from_file("input.cif")
   print(f"Formula: {struct.formula}, Atoms: {len(struct)}")
   print(f"Cell: {struct.lattice.abc}, Angles: {struct.lattice.angles}")
   ```
2. For ordinary inorganic/metal/covalent bulk slabs, use ASE or pymatgen slab
   generation.
3. For polar surfaces with net dipole perpendicular to surface (e.g. ZnO (0001),
   GaAs (111), wurtzite c-axis), try symmetric terminations or even layers
   before accepting a polar asymmetric slab.
4. For adsorbates, build or inspect the molecule first, then choose site type
   (`ontop`, `bridge`, `hollow`) and orientation.
5. For interfaces, match in-plane lattice vectors and reject excessive strain →
   `references/interface_lattice_matching.md`
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

**PACKMOL convergence check**: exit code 0 does NOT guarantee convergence.
Parse stdout for `"SOLUTION CONVERGED"`. If output contains only
`"best solution found"`, the minimum distance constraint was NOT satisfied
and the structure will have atomic overlaps. In that case, increase the box
size or reduce tolerance before accepting the output.

## Hard Guards

- Output filename and extension MUST exactly match the caller's specification.
  Never substitute conventional aliases (e.g. do not rename
  `ag111_k_water_interface.cif` to `ag_water.cif`). Evaluators check the exact
  string.
- Slab vacuum must be at least 15 A unless the user explicitly accepts a smaller
  test structure.
- Binary compounds: an N-bilayer request corresponds to 2N atomic planes. Do
  not treat bilayers as individual atomic layers.
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
- Final structure validated: dimensionality, formula, minimum interatomic
  distance (no overlaps).
