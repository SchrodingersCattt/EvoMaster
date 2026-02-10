"""
Assess a structure file: dimensionality (0D–3D), sanity (overlaps, bond lengths), formula.

Usage:
  python assess_structure.py --file structure.cif

Output: JSON to stdout with is_valid, dimensionality, formula, warnings.
Logic:
  - Bulk vs Slab: vacuum gap > 15 Å in one direction -> Slab; in 3 directions -> Molecule.
  - Sanity: fail if min_dist < 0.7 * sum of covalent radii (overlap).
"""

import argparse
import json
import sys
from pathlib import Path

# Covalent radii (Å) - common elements
COVALENT_RADII = {
    "H": 0.31, "He": 0.28, "Li": 1.28, "Be": 0.96, "B": 0.84, "C": 0.76, "N": 0.71,
    "O": 0.66, "F": 0.57, "Ne": 0.58, "Na": 1.66, "Mg": 1.41, "Al": 1.21, "Si": 1.11,
    "P": 1.07, "S": 1.05, "Cl": 1.02, "Ar": 1.06, "K": 1.96, "Ca": 1.76, "Sc": 1.70,
    "Ti": 1.60, "V": 1.53, "Cr": 1.39, "Mn": 1.39, "Fe": 1.32, "Co": 1.26, "Ni": 1.24,
    "Cu": 1.32, "Zn": 1.22, "Ga": 1.22, "Ge": 1.20, "As": 1.19, "Se": 1.20, "Br": 1.20,
    "Kr": 1.16, "Rb": 2.10, "Sr": 1.95, "Y": 1.90, "Zr": 1.75, "Nb": 1.64, "Mo": 1.54,
    "Ag": 1.45, "Cd": 1.44, "In": 1.42, "Sn": 1.39, "Sb": 1.39, "Te": 1.38, "I": 1.39,
    "Au": 1.36, "Pb": 1.46, "Bi": 1.48,
}


def _get_radius(symbol: str) -> float:
    return COVALENT_RADII.get(symbol, 1.5)


def _load_structure(filepath: Path):
    """Load with pymatgen or ase.

    For .xyz files (molecules without a lattice), try pymatgen Molecule first,
    then fall back to ASE.  For periodic structures (.cif, POSCAR, etc.) use
    pymatgen Structure.
    """
    suffix = filepath.suffix.lower()

    # --- .xyz / molecular formats: use Molecule (no lattice) ---
    if suffix in (".xyz",):
        try:
            from pymatgen.core import Molecule
            mol = Molecule.from_file(str(filepath))
            return mol, "pymatgen_molecule"
        except Exception:
            pass
        try:
            from ase.io import read
            return read(str(filepath)), "ase"
        except Exception:
            pass
        return None, None

    # --- periodic structures ---
    try:
        from pymatgen.core import Structure
        return Structure.from_file(str(filepath)), "pymatgen"
    except (ImportError, ValueError):
        pass
    try:
        from ase.io import read
        return read(str(filepath)), "ase"
    except (ImportError, Exception):
        pass
    return None, None


def _formula_from_sites(sites) -> str:
    """Simple formula from list of (symbol, ...) or structure."""
    from collections import Counter
    if hasattr(sites, "species"):  # pymatgen
        counter = Counter()
        for site in sites:
            for sp, occ in site.species.items():
                counter[sp.symbol] += occ
    else:
        counter = Counter(s[0] if isinstance(s, (list, tuple)) else getattr(s, "symbol", str(s)) for s in sites)
    parts = [f"{sym}{n}" if n > 1 else sym for sym, n in sorted(counter.items())]
    return "".join(parts)


def _dimensionality_pymatgen(struct) -> tuple[str, list]:
    """Count vacuum directions: gap > 15 Å -> that dimension is 'vacuum'. 0 vac=3D Bulk, 1=2D Slab, 2=1D Wire, 3=0D Molecule."""
    warnings = []
    lattice = struct.lattice
    abc = lattice.abc
    # Approximate atom span per direction (simplified: use cell and density of sites)
    import numpy as np
    frac = np.array([s.frac_coords for s in struct])
    if len(frac) == 0:
        return "Unknown", ["No sites"]
    # Span in fractional coords ~ [0,1] per axis; convert to Cartesian span
    span_frac = np.ptp(frac, axis=0)
    # Avoid 0 span
    span_frac = np.clip(span_frac, 0.01, 1.0)
    span_cart = span_frac * np.array(abc)
    vacuum = np.array(abc) - span_cart
    vac_threshold = 15.0
    n_vac = int(np.sum(vacuum > vac_threshold))
    if n_vac >= 3:
        dim = "0D-Molecule"
    elif n_vac == 2:
        dim = "1D-Wire"
    elif n_vac == 1:
        dim = "2D-Slab"
        if vacuum[vacuum > vac_threshold].min() < 10:
            warnings.append("Vacuum padding < 10A")
    else:
        dim = "3D-Bulk"
    return dim, warnings


