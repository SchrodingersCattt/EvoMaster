---
name: transform-atomic-structure
description: "Transform existing non-molecular crystal structures: supercell, strain, doping, vacancy/defect, ordering, or in-place mutation. Not for molecular crystals (bond-breaking risk) or multi-structure assembly (slab+adsorbate, interface, packing)."
---

# Transform Atomic Structure

Use this skill for operations that start from one structure and produce a
modified version of the same object. It handles ordinary inorganic, metallic,
ionic, and covalent crystals.

## Capability Gate

- **STOP** if the input is a molecular crystal (e.g. MOF with organic linkers,
  pharmaceutical polymorph, polymer crystal). This skill operates at the atomic
  level — individual atom removal or substitution would break molecules.
- **STOP** if the task requires combining multiple structures (slab+adsorbate,
  interface, amorphous packing). This skill only transforms a single structure
  in-place.

## Decision Tree

1. Inspect the input structure:
   ```python
   from pymatgen.core import Structure
   from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
   struct = Structure.from_file("input.cif")
   print(f"Formula: {struct.formula}, Atoms: {len(struct)}")
   sga = SpacegroupAnalyzer(struct, symprec=0.1)
   print(f"Space group: {sga.get_space_group_symbol()} ({sga.get_space_group_number()})")
   print(f"Ordered: {struct.is_ordered}")
   ```
2. If the user requests only cell multiplication, use a supercell matrix.
3. If the user requests strain/shear, apply a deformation gradient and decide
   whether atom coordinates should scale with the lattice.
4. If the user requests dopants, select sites by mode:
   - `random`: reproducible random indices with a fixed seed.
   - `ordered`: choose symmetry-related sites or ordered sublattices.
   - `wyckoff`: choose sites from explicit Wyckoff labels.
5. If dopant valence differs from host, apply charge compensation **before**
   writing the output:

   | Substitution | Compensation | Example |
   |-------------|--------------|---------|
   | Higher-valence cation (e.g. Al³⁺→Mg²⁺) | Remove cation(s): 1 vacancy per 2 extra charges | 2 Al³⁺ in MgO → remove 1 Mg²⁺ |
   | Lower-valence cation (e.g. Li⁺→Mg²⁺) | Remove anion(s) or add interstitial | 2 Li⁺ in MgO → remove 1 O²⁻ |
   | Same valence (e.g. Ni²⁺→Mg²⁺) | None needed | Direct substitution |

   If the user does not specify a strategy, default to cation/anion vacancy.
   Never output an aliovalent-doped structure without neutralizing the charge.
   **Priority**: charge neutrality > target concentration. In small supercells
   the exact requested at.% is often unachievable; round the dopant count to
   the nearest charge-neutral integer set, even if the resulting concentration
   deviates from the request.

6. For doping/defect tasks, run acceptance checklist → `references/doping_checklist.md`

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

Aliovalent doping with vacancy compensation (e.g. Al³⁺ in MgO):

```python
from pymatgen.core import Structure
import numpy as np

struct = Structure.from_file("supercell.cif")
# 1. Substitute: replace 2 Mg with Al
mg_indices = [i for i, s in enumerate(struct) if s.species_string == "Mg"]
rng = np.random.default_rng(seed=42)
sub_indices = rng.choice(mg_indices, size=2, replace=False).tolist()
for idx in sub_indices:
    struct[idx] = "Al"
# 2. Charge compensation: remove 1 Mg vacancy (2×Al³⁺ - 2×Mg²⁺ = +2 → need -2 → remove 1 Mg²⁺)
remaining_mg = [i for i, s in enumerate(struct) if s.species_string == "Mg"]
vac_idx = rng.choice(remaining_mg, size=1).tolist()
struct.remove_sites(vac_idx)
# 3. Verify neutrality
charge = sum({"Mg": 2, "Al": 3, "O": -2}[s.species_string] for s in struct)
assert charge == 0, f"Charge imbalance: {charge}"
struct.to(filename="doped.cif")
```

For simple same-valence doping or defects, inline `pymatgen` site selection is
sufficient. For ordered Wyckoff targeting or multiple doping rules, use
`SpacegroupAnalyzer` plus explicit Wyckoff filtering.

## Hard Guards

- Output filename and extension MUST exactly match the caller's specification.
  Never substitute conventional aliases (e.g. do not rename `gamma_alumina.cif`
  to `gamma_al2o3.cif`). Evaluators check the exact string.
- Always preserve the input file and write a new output file.
- Supercell mode requires an integer 3x3 matrix or three integer repeats.
- Deformation mode must state whether atomic coordinates are scaled with the
  cell. Default to scaling atoms for physical strain.
- `fraction` and `count` are mutually exclusive for any doping rule.
- Do not silently replace zero atoms. If `fraction * site_count < 1`, require a
  larger supercell or an exact count.
- Use a fixed seed for stochastic replacements and report it.

## Acceptance Checklist

- Input and output filenames are both reported.
- Structure validated before and after (dimensionality, formula, min distance).
- Formula, atom count, and lattice change match the requested transformation.
- For stochastic operations, the seed and selected site summary are reported.

