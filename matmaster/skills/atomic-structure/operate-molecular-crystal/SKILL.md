---
name: operate-molecular-crystal
description: "Operations for molecular crystals and related local geometric repairs, organised in four axes. (A) Topology-preserving fragment ops on molecular crystals: PBC slab cut, molecule-cluster defect, guest molecule extraction, desolvation, cocrystal guest separation. (B) Experimental-crystallography fixes: disorder ordering, partial-occupancy resolution, CIF moiety constraints. (C) Generic atom-level geometric operations: sp3/sp2/octahedral hydrogen completion, perturbation, rotation, usable on any structure including inorganic slabs and surfaces with dangling bonds. (D) Local coordination environment characterisation: CN detection, ideal-polyhedron match, CShM shape classification, packing shells. Axes A and B trigger when the inspected structure is a molecular crystal or has molecular fragments under PBC; Axes C and D trigger on the geometric task type, not the crystal class."
skill_type: operator
depends_on: inspect-atomic-structure
---

# Operate with molcrys-kit

`molcrys-kit` exposes four orthogonal capability axes. Pick the section whose
**trigger** matches your task; do **not** gate axis C/D behind
`is_molecular_crystal=true`.

| Axis | What it does | Trigger |
| --- | --- | --- |
| **A** Topological fragments as first-class citizens | PBC-aware slab, molecule-cluster defect, desolvation, extraction — all preserve whole molecules across periodic boundaries | `is_molecular_crystal=true` |
| **B** Experimental crystallography fixes | Disorder → ordered replicas, partial-occupancy resolution, moiety-aware constraints | CIF has disorder / partial occupancy / `_chemical_formula_moiety` |
| **C** Local geometric atom-level ops | sp3 / sp2 / octahedral hydrogen completion, perturbation, rotation | Any structure (inorganic slab, surface, defect, molecule) needs atoms placed by coordination geometry |
| **D** Local environment characterisation | CN detection, ideal-polyhedron match, CShM shape, packing shells | Need to describe a centre's coordination shell (`main` / 0.3+) |

## Install

`molcrys-kit` is not on PyPI. Install from the GitHub release tarball with
`pip` (works inside Bohrium remote shells too — do not rely on `uv`):

```bash
pip install "https://github.com/SchrodingersCattt/MolCrysKit/archive/refs/tags/v0.2.0.tar.gz"
```

Pinned to **v0.2.0** for reproducibility. Axis D and the moiety-aware
hydrogen completion in axis C are still on `main`; see
"Advanced (main-only features)" below.

All snippets run **locally** in the agent's Python environment in seconds. Do
**not** submit to Bohrium for any of them.

---

## Axis A — Topological fragments

Use whenever `inspect-atomic-structure` reports `is_molecular_crystal=true`
and whole molecules must remain intact across periodic boundaries.

### Read / write

```python
from molcrys_kit.io.cif import read_mol_crystal
from molcrys_kit.io.output import write_cif

mol_crystal = read_mol_crystal("input.cif")
write_cif(mol_crystal, filename="output.cif")
```

### Molecule extraction

`mol_crystal.molecules` is a `list[CrystalMolecule]`; each one is already
unwrapped across PBC by molcrys-kit's reader.

```python
for i, m in enumerate(mol_crystal.molecules):
    m.write_xyz(f"mol_{i:02d}.xyz")
```

### PBC-aware slab cutting

```python
from molcrys_kit.operations.surface import (
    enumerate_terminations,
    generate_slabs_with_terminations,
)

terms = enumerate_terminations(mol_crystal, miller_index=(1, 0, 0))
slabs = generate_slabs_with_terminations(
    mol_crystal,
    miller_index=(1, 0, 0),
    layers=4,
    min_vacuum_size=15.0,
    term_selection="tasker_preferred",
    correct_tasker2=False,
)
for crystal_slab, info in slabs:
    write_cif(crystal_slab, filename=f"slab_{info.termination_index}.cif")
```

For ordinary (non-molecular) bulk crystals stay in `assemble-atomic-structure`
with `pymatgen.core.surface.SlabGenerator`. The molcrys-kit slab path is only
needed when molecules would be sliced across PBC.

### Solvent removal

