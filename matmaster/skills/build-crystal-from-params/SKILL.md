---
name: build-crystal-from-params
description: "Build simple inorganic crystals from explicit prototype, lattice, or space-group/Wyckoff parameters with guards against silent coordinate/setting/cell/stoichiometry errors. Not for isolated molecules, polymers, molecular crystals, or polyatomic ionic crystals; route to retrieve-structure instead."
skill_type: operator
---

# Build Crystal From Params

Use this skill only when the user gives explicit parameters for a simple
inorganic crystal and wants a saved structure file. This skill is mainly a
guardrail against crystal-building mistakes that silently produce plausible but
wrong structures.

## Decision Tree

1. Element/formula plus prototype and lattice constants: use ASE `bulk` or a
   documented prototype builder.
2. Space group plus Wyckoff coordinates: use
   `pymatgen.Structure.from_spacegroup`.
3. Known material, molecular crystal, polyatomic ionic crystal, DOI/SI/database
   source, or incomplete/implicit coordinates: route to `retrieve-structure`
   first instead of constructing from guessed parameters.

## Local API

Operator snippets run in the Bohrium remote shell image.

Bulk template:

```python
from ase.build import bulk
from ase.io import write

atoms = bulk("NaCl", crystalstructure="rocksalt", a=5.64, cubic=True)
write("nacl.cif", atoms)
```

Wyckoff construction:

```python
from pymatgen.core import Lattice, Structure

lattice = Lattice.from_parameters(a, b, c, alpha, beta, gamma)
structure = Structure.from_spacegroup(
    spacegroup,
    lattice,
    species=["Ti", "O"],
    coords=[[0, 0, 0], [0.305, 0.305, 0]],
)
structure.to(filename="structure.cif")
```

## Hard Guards

- **Never hand-write fractional coordinates into an ASE `Atoms(positions=...)`
  call.** ASE `positions` are Cartesian coordinates. Use
  `Structure.from_spacegroup`, `Structure(..., coords_are_cartesian=False)`, or
  `Atoms(scaled_positions=...)` when starting from fractional coordinates.
- Do not guess missing lattice constants, space-group settings, origin choice,
  or Wyckoff coordinates. Ask the user, retrieve a known structure, or cite the
  data source used.
- For space groups with multiple settings or origin choices, record the exact
  setting/origin used. Convert literature coordinates before calling
  `from_spacegroup` when the source uses a different origin choice.
- Make primitive vs conventional cell choice explicit. `ase.build.bulk` may
  return a primitive cell unless the requested prototype and options specify a
  conventional cell.
- Confirm all intended Wyckoff sites are included. `from_spacegroup` will build
  a valid lower-stoichiometry structure if an input site is omitted.
- For Hermann-Mauguin symbols with setting ambiguity, especially monoclinic
  settings, prefer an explicit space-group number plus setting/origin notes.
- Output filename and extension MUST exactly match the caller's specification
  (spelling, casing, abbreviation, suffix). Never substitute conventional
  aliases or systematic equivalents.

## Acceptance Checklist

- The requested formula/stoichiometry matches the output file.
- Atom count matches the intended primitive/conventional/supercell choice.
- Space group is reported when symmetry analysis works.
- The chosen setting/origin choice is reported for Wyckoff builds.
- Every delivered structure has been checked with `inspect-atomic-structure`.

## Cross-Skill Refs

- `inspect-atomic-structure`: mandatory validation.
- `retrieve-structure`: known structures, molecular crystals, polyatomic ionic
  crystals, database/DOI/SI sources, and incomplete coordinate data.
- `transform-atomic-structure`: supercells, strain, doping, defects.
- `assemble-atomic-structure`: surfaces, interfaces, adsorbates, amorphous
  cells, and crosslinks.
