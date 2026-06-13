# Molecular Crystal Calculations

Sublimation energy and related properties for molecular crystals using DPA MLIPs.

## Head Selection

Molecular crystals (organic, drug-like molecules) require a molecular-crystal-trained head:

| Model | Head | Use case |
|-------|------|----------|
| DPA3.2-5M | OMol25 | Organic molecular crystals |
| DPA3.2-5M | Domains_Drug | Drug-like molecules |

**Do NOT use Omat24** (inorganic default) — it lacks training on intermolecular van der Waals interactions and gives wrong cohesive energies.

## Sublimation Energy Workflow

```
E_sub = E_gas - E_crystal/Z    (positive = crystal more stable)
```

1. Obtain/build molecular crystal unit cell
2. Optimize crystal (cell + positions) with organic head
3. Extract single molecule from crystal → `molecule.xyz`
4. Place molecule in large periodic box, optimize (gas phase reference)
5. Compute E_sub

## Molecule Extraction from Crystal

Molecules in molecular crystals often **span periodic boundaries**. Naive slicing by atom index produces fragments with wrong composition.

Correct approach:
1. Build connectivity graph (bond cutoffs: C-C < 1.7Å, C-H < 1.3Å, C-O < 1.6Å, etc.)
2. Use MIC distances for neighbor detection (not direct Cartesian distances)
3. Pick one connected component
4. Unwrap atomic positions: once you have the molecule's atom indices, reconstruct contiguous Cartesian coords using MIC vectors from a seed atom
5. Verify formula matches expected (e.g., C10H8 for naphthalene = 18 atoms)

## Gas-Phase Reference — CRITICAL

DPA models have **different energy references for periodic vs non-periodic systems**. Always use `pbc=True` with a large vacuum box for the gas-phase molecule:

```python
mol.center(vacuum=15.0)
mol.set_pbc(True)  # CRITICAL: must match crystal's periodic treatment
```

Using `pbc=False` gives energy offsets of 10+ eV — this is NOT a head problem.

## Troubleshooting Large Sublimation Energy

If E_sub > 5 eV/molecule (experimental range is typically 0.5–2 eV):

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| E_sub = 10-15 eV | pbc=False for molecule | Set `pbc=True` + large box |
| E_sub = 10-15 eV with pbc=True | Extracted molecule has wrong composition | Check formula, redo extraction with PBC unwrap |
| Molecule doesn't converge | Bad initial geometry from extraction | Verify connectivity, use tighter fmax with FIRE |

**Do NOT switch heads when you see large E_sub.** The issue is almost always in the molecule extraction or pbc setup, not the head choice.