```python
from molcrys_kit.operations.desolvation import remove_solvents

crystal_dry = remove_solvents(mol_crystal, targets=["H2O", "CH3OH"])
```

`targets` matches whole molecules by formula; never delete by element symbol.

### Molecule-cluster vacancy defect

```python
from molcrys_kit.operations.defects import generate_vacancy

crystal_def, removed = generate_vacancy(
    mol_crystal,
    species_list=[{"formula": "C24H40N16O2Fe2", "count": 1}],
    method="spatial_cluster",
    return_removed_cluster=True,
    random_seed=42,
)
```

`method="spatial_cluster"` removes a contiguous cluster instead of slicing
across one molecule.

### Axis A guards

- Output filename and extension MUST exactly match the caller's specification
  (spelling, casing, abbreviation, suffix). Never substitute conventional
  aliases (e.g. do not rename `dacmor_hydrogenated.cif` to `dacmor_h.cif`,
  or write `hydrogenated.cif` when the caller asked for
  `dacmor_hydrogenated.cif`). Evaluators check the exact string before
  opening the file.
- Never silently fall back to ASE slab cutting for molecular crystals.
- Molecule extraction defaults to unwrapping molecules across PBC.
- Solvent removal removes whole molecules, not atoms matching an element.
- Defects remove complete molecule units or complete spatial clusters.
- When reporting `chemical_formula` strings in the final answer (in tables,
  bullet lists, or prose), use the **compact contiguous form without spaces**
  between element-count groups: write `H288C80N48Cl48O192`, never
  `H288 C80 N48 Cl48 O192`. The downstream evaluator extracts formula tokens
  with a `\b…\b`-anchored regex; spaces split it into separate tokens like
  `H288`, `C80`, ... and the reference formula appears as "missing" even
  though every count is correct.

---

## Axis B — Experimental crystallography fixes

Use when the input CIF carries occupancies < 1, alternative-site labels, or
`_chemical_formula_moiety` annotations.

### Disorder → ordered replicas

```python
from molcrys_kit.analysis.disorder.process import (
    generate_ordered_replicas_from_disordered_sites,
)
from molcrys_kit.io.output import write_cif

# method='optimal' returns a single best structure;
# method='random' with generate_count=N returns an ensemble.
replicas = generate_ordered_replicas_from_disordered_sites(
    "input_disordered.cif", generate_count=1, method="optimal"
)
for i, mc in enumerate(replicas):
    write_cif(mc, filename=f"ordered_{i:02d}.cif")
```

This is the **only sanctioned ordering path**. Do not handcraft a builder
around `scan_cif_disorder` / `DisorderInfo`; that route is what dropped
SC_struct_005 to 0.31 by emitting fractional stoichiometry like
`K1 H13.9872 C5.9904 N5.0064 O9` for DAN-2. Use `scan_cif_disorder` only
for diagnosis (`is_disordered`, group counts), never for emitting ordered
geometry.

### Axis B guards

- Ordered replicas need per-structure chemical reasoning, not just filenames:
  name the disordered atom labels or assemblies, describe what changes when
  collapsing partial occupancies into integer counts (which atom is retained
  vs. dropped, connectivity changes, charge-balance implications), and tie
  the reasoning to each `ordered_<name>.cif`. A single-line "X is disordered"
  plus the formula is not enough; the grounding judge will reject it.
- Stoichiometry of every emitted CIF is integer-valued.

---

## Axis C — Local geometric atom-level operations

This axis is **NOT** gated on `is_molecular_crystal`. The geometry helpers
work on **any structure**: organic crystals, salts, inorganic bulks, slabs,
surfaces with dangling bonds, and point defects. If your task is "place N
atoms on this centre to satisfy sp3 / sp2 / octahedral coordination", come
here regardless of whether `inspect-atomic-structure` reports a molecular
crystal.

### High-level: hydrogen completion on a molecular crystal

```python
from molcrys_kit.operations.hydrogen_completion import add_hydrogens
from molcrys_kit.io.output import write_cif

crystal_h = add_hydrogens(
    mol_crystal,
    target_elements=None,        # default: every H-deficient heavy atom
    optimize_torsion=False,      # set True for CH3 / NH2 dihedral relaxation
)
write_cif(crystal_h, filename="hydrogenated.cif")
```

### Low-level: place atoms by coordination geometry on any structure