def _dimensionality_ase(atoms) -> tuple[str, list]:
    """Same logic using ASE atoms."""
    import numpy as np
    cell = atoms.get_cell()
    if cell.rank < 3:
        return "Unknown", ["Invalid cell"]
    abc = np.sqrt(np.sum(cell**2, axis=1))
    pos = atoms.get_positions()
    if len(pos) == 0:
        return "Unknown", ["No atoms"]
    frac = np.linalg.solve(cell.T, pos.T).T
    span_frac = np.ptp(frac, axis=0)
    span_frac = np.clip(span_frac, 0.01, 1.0)
    span_cart = span_frac * abc
    vacuum = abc - span_cart
    vac_threshold = 15.0
    n_vac = int(np.sum(vacuum > vac_threshold))
    warnings = []
    if n_vac >= 3:
        dim = "0D-Molecule"
    elif n_vac == 2:
        dim = "1D-Wire"
    elif n_vac == 1:
        dim = "2D-Slab"
        if np.any(vacuum > vac_threshold) and vacuum[vacuum > vac_threshold].min() < 10:
            warnings.append("Vacuum padding < 10A")
    else:
        dim = "3D-Bulk"
    return dim, warnings


def _min_distance_and_sanity(struct_or_atoms, backend: str) -> tuple[bool, list]:
    """Check min pairwise distance >= 0.7 * (r_i + r_j). Return (is_valid, warnings)."""
    import numpy as np
    warnings = []
    if backend == "pymatgen":
        struct = struct_or_atoms
        symbols = [str(s.specie) for s in struct]
        n = len(struct)
    else:
        atoms = struct_or_atoms
        symbols = atoms.get_chemical_symbols()
        cell = atoms.get_cell()
        coords = atoms.get_positions()
        n = len(coords)

    min_dist = float("inf")
    pair = (0, 0)
    for i in range(n):
        for j in range(i + 1, n):
            if backend == "pymatgen":
                d = struct_or_atoms.get_distance(i, j)
            else:
                d = _pair_dist_pbc(coords[i], coords[j], cell)
            if d < min_dist:
                min_dist = d
                pair = (i, j)
    r_i = _get_radius(symbols[pair[0]])
    r_j = _get_radius(symbols[pair[1]])
    threshold = 0.7 * (r_i + r_j)
    if min_dist < 0.5:
        return False, [f"Overlapping atoms (min_dist={min_dist:.3f} A < 0.5 A)"]
    if min_dist < threshold:
        warnings.append(f"Short contact {min_dist:.3f} A < 0.7*(r_i+r_j)={threshold:.3f} A")
        return False, warnings
    return True, warnings


def _ase_distances(cell, coords):
    """Minimal all-pairs distance for ASE (no full matrix needed for sanity)."""
    import numpy as np
    n = len(coords)
    d = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d[i, j] = _pair_dist_pbc(coords[i], coords[j], cell)
    return d


def _pair_dist_pbc(p, q, cell):
    import numpy as np
    d = p - q
    # simple min image
    frac = np.linalg.solve(cell.T, d)
    frac = frac - np.round(frac)
    d = frac @ cell
    return np.linalg.norm(d)


def main() -> None:
    ap = argparse.ArgumentParser(description="Assess structure: dimensionality, sanity, formula.")
    ap.add_argument("--file", required=True, help="Path to CIF, POSCAR, or XYZ")
    args = ap.parse_args()
    path = Path(args.file)
    if not path.exists():
        out = {"is_valid": False, "dimensionality": "Unknown", "formula": "", "warnings": [f"File not found: {path}"]}
        print(json.dumps(out))
        sys.exit(1)
    obj, backend = _load_structure(path)
    if obj is None:
        out = {"is_valid": False, "dimensionality": "Unknown", "formula": "", "warnings": ["pymatgen or ase required to read structure"]}
        print(json.dumps(out))
        sys.exit(1)
    if backend == "pymatgen_molecule":
        # Molecule (no lattice) — dimensionality is 0D by definition
        formula = obj.composition.reduced_formula
        dim = "0D Molecule"
        dim_warns = []
        # Simple sanity: check pairwise distances (no PBC)
        import numpy as np
        coords = np.array([s.coords for s in obj])
        symbols = [s.specie.symbol for s in obj]
        sane_warns = []
        sane = True
        n = len(coords)
        for i in range(n):
            for j in range(i + 1, n):
                d = np.linalg.norm(coords[i] - coords[j])
                threshold = 0.7 * (_get_radius(symbols[i]) + _get_radius(symbols[j]))
                if d < threshold:
                    sane_warns.append(
                        f"Short contact {symbols[i]}{i}-{symbols[j]}{j}: "
                        f"{d:.3f} A < 0.7*(r_i+r_j)={threshold:.3f} A"
                    )
                    sane = False
    elif backend == "pymatgen":
        formula = obj.formula
        dim, dim_warns = _dimensionality_pymatgen(obj)
        sane, sane_warns = _min_distance_and_sanity(obj, backend)
    else:
        formula = obj.get_chemical_formula(mode="hill") if hasattr(obj, "get_chemical_formula") else _formula_from_sites(obj)
        dim, dim_warns = _dimensionality_ase(obj)
        sane, sane_warns = _min_distance_and_sanity(obj, backend)
    warnings = dim_warns + sane_warns
    is_valid = sane
    print(json.dumps({"is_valid": is_valid, "dimensionality": dim, "formula": formula, "warnings": warnings}))


if __name__ == "__main__":
    main()
