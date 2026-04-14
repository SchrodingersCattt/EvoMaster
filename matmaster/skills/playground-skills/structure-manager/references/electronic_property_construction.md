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

> **Task-specified k-labels**: When a task names high-symmetry points (e.g. M-Gamma-K), resolve coordinates from the **label's native BZ** (hexagonal: M=(½,0,0), K=(⅓,⅓,0) in fractional reciprocal coords). Do not relabel a different BZ's points.

> **Post-processing without DFT data**: Use literature band parameters (gaps, effective masses) with numpy to generate synthetic dispersion. Do not implement tight-binding from scratch—this risks timeout.

## Supercell for Defect Electronic Structure
- Point defects: supercell >= 10 A between defect and nearest image. Typical: 3x3x3 bulk, 2x2 or 3x3 in-plane for slabs.
- Use G-point or sparse k-mesh for large supercells.
- Charged defects: compensating background charge.

## Heterostructure for Band Alignment

### Workflow (execute ALL steps — partial completion is the #1 failure mode)
1. **Build each slab separately** using `build_surface_slab` (MCP) or `build_slab_tasker_fix.py`. Save each slab as a separate CIF/POSCAR file **immediately**.
2. **Lattice matching**: compute in-plane lattice parameters of both slabs. If mismatch > 5%, create commensurate supercells (e.g., 2×2 of material A with 3×3 of material B). Use `make_supercell_structure` (MCP) or inline pymatgen `Structure.make_supercell()`.
3. **Stack**: use `build_surface_interface` (MCP) with `interface_distance` 2.0–3.0 Å (vdW) or 1.5–2.5 Å (covalent), `max_strain` 0.05–0.2. If MCP fails, use ASE: read both slabs, extend c-axis, combine atoms.
4. **Verify with `get_structure_info`**: atom count = sum of both slabs, no overlaps (min dist > 0.5 Å), vacuum ≥ 15 Å.
5. **Save-early rule**: save each intermediate (slab A, slab B, interface) as separate files before moving to the next step. If timeout threatens, you get partial credit for delivered files.

### Common pitfalls
- Spending too many turns on lattice matching — use the simplest supercell that brings mismatch below 5%.
- Not saving intermediate files — if the interface build fails, at least the individual slabs exist as deliverables.
- Forgetting to verify atom count after stacking (most common error).

## Verification Checklist
1. `assess_structure.py` — dimensionality matches intent (bulk=3D, slab=2D)
2. Atom count = formula x supercell size
3. Slabs: vacuum >= 15 A (20 A for work function), kpoints=1 in vacuum direction
4. Primitive cells: confirm actually primitive (not conventional with doubled atoms)