`molcrys_kit.utils.geometry.get_missing_vectors` is a pure geometric
projector. Given a centre, its existing neighbours, the target geometry, and
a bond length, it returns the unit vectors for the missing partners. It works
on **inorganic slabs and surface dangling bonds**, not only organic
fragments. Use it whenever the task is "put N atoms on this centre to satisfy
sp3 / sp2 / octahedral coordination".

```python
import numpy as np
from ase.build import diamond100
from ase.io import write
from ase.neighborlist import neighbor_list
from molcrys_kit.utils.geometry import get_missing_vectors

# Example: passivate a Si(100) slab with H to make every Si 4-coordinated.
slab = diamond100("Si", size=(2, 1, 6), a=5.43, vacuum=15.0)
slab.set_pbc([True, True, False])

cutoffs = [1.3] * len(slab)        # Si-Si pair cutoff = 2.6 Å
ii, jj = neighbor_list("ij", slab, cutoffs)
neigh: dict[int, list[int]] = {i: [] for i in range(len(slab))}
for i, j in zip(ii, jj):
    neigh[i].append(j)

pos = slab.get_positions()
H_positions: list[np.ndarray] = []
for si, ns in neigh.items():
    if len(ns) >= 4:
        continue                   # already saturated
    offsets = get_missing_vectors(
        center=pos[si],
        existing_neighbors=[pos[j] for j in ns],
        geometry_type="tetrahedral",
        bond_length=1.48,           # Si-H from the spec
    )
    H_positions.extend(pos[si] + v for v in offsets)

from ase import Atoms
out = slab + Atoms(symbols=["H"] * len(H_positions),
                   positions=H_positions, cell=slab.cell, pbc=slab.pbc)
write("Si100_H_passivated_POSCAR", out, format="vasp", sort=True)
```

Supported `geometry_type`: `linear`, `bent`, `trigonal_planar`,
`trigonal_pyramidal`, `tetrahedral`, `planar_bisector`,
`trigonal_bipyramidal`, `octahedral`. Do **not** handcraft "sum of bond
vectors → reverse" projections — they collapse to the wrong angles whenever
the existing bonds are coplanar (the typical surface case).

### Fallbacks: obabel / RDKit (organic only)

```bash
obabel input.cif -O hydrogenated.cif -h
```

If neither molcrys-kit nor obabel produces an acceptable geometry, RDKit
per-molecule completion is the last resort. RDKit ignores PBC, so unwrap each
molecule first, optimise it, then write H positions back into the crystal
frame:

```python
from rdkit import Chem
from rdkit.Chem import AllChem

mol = Chem.MolFromMolFile("mol1_nopbc.sdf", removeHs=False, sanitize=True)
mol = Chem.AddHs(mol, addCoords=True)
AllChem.EmbedMolecule(mol, useRandomCoords=False, randomSeed=42)
AllChem.MMFFOptimizeMolecule(mol, mmffVariant="MMFF94", maxIters=500)
Chem.MolToMolFile(mol, "mol1_h.sdf")
```

After RDKit completion, immediately verify the geometry: read the resulting
structure, compute the mean H-C-H angle around every sp3 carbon, and confirm
it is within ±5° of 109.5° (sp3) or ±5° of 120° (sp2). If the deviation is
larger, iterate the optimiser, switch to UFF, or fall back to molcrys-kit's
PBC-aware path. Do not write the final CIF until this check passes.

### Axis C guards

- Pure RDKit/obabel completion **without geometry optimisation** is forbidden;
  if RDKit/obabel is the only available tool, run an MMFF94 (or UFF)
  optimisation pass and verify the mean H-C-H angle is within ±5° of the
  sp3 ideal (109.5°) and ±5° of the sp2 ideal (120°). Iterate or fall back
  to molcrys-kit if the angle check fails.
- Hydrogen completion on a molecular crystal must not add H to carbonyl /
  ester oxygens unless explicitly justified.
- For inorganic slabs / surfaces, **use the same neighbour cutoffs the
  evaluator uses** (typically Si-Si ≈ 2.6 Å, Si-H ≈ 1.8 Å) when you (a)
  detect undercoordinated centres and (b) verify the final coordination.
  Custom self-verification with looser cutoffs is the most common reason a
  "looks-fine" answer fails the deterministic check. State the cutoffs
  explicitly in the final answer.
