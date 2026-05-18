---
name: inspect-atomic-structure
description: "Trigger to inspect an existing structure and report formula, symmetry/Wyckoff, dimensionality, slab vacuum, molecular-crystal indication, disorder, partial occupancy, CRYST1/PBC, or solvent fragments."
skill_type: operator
---

# Inspect Atomic Structure

Use this skill whenever a task starts from an existing structure file, and after
every build, transform, or assemble operation.

## Decision Tree

1. If the file is periodic (`CIF`, `POSCAR`, `CONTCAR`, periodic XYZ), parse it
   as a `pymatgen.core.Structure`.
2. If the file is molecular (`SDF`, `MOL`, nonperiodic XYZ/PDB), parse it as an
   ASE `Atoms` or `pymatgen.core.Molecule`.
3. If the periodic structure contains discrete molecules under PBC connectivity,
   set `is_molecular_crystal=true` and route operations that may break
   molecules to `operate-molecular-crystal`.
4. If it contains a large vacuum dimension, classify it as slab/interface and
   report the vacuum direction and approximate vacuum thickness.

## Local API

Operator snippets normally run in the Bohrium remote shell image. Simple
`ase`/`pymatgen` inspections also work in the worker venv.

```python
from pathlib import Path

from pymatgen.analysis.structure_analyzer import SpacegroupAnalyzer
from pymatgen.core import Structure

path = Path("structure.cif")
structure = Structure.from_file(path)
formula = structure.composition.reduced_formula
natoms = len(structure)
species_counts = structure.composition.get_el_amt_dict()
lattice = structure.lattice

analyzer = SpacegroupAnalyzer(structure, symprec=0.1)
spacegroup = analyzer.get_space_group_symbol()
wyckoff = analyzer.get_symmetrized_structure().wyckoff_symbols
```

For molecule-like files:

```python
from ase.io import read

atoms = read("molecule.xyz")
formula = atoms.get_chemical_formula()
natoms = len(atoms)
```

For molecular-crystal routing, prefer `molcrys_kit` when available:

```python
from molcrys_kit.io.cif import read_mol_crystal

mol_crystal = read_mol_crystal("structure.cif")
is_molecular_crystal = True
```

If `molcrys_kit` import fails on a task that needs molecular connectivity, stop
and report the dependency problem instead of falling back to bond-cutting logic.

## Hard Guards

- Inspect before and after every mutation that writes a new structure file.
- Report formula, atom count, lattice parameters, dimensionality, and
  `is_molecular_crystal`.
- Do not call a structure valid only because the parser succeeded. Check atom
  counts, lattice sanity, and obvious overlaps/vacuum.
- If a slab is detected, estimate vacuum thickness. A vacuum dimension below
  15 A is suspect for surface simulations.
- If a CIF reports disorder or partial occupancies, route ordering work to
  `transform-atomic-structure` or `operate-molecular-crystal`.

## Acceptance Checklist

Report these items in the final answer whenever this skill is used:

- Formula agrees with the file contents and any declared CIF formula when present.
- Atom count equals the sum of species counts.
- Lattice parameters and angles are physically plausible for the claimed class.
- Space group and Wyckoff sites are reported when symmetry analysis succeeds.
- Slab/interface structures include vacuum direction and approximate vacuum size.
- Molecular-crystal routing decision is explicit: `is_molecular_crystal=true/false`.

## Cross-Skill Refs

- Use `build-atomic-structure` when there is no input structure yet.
- Use `transform-atomic-structure` for single-structure mutations.
- Use `assemble-atomic-structure` for slabs, adsorbates, interfaces, amorphous
  boxes, and crosslinks.
- Use `operate-molecular-crystal` whenever PBC molecule integrity matters.
- `matmaster/skills/playground-skills/retrieve-structure/scripts/assess_structure.py`
  remains a quick CLI validation fallback.
