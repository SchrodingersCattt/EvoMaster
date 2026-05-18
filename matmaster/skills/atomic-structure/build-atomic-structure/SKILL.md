---
name: build-atomic-structure
description: "Build a new atomic structure from spec: bulk crystal (template / spacegroup+Wyckoff), molecule (SMILES / hand-coords), polymer chain (architecture+monomer sequence). Use when there is no input structure file. Not for modifying an existing structure — use transform-atomic-structure or assemble-atomic-structure."
skill_type: operator
depends_on: inspect-atomic-structure, poly-generator
---

# Build Atomic Structure

Use this skill when the user wants a new atomic structure and has not provided a
starting structure file. The output should be a saved file, not just a recipe.

## Decision Tree

1. Element or formula plus prototype: use ASE `bulk`.
2. Space group plus Wyckoff coordinates: use `pymatgen.Structure.from_spacegroup`.
3. SMILES: use RDKit embedding and write SDF/XYZ/PDB.
4. Monomer sequence or polymer architecture: use `poly-generator` for
   marker-based polymers (PSP/RDKit driven). For exotic architectures or
   monomer aliases not covered locally, ask the user for explicit SMILES and
   sequence and build with the RDKit + tail-coupling recipe below.
5. Molecule needing a periodic box: add a cell and vacuum after molecule build.

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

SMILES molecule:

```python
from rdkit import Chem
from rdkit.Chem import AllChem

mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
if mol is None:
    raise ValueError("Invalid SMILES")
AllChem.EmbedMolecule(mol, randomSeed=42)
AllChem.UFFOptimizeMolecule(mol)
Chem.MolToMolFile(mol, "molecule.mol")
```

## Hard Guards

- **Never hand-write fractional coordinates into a manually constructed ASE
  `cell` array.** Fractional coordinates depend on the cell angle convention
  (γ=60° vs 120° for hexagonal). Use `pymatgen Lattice.from_parameters()` +
  `Structure()` or `Structure.from_spacegroup()` to avoid mismatch. ASE `bulk()`
  is safe for named prototypes only.
- Output filename and extension MUST exactly match the caller's specification
  (spelling, casing, abbreviation, suffix). Never substitute conventional
  aliases or systematic equivalents (e.g. do not rename `gamma_alumina.cif`
  to `gamma_al2o3.cif`, `CeO2.cif` to `ceria.cif`, `NaCl.cif` to `nacl.cif`,
  or `slab_h_passivated.vasp` to `slab.vasp`). Evaluators check the exact
  string before opening the file.
- Supported ASE prototype names are: `sc`, `fcc`, `bcc`, `hcp`,
  `rhombohedral`, `orthorhombic`, `mcl`, `diamond`, `zincblende`,
  `rocksalt`, `cesiumchloride`, `fluorite`, and `wurtzite`.
- Do not guess missing lattice constants or Wyckoff coordinates. Ask the user or
  fetch a known structure first.
- SMILES parsing failure is fatal. Do not silently generate random coordinates.
- A polymer-chain SDF is one isolated molecule with no periodic box and is not a
  ready-to-run MD or DFT input. Send it to `assemble-atomic-structure` for
  amorphous packing when a simulation cell is required.

## Acceptance Checklist

- The requested formula/stoichiometry matches the output file.
- Atom count matches the intended primitive/conventional/supercell choice.
- Space group is reported for crystalline outputs when symmetry analysis works.
- Molecule outputs include formula and molar mass.
- Polymer outputs explicitly state isolated-chain status and downstream packing
  requirement when relevant.
- Every delivered structure has been checked with `inspect-atomic-structure`.

## Cross-Skill Refs

- `inspect-atomic-structure`: mandatory validation.
- `transform-atomic-structure`: supercells, strain, doping, defects.
- `assemble-atomic-structure`: surfaces, interfaces, amorphous cells, crosslinks.
- `poly-generator`: local lightweight polymer builder and 2D/3D reports.
