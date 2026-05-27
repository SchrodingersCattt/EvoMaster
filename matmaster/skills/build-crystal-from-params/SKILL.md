---
name: build-crystal-from-params
description: "Build simple inorganic crystals from prototype name, lattice parameters, or space-group/Wyckoff data. Covers well-known binaries (NaCl, NiO, Si, ZnS, etc.) with standard lattice constants. Not for molecular crystals or complex polyatomic compounds with ambiguous structures."
skill_type: operator
---

# Build Crystal From Params

Use this skill when the task requires building a simple inorganic crystal
structure file. This includes cases where the user gives explicit parameters
AND well-known binary compounds whose prototype and lattice constants are
standard literature values. This skill is mainly a guardrail against
crystal-building mistakes that silently produce plausible but wrong structures.

## Decision Tree

1. Element/formula plus prototype and lattice constants: use ASE `bulk` or a
   documented prototype builder.
2. Simple binary with well-known prototype (rocksalt, fluorite, zincblende,
   diamond, wurtzite, etc.) but lattice constant not given explicitly: use
   standard literature values (e.g. NiO rocksalt a=4.17, Si diamond a=5.43,
   NaCl rocksalt a=5.64, ZnO wurtzite a=3.25 c=5.21).
3. Space group plus Wyckoff coordinates: use
   `pymatgen.Structure.from_spacegroup`.
4. Molecular crystal, polyatomic ionic crystal (CaCO3, K2SO4, etc.),
   DOI/SI/database source, or incomplete Wyckoff coordinates: this skill
   cannot safely construct these — stop and report that a database lookup
   or literature retrieval is needed.

## Local API

Operator snippets run in the Bohrium remote shell image.

Bulk template:

```python
from ase.build import bulk
from ase.io import write

atoms = bulk("NaCl", crystalstructure="rocksalt", a=5.64, cubic=True)
write("nacl.cif", atoms)
```

Common ASE `bulk(..., crystalstructure=...)` names include `sc`, `fcc`,
`bcc`, `hcp`, `rhombohedral`, `orthorhombic`, `mcl`, `diamond`,
`zincblende`, `rocksalt`, `cesiumchloride`, `fluorite`, and `wurtzite`. If ASE
rejects a prototype name, do not hand-write replacement coordinates; switch to a
documented Wyckoff construction or retrieve a known structure.

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
  data source used. **Exception**: for simple elemental/binary compounds with a
  single well-known polymorph (Si, Cu, NaCl, NiO, ZnO, etc.), standard textbook
  lattice constants are acceptable without explicit user input.
- **Ternary or higher compounds (3+ elements) almost always require Wyckoff
  construction or database retrieval.** Do NOT assume a simple prototype
  (perovskite, anti-perovskite, etc.) without verifying — many ternary phases
  (MAX phases, Heusler alloys, spinels) have specific space groups that differ
  from naive guesses. When in doubt, retrieve from a database rather than
  constructing from guessed parameters.
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
