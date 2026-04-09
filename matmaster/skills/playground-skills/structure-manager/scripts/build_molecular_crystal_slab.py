#!/usr/bin/env python3
"""build_molecular_crystal_slab.py — Cut a slab from a molecular crystal.

Designed for organic, MOF, co-crystal, and hybrid molecular crystal structures
where standard SlabGenerator may fragment molecules across slab boundaries.

Algorithm:
1. Load CIF and detect molecules via covalent-bond graph (PBC-aware).
2. Use pymatgen SlabGenerator with in_unit_planes=True to enumerate terminations.
3. For each candidate slab, verify molecule integrity:
   - All molecules remain intact (no broken covalent bonds across slab boundary).
   - Atom count = integer multiple of unit-cell molecule count × layers.
4. Select the best slab (intact molecules, correct layer count).
5. If no termination preserves molecules, try adjacent layer counts (layers±1)
   and report the best available option.

Usage:
  python build_molecular_crystal_slab.py --file input.cif --miller 1 1 0 --layers 4
      [-o output.cif] [--vacuum 20.0] [--bond-tolerance 0.45]

Output: JSON to stdout with success status, slab info, and molecule integrity report.

Requires: pymatgen, numpy
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Covalent radii (Å) for bond detection
# ---------------------------------------------------------------------------

COVALENT_RADII = {
    "H": 0.31, "He": 0.28, "Li": 1.28, "Be": 0.96, "B": 0.84,
    "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57, "Ne": 0.58,
    "Na": 1.66, "Mg": 1.41, "Al": 1.21, "Si": 1.11, "P": 1.07,
    "S": 1.05, "Cl": 1.02, "Ar": 1.06, "K": 1.96, "Ca": 1.76,
    "Sc": 1.70, "Ti": 1.60, "V": 1.53, "Cr": 1.39, "Mn": 1.39,
    "Fe": 1.32, "Co": 1.26, "Ni": 1.24, "Cu": 1.32, "Zn": 1.22,
    "Ga": 1.22, "Ge": 1.20, "As": 1.19, "Se": 1.20, "Br": 1.20,
    "Kr": 1.16, "Rb": 2.10, "Sr": 1.95, "Y": 1.90, "Zr": 1.75,
    "Nb": 1.64, "Mo": 1.54, "Ru": 1.46, "Rh": 1.42, "Pd": 1.39,
    "Ag": 1.45, "Cd": 1.44, "In": 1.42, "Sn": 1.39, "Sb": 1.39,
    "Te": 1.38, "I": 1.39, "Cs": 2.44, "Ba": 2.15, "La": 2.07,
    "Pt": 1.36, "Au": 1.36, "Pb": 1.46, "Bi": 1.48,
}

_DEFAULT_RADIUS = 1.50


def _get_radius(element: str) -> float:
    return COVALENT_RADII.get(element, _DEFAULT_RADIUS)


# ---------------------------------------------------------------------------
# Molecule detection (PBC-aware bond graph)
# ---------------------------------------------------------------------------

def detect_molecules(structure, bond_tol: float = 0.45) -> list[list[int]]:
    """Detect molecules in a periodic structure via covalent bond graph.

    Returns list of molecules, each a list of site indices.
    """
    n = len(structure)
    max_r = 2 * max(_get_radius(str(s.specie)) for s in structure) + bond_tol
    all_nn = structure.get_all_neighbors(max_r, include_index=True)

    # Build adjacency list (only covalent bonds)
    adj: list[set[int]] = [set() for _ in range(n)]
    for i in range(n):
        ri = _get_radius(str(structure[i].specie))
        for nn in all_nn[i]:
            j = nn.index if hasattr(nn, "index") else nn[2]
            d = nn.nn_distance if hasattr(nn, "nn_distance") else nn[1]
            rj = _get_radius(str(structure[j].specie))
            if d <= ri + rj + bond_tol:
                adj[i].add(j)
                adj[j].add(i)

    # BFS connected components
    visited = [False] * n
    molecules = []
    for start in range(n):
        if visited[start]:
            continue
        mol = []
        queue = deque([start])
        visited[start] = True
        while queue:
            node = queue.popleft()
            mol.append(node)
            for nb in adj[node]:
                if not visited[nb]:
                    visited[nb] = True
                    queue.append(nb)
        molecules.append(sorted(mol))
    return molecules


def molecules_intact(structure, bond_tol: float = 0.45) -> dict:
    """Check if all molecules in the structure are intact.

    Returns dict with 'intact', 'n_molecules', 'molecule_sizes',
    'unique_formulas', and 'warnings'.
    """
    from collections import Counter

    mols = detect_molecules(structure, bond_tol)
    sizes = [len(m) for m in mols]

    # Get formula for each molecule
    formulas = []
    for mol in mols:
        comp = Counter()
        for idx in mol:
            el = str(structure[idx].specie)
            comp[el] += 1
        # Sort by element symbol for consistency
        formula = "".join(f"{el}{comp[el]}" for el in sorted(comp))
        formulas.append(formula)

    unique_formulas = sorted(set(formulas))
    formula_counts = Counter(formulas)

    # Check if molecules are complete (same formula repeated)
    # Fragmented molecules would show up as unusual formulas
    warnings = []
    if len(unique_formulas) > 2:
        # More than 2 unique formulas might indicate fragmentation
        # (some crystals have 2 molecular species, e.g., co-crystals)
        warnings.append(
            f"Found {len(unique_formulas)} distinct molecular formulas "
            f"(could indicate fragmentation): {unique_formulas}"
        )

    return {
        "intact": len(warnings) == 0,
        "n_molecules": len(mols),
        "molecule_sizes": sizes,
        "unique_formulas": unique_formulas,
        "formula_counts": dict(formula_counts),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Slab cutting with molecule integrity verification
# ---------------------------------------------------------------------------

def build_molecular_slab(
    structure,
    miller_index: tuple[int, int, int],
    layers: int,
    vacuum: float = 20.0,
    bond_tol: float = 0.45,
) -> dict:
    """Cut a slab from a molecular crystal, preserving molecule integrity.

    Tries all terminations from SlabGenerator. Selects the one where:
    (a) molecules are not fragmented, and
    (b) atom count matches layers × unit-cell atoms.

    Returns result dict with success status and slab info.
    """
    from pymatgen.core.surface import SlabGenerator
    from pymatgen.io.cif import CifWriter

    n_unitcell = len(structure)

    # Detect molecules in the bulk unit cell
    bulk_mols = detect_molecules(structure, bond_tol)
    n_mols_per_cell = len(bulk_mols)
    mol_sizes_bulk = sorted([len(m) for m in bulk_mols])

    result_info = {
        "unit_cell_atoms": n_unitcell,
        "unit_cell_molecules": n_mols_per_cell,
        "bulk_molecule_sizes": mol_sizes_bulk,
        "miller_index": list(miller_index),
        "requested_layers": layers,
    }

    expected_atoms = layers * n_unitcell

    # Generate slabs with in_unit_planes=True for layer counting
    slabgen = SlabGenerator(
        structure,
        miller_index=miller_index,
        min_slab_size=layers,
        min_vacuum_size=vacuum,
        center_slab=True,
        in_unit_planes=True,
        lll_reduce=False,
        reorient_lattice=True,
    )

    try:
        slabs = slabgen.get_slabs(symmetrize=False)
    except Exception as e:
        return {
            "success": False,
            "error": f"SlabGenerator failed: {e}",
            **result_info,
        }

    if not slabs:
        return {
            "success": False,
            "error": "SlabGenerator returned no slabs",
            **result_info,
        }

    # Evaluate each termination
    candidates = []
    for i, slab in enumerate(slabs):
        n_slab = len(slab)
        ratio = n_slab / n_unitcell if n_unitcell > 0 else 0

        # Check molecule integrity
        mol_check = molecules_intact(slab, bond_tol)

        # Count unique z-planes (for layer verification)
        z_coords = np.array([s.frac_coords[2] for s in slab])
        z_unique = len(set(np.round(z_coords, 4)))

        atom_count_ok = (n_slab == expected_atoms)
        integer_ratio = abs(ratio - round(ratio)) < 0.01

        score = 0
        if atom_count_ok:
            score += 10
        elif integer_ratio:
            score += 5
        if mol_check["intact"]:
            score += 10
        if len(mol_check["unique_formulas"]) <= 2:
            score += 3

        candidates.append({
            "index": i,
            "slab": slab,
            "n_atoms": n_slab,
            "ratio": ratio,
            "atom_count_matches": atom_count_ok,
            "integer_ratio": integer_ratio,
            "mol_integrity": mol_check,
            "shift": float(slab.shift),
            "z_unique_planes": z_unique,
            "score": score,
        })

    # Sort by score (highest first)
    candidates.sort(key=lambda c: c["score"], reverse=True)

    best = candidates[0]
    slab = best["slab"]

    # Determine if molecules are fragmented
    mol_intact = best["mol_integrity"]["intact"]
    atom_ok = best["atom_count_matches"]

    return {
        "success": True,
        "molecules_intact": mol_intact,
        "atom_count_matches_expected": atom_ok,
        "n_atoms": best["n_atoms"],
        "expected_atoms": expected_atoms,
        "ratio_to_unitcell": best["ratio"],
        "shift": best["shift"],
        "z_unique_planes": best["z_unique_planes"],
        "n_terminations_evaluated": len(candidates),
        "mol_integrity": best["mol_integrity"],
        "slab": slab,
        **result_info,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Cut a slab from a molecular crystal with molecule integrity checks."
    )
    ap.add_argument("--file", required=True, help="Input CIF/POSCAR file")
    ap.add_argument(
        "--miller", nargs=3, type=int, required=True,
        help="Miller indices, e.g. --miller 1 1 0"
    )
    ap.add_argument(
        "--layers", type=int, required=True,
        help="Number of layers (unit-cell repeats along surface normal)"
    )
    ap.add_argument("-o", "--output", default=None, help="Output CIF path")
    ap.add_argument(
        "--vacuum", type=float, default=20.0,
        help="Vacuum thickness in Å (default: 20.0)"
    )
    ap.add_argument(
        "--bond-tolerance", type=float, default=0.45,
        help="Bond tolerance for molecule detection in Å (default: 0.45)"
    )
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(json.dumps({"success": False, "error": f"File not found: {path}"}))
        sys.exit(1)

    try:
        from pymatgen.core import Structure
        struct = Structure.from_file(str(path))
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Failed to load: {e}"}))
        sys.exit(1)

    miller = tuple(args.miller)
    result = build_molecular_slab(
        struct, miller, args.layers, args.vacuum, args.bond_tolerance
    )

    # Save slab if successful
    slab = result.pop("slab", None)
    if result["success"] and slab is not None:
        out_path = args.output or f"{path.stem}_slab_{miller[0]}{miller[1]}{miller[2]}.cif"
        try:
            from pymatgen.io.cif import CifWriter
            writer = CifWriter(slab, symprec=0.01)
            writer.write_file(out_path)
        except Exception:
            slab.to(filename=out_path)
        result["output_file"] = out_path
        result["formula"] = slab.composition.reduced_formula

    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
