# Structure Construction for Electronic Property Calculations

When building structures **specifically for electronic property calculations** (band structure, DOS, charge density, work function):

## Primitive vs Conventional Cell
- **Band structure**: use **primitive cell** (standard high-symmetry k-path; conventional cells have folded bands). Convert with pymatgen `SpacegroupAnalyzer.get_primitive_standard_structure()`.
- **DOS**: either primitive or supercell; use **dense uniform k-mesh** (not line-mode).
- **Work function**: use **slab** with vacuum >= 20 A + dipole correction.

## K-path by Crystal System
| Crystal system | Key points | Example path |
|---------------|-----------|-------------|
| FCC (cubic) | G, X, W, K, L | G->X->W->K->G->L |
| BCC (cubic) | G, H, N, P | G->H->N->G->P->H |
| Hexagonal | G, M, K, A | G->M->K->G (2D: skip A) |
| Tetragonal | G, X, M, Z | G->X->M->G->Z |
| Simple cubic | G, X, M, R | G->X->M->G->R->X |

Auto-generate with pymatgen `HighSymmKpath(structure)`.

## Supercell for Defect Electronic Structure
- Point defects: supercell >= 10 A between defect and nearest image. Typical: 3x3x3 bulk, 2x2 or 3x3 in-plane for slabs.
- Use G-point or sparse k-mesh for large supercells.
- Charged defects: compensating background charge.

## Heterostructure for Band Alignment
- Build each slab separately (`build_surface_slab` or `build_slab_tasker_fix.py`).
- Match in-plane lattice (strain < 5%) via supercell matching.
- Stack with `build_surface_interface` (MCP) or manual ASE stacking.
- Verify: no overlaps, correct count, sufficient vacuum.

## Verification Checklist
1. `assess_structure.py` — dimensionality matches intent (bulk=3D, slab=2D)
2. Atom count = formula x supercell size
3. Slabs: vacuum >= 15 A (20 A for work function), kpoints=1 in vacuum direction
4. Primitive cells: confirm actually primitive (not conventional with doubled atoms)