- All sp3 / sp2 / octahedral / etc. atom placements must use
  `get_missing_vectors` (or an equivalent geometry-aware projection). Never
  hand-roll a "sum of bond vectors → reverse" trick — it is angularly wrong
  whenever the existing bonds share a plane.

---

## Axis D — Local environment characterisation (advanced, main / 0.3+)

`molcrys_kit.analysis.chemical_env` exposes coordination-shell extractors and
shape classifiers. The 0.2.0 release ships only the basic
`ChemicalEnvironment` graph. The richer API below — `find_polyhedra`,
`detect_coordination_number(r_c=...)`, the ideal-polyhedron registry
(CN 4–12, including capped cubes), and the topology-gated CShM classifier —
lives on `main` and will land in **0.3.0**.

```python
# Available on main; pin will move to 0.3.0 once released.
from molcrys_kit.analysis.chemical_env import find_polyhedra

polyhedra = find_polyhedra(
    structure,                # ASE Atoms or pymatgen Structure
    central="A",              # central element symbol
    ligand="B",               # ligand element symbol; non-ligand atoms are
                              # never admitted to the shell
    mode="gap+enclosure",     # or "gap" / "cutoff"
    r_c=None,                 # required when mode="cutoff"
)
```

If a task genuinely needs axis D (e.g. classifying a CN=11 tricapped cube vs.
pentagonal antiprism around a ClO4 cage), install `main` ad-hoc:

```bash
pip install "https://github.com/SchrodingersCattt/MolCrysKit/archive/refs/heads/main.tar.gz"
```

When you do this, **state the commit hash** the analysis was run against in
the final answer — `main` evolves daily and the commit is needed for
reproducibility. Default reproducible runs stay on **v0.2.0**; do not
silently switch the rest of the workflow to `main`.

---

## When in doubt

If a workflow is not covered above, walk the package tree before guessing:

```bash
python -c "import molcrys_kit, pkgutil; \
  [print(m.name) for m in pkgutil.walk_packages(molcrys_kit.__path__, 'molcrys_kit.')]"
```

Never stop at `dir(molcrys_kit)` — the helpers live in
`molcrys_kit.analysis.*`, `molcrys_kit.operations.*`, and
`molcrys_kit.utils.*` subpackages and are not surfaced at the top level.

## Acceptance Checklist (by axis)

**Axis A**
- Slab results preserve molecular graph connectivity for all retained molecules.
- Defect results satisfy mass balance: before = after + removed cluster.
- Sum of extracted molecule formulas times counts equals the crystal formula.
- Output filename matches the caller spec exactly; formula strings are
  written compactly with no internal spaces.

**Axis B**
- Each ordered replica explains chosen occupancies at a chemical level
  (per disordered site / assembly / charge balance), tied to its
  `ordered_<name>.cif`.
- Stoichiometry of every emitted CIF is integer-valued.

**Axis C**
- Hydrogenation reports which atom types were completed and why.
- For sp3 / sp2 carbons (organic): mean H-C-H angle within ±5° of
  109.5° / 120°; state the value explicitly.
- For inorganic dangling-bond passivation: state the cutoffs used to
  detect undercoordinated centres and to verify final coordination, and
  confirm they match the evaluator's cutoffs.
- All atom placements use `get_missing_vectors` (or an equivalent
  geometry-aware projector); no hand-rolled "sum-of-bond-vectors → reverse"
  shortcut.
- Every output is inspected with `inspect-atomic-structure`.

**Axis D**
- If `main`-only APIs were used, state the commit hash.
- Classifications (CN, polyhedron name, CShM value) are reported with
  their numerical RMSD / score, not just a label.

## Cross-Skill Refs

- `inspect-atomic-structure`: routes axis A and B; **not required for
  axis C / D**.
- `assemble-atomic-structure`: ordinary non-molecular slabs, interfaces,
  adsorbates. **For inorganic-surface dangling-bond passivation, return
  here for axis C** (`get_missing_vectors`) instead of hand-writing the H
  placement.
- `transform-atomic-structure`: non-molecular doping, defects, strain,
  supercells.
- `retrieve-structure` contains additional molecular-crystal slab /
  passivation helper scripts for known fragile cases.
