"""
Assess a structure file: dimensionality (0D–3D), sanity (overlaps), formula.

Usage:
  python assess_structure.py --file structure.cif

Output: JSON to stdout with is_valid, dimensionality, formula, warnings.
Logic:
  - Bulk vs Slab: vacuum gap > 15 Å in one direction -> Slab; in 3 directions -> Molecule.
  - Sanity: fail if min_dist < 0.5 Å (hard overlap cutoff, PBC-aware).

Requires: pymatgen, numpy
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# Covalent radii (Å) - common elements
COVALENT_RADII = {
    "H": 0.31,
    "He": 0.28,
    "Li": 1.28,
    "Be": 0.96,
    "B": 0.84,
    "C": 0.76,
    "N": 0.71,
    "O": 0.66,
    "F": 0.57,
    "Ne": 0.58,
    "Na": 1.66,
    "Mg": 1.41,
    "Al": 1.21,
    "Si": 1.11,
    "P": 1.07,
    "S": 1.05,
    "Cl": 1.02,
    "Ar": 1.06,
    "K": 1.96,
    "Ca": 1.76,
    "Sc": 1.70,
    "Ti": 1.60,
    "V": 1.53,
    "Cr": 1.39,
    "Mn": 1.39,
    "Fe": 1.32,
    "Co": 1.26,
    "Ni": 1.24,
    "Cu": 1.32,
    "Zn": 1.22,
    "Ga": 1.22,
    "Ge": 1.20,
    "As": 1.19,
    "Se": 1.20,
    "Br": 1.20,
    "Kr": 1.16,
    "Rb": 2.10,
    "Sr": 1.95,
    "Y": 1.90,
    "Zr": 1.75,
    "Nb": 1.64,
    "Mo": 1.54,
    "Ag": 1.45,
    "Cd": 1.44,
    "In": 1.42,
    "Sn": 1.39,
    "Sb": 1.39,
    "Te": 1.38,
    "I": 1.39,
    "Au": 1.36,
    "Pb": 1.46,
    "Bi": 1.48,
}

_MAX_RADIUS = max(COVALENT_RADII.values())


def _load_structure(filepath: Path):
    """Load structure with pymatgen. Returns (obj, kind) where kind is
    'periodic' (Structure) or 'molecule' (Molecule)."""
    from pymatgen.core import Molecule, Structure

    suffix = filepath.suffix.lower()
    if suffix == ".xyz":
        return Molecule.from_file(str(filepath)), "molecule"
    return Structure.from_file(str(filepath)), "periodic"


def _dimensionality(struct) -> tuple[str, list[str]]:
    """Infer dimensionality from vacuum gaps in each lattice direction."""
    warnings: list[str] = []
    abc = np.array(struct.lattice.abc)
    frac = np.array([s.frac_coords for s in struct])
    if len(frac) == 0:
        return "Unknown", ["No sites"]
    span_frac = np.clip(np.ptp(frac, axis=0), 0.01, 1.0)
    vacuum = abc - span_frac * abc
    vac_threshold = 15.0
    n_vac = int(np.sum(vacuum > vac_threshold))
    if n_vac >= 3:
        return "0D-Molecule", warnings
    if n_vac == 2:
        return "1D-Wire", warnings
    if n_vac == 1:
        if vacuum[vacuum > vac_threshold].min() < 10:
            warnings.append("Vacuum padding < 10A")
        return "2D-Slab", warnings
    return "3D-Bulk", warnings


def _sanity_periodic(struct) -> tuple[bool, list[str]]:
    """PBC-aware overlap check using pymatgen get_all_neighbors."""
    cutoff = 2.0 * _MAX_RADIUS
    all_neighbors = struct.get_all_neighbors(cutoff, include_index=True)
    min_dist = float("inf")
    for i, neighbors in enumerate(all_neighbors):
        for nb in neighbors:
            d = nb.nn_distance if hasattr(nb, "nn_distance") else nb[1]
            j = nb.index if hasattr(nb, "index") else nb[2]
            if j > i and d < min_dist:
                min_dist = d
    if min_dist < 0.5:
        return False, [f"Overlapping atoms (min_dist={min_dist:.3f} A < 0.5 A)"]
    return True, []


def _sanity_molecule(mol) -> tuple[bool, list[str]]:
    """Overlap check for molecules (no PBC), O(n²) via numpy broadcasting."""
    coords = np.array([s.coords for s in mol])
    n = len(coords)
    if n < 2:
        return True, []
    diff = coords[:, None, :] - coords[None, :, :]
    dists = np.sqrt(np.sum(diff**2, axis=-1))
    np.fill_diagonal(dists, np.inf)
    min_dist = dists.min()
    if min_dist < 0.5:
        return False, [f"Overlapping atoms (min_dist={min_dist:.3f} A < 0.5 A)"]
    return True, []


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Assess structure: dimensionality, sanity, formula."
    )
    ap.add_argument("--file", required=True, help="Path to CIF, POSCAR, XYZ, etc.")
    args = ap.parse_args()
    path = Path(args.file)

    if not path.exists():
        print(
            json.dumps(
                {
                    "is_valid": False,
                    "dimensionality": "Unknown",
                    "formula": "",
                    "warnings": [f"File not found: {path}"],
                }
            )
        )
        sys.exit(1)

    try:
        obj, kind = _load_structure(path)
    except Exception as e:
        print(
            json.dumps(
                {
                    "is_valid": False,
                    "dimensionality": "Unknown",
                    "formula": "",
                    "warnings": [f"Failed to load structure: {e}"],
                }
            )
        )
        sys.exit(1)

    if kind == "molecule":
        formula = obj.composition.reduced_formula
        dim, dim_warns = "0D-Molecule", []
        sane, sane_warns = _sanity_molecule(obj)
    else:
        formula = obj.formula
        dim, dim_warns = _dimensionality(obj)
        sane, sane_warns = _sanity_periodic(obj)

    print(
        json.dumps(
            {
                "is_valid": sane,
                "dimensionality": dim,
                "formula": formula,
                "warnings": dim_warns + sane_warns,
            }
        )
    )


if __name__ == "__main__":
    main()
