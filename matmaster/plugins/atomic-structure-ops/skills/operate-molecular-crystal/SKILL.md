---
name: operate-molecular-crystal
description: "Use to edit a crystal with bonded fragments, including organic molecules, organic-inorganic hybrids, or polyatomic ions keeping units intact: cut surface slabs, remove guests / solvents, resolve disordered parts and convert to computation-ready models, handling bond geometries, cap dangling bonds."
---

# Operate with MolCrysKit

Use `mck` CLI for supported file-to-file operations. Do **not** write Python
MolCrysKit scripts unless the task is one of the two API exceptions below.

| Axis | Trigger | Use |
| --- | --- | --- |
| A. Topological fragments | `is_molecular_crystal=true` | `mck operate slab/desolvate/vacancy`; API only for single-molecule XYZ extraction |
| B. Disorder | occupancies < 1, alternative sites, `_chemical_formula_moiety` | `mck operate disorder` |
| C. Local geometry | missing H / atom placement by coordination geometry | `mck operate add-h`; API only for `get_missing_vectors` custom placement |
| D. Coordination analysis | CN / polyhedron / shell description | `mck analyze polyhedra --json` |

## Install / availability

MolCrysKit installs `mck` and is preinstalled in the remote calculation image.
If missing: install `molcrys-kit` and verify with `mck --help`.

## CLI replacements for former Python snippets

```bash
# inspect / whole-structure read-write
mck io info input.cif
mck io convert input.cif -o output.cif

# PBC-aware molecular-crystal slab
mck operate slab input.cif -o slab_PUBMUU03_110.cif \
   --miller 1 1 0 --layers 4 --vacuum 15 --terminations tasker_preferred

# carve finite H-capped QM cluster around Zn
mck operate cluster input.cif -o cluster.xyz \
   --seed-element Zn --max-atoms 500 --freeze-shell 1

# interpolate crystal images between two structures
mck operate interpolate start.cif end.cif -o traj.extxyz \
   --n-images 6 --method se3_screw

# remove whole solvent molecules
mck operate desolvate input.cif -o dry.cif --targets H2O --targets CH3OH

# remove whole molecule cluster vacancy
mck operate vacancy input.cif -o defect.cif \
   --species C24H40N16O2Fe2 1 --method spatial_cluster --random-seed 42

# disorder to ordered integer-occupancy replica
mck operate disorder input_disordered.cif -o ordered_input.cif \
   --method optimal --count 1

# high-level H completion
mck operate add-h input.cif -o hydrogenated.cif

# coordination/polyhedra analysis
mck analyze polyhedra input.cif --central Fe --ligand O --level atom --json
```

Notes:
- `mck io convert` writes the whole structure; it does **not** extract one molecule.
- `mck operate cluster` emits per-group files: `output__group0.xyz`, `output__group1.xyz`, etc.
- For ordinary non-molecular slabs, use `assemble-atomic-structure`; never silently
   fall back to ASE slab cutting for molecular crystals.
- Do not handcraft emitters around `scan_cif_disorder` / `DisorderInfo`; that path
   has previously produced fractional stoichiometry such as `H13.9872`.

## Python API exceptions

### 1. Single molecule XYZ extraction

Use for MLIP sublimation/gas-phase references, where exact molecule selection
and PBC unwrapping are required.

```python
from molcrys_kit.io.cif import read_mol_crystal

crystal = read_mol_crystal("crystal.cif")
crystal.molecules[0].write_xyz("molecule.xyz")
```

Each `crystal.molecules[i]` is PBC-unwrapped by the reader. Verify formula and
atom count before gas-phase calculation, e.g. naphthalene `C10H8` has 18 atoms.

### 2. Low-level geometry placement with `get_missing_vectors`

Use only when custom local atom placement is not expressible as a CLI operation.

```python
from molcrys_kit.utils.geometry import get_missing_vectors

offsets = get_missing_vectors(
      center=center_pos,
      existing_neighbors=neighbor_positions,
      geometry_type="tetrahedral",
      bond_length=1.48,
)
```

Supported `geometry_type`: `linear`, `bent`, `trigonal_planar`,
`trigonal_pyramidal`, `tetrahedral`, `planar_bisector`,
`trigonal_bipyramidal`, `octahedral`. Never hand-roll
"sum of bond vectors → reverse" projections.

## Fallbacks

For organic-only H completion, `obabel input.cif -O hydrogenated.cif -h` or RDKit
may be a last resort, but only with geometry optimisation and angle verification.
RDKit ignores PBC; unwrap molecules first.

## Guards

- Output filename and extension MUST exactly match the user's requested name.
- Slab/desolvation/vacancy operations preserve whole molecular fragments.
- Disorder outputs must have occupancy 1 and integer stoichiometry.
- Hydrogen completion must not add H to carbonyl / ester oxygens unless explicitly justified.
- Formula strings in final answers use compact form: `H288C80N48Cl48O192`, not `H288 C80 N48 Cl48 O192`.
- For inorganic dangling-bond passivation, state neighbour cutoffs used for both construction and verification.

## Acceptance checklist

- Used `mck` CLI for slab, disorder, desolvation, vacancy, hydrogen completion,
   read/write, and polyhedra analysis.
- Used Python API only for single-molecule XYZ extraction or `get_missing_vectors`.
- Exact requested output filename exists.
- Formula strings are compact and atom counts are verified.
- Disorder replicas are integer-occupancy and integer-stoichiometry.
- Every output is inspected with `mck io info` or `inspect-atomic-structure`.

## Cross-skill refs

- `inspect-atomic-structure`: use `mck io info` for molecular-crystal inspection.
- `assemble-atomic-structure`: ordinary non-molecular slabs/interfaces.
- `transform-atomic-structure`: non-molecular doping, defects, strain, supercells.
- `mlips`: molecular-crystal sublimation workflow and gas-phase periodic box.
