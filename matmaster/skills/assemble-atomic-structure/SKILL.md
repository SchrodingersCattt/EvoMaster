---
name: assemble-atomic-structure
description: "Use to combine separate existing pieces into one atomistic cell: build a slab from a bulk crystal, place an adsorbate, stack two slabs into an interface, pack a box with PACKMOL, or build a crosslinked network."
skill_type: operator
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

1. Inspect all input structures first.
2. For ordinary inorganic/metal/covalent bulk slabs, use ASE or pymatgen slab
   generation.
3. For polar ionic surfaces (Type-3), try symmetric terminations or even layers
   before accepting a polar asymmetric slab.
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

**PACKMOL convergence check**: exit code 0 does NOT guarantee convergence.
Parse stdout for `"SOLUTION CONVERGED"`. If output contains only
`"best solution found"`, the minimum distance constraint was NOT satisfied
and the structure will have atomic overlaps. In that case, increase the box
size or reduce tolerance before accepting the output.

For interface lattice matching, use `ZSLGenerator` from
`pymatgen.analysis.interfaces.zsl`:

```python
import numpy as np
from pymatgen.analysis.interfaces.zsl import ZSLGenerator
from pymatgen.core.surface import SlabGenerator

# Slab generation — use filter_out_sym_slabs=False to avoid
# StructureMatcher numpy compatibility issues
slab_gen = SlabGenerator(bulk, miller, min_slab_size=8, min_vacuum_size=15)
slabs = slab_gen.get_slabs(symmetrize=False, filter_out_sym_slabs=False)

# Lattice matching — enumerate all matches, sort by area
zsl = ZSLGenerator(max_area_ratio_tol=0.09, max_angle_tol=0.01,
                   max_length_tol=0.03)
matches = list(zsl(slab_a.lattice.matrix[:2], slab_b.lattice.matrix[:2],
                   lowest=True))
# Sort by interface area and pick the smallest within strain budget
matches.sort(key=lambda m: m.match_area)

# Strain calculation — use the pre-computed sl_vectors from ZSLMatch,
# do NOT recompute via transformation @ original_lattice (breaks for
# non-orthogonal cells like hexagonal).
def calc_strain(m):
    fa, fb = np.linalg.norm(m.film_sl_vectors[0]), np.linalg.norm(m.film_sl_vectors[1])
    sa, sb = np.linalg.norm(m.substrate_sl_vectors[0]), np.linalg.norm(m.substrate_sl_vectors[1])
    return abs(fa - sa) / sa, abs(fb - sb) / sb

best = min((m for m in matches if max(calc_strain(m)) < 0.05),
           key=lambda m: m.match_area)
```

Higher-level `SubstrateAnalyzer`/`CoherentInterfaceBuilder` can also work but
may trigger internal bugs; `ZSLGenerator` is more robust. Fall back to manual
stacking if no candidate fits.

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
