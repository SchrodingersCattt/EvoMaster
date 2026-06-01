"""validate_structure.py — Pre-submission structure sanity check.

Usage::

    python validate_structure.py --structure input.xyz

Checks:
- min interatomic distance > 1.0 Å (overlapping atoms)
- at least 1 atom present
- cell is defined (non-zero volume)

Exit code 0 = pass, exit code 1 = fail with diagnostic message.
"""

import argparse
import sys

import numpy as np
from ase.io import read


def main():
    parser = argparse.ArgumentParser(
        description="Validate structure before Bohrium submission"
    )
    parser.add_argument(
        "--structure",
        required=True,
        help="Path to structure file (xyz, cif, vasp, etc.)",
    )
    args = parser.parse_args()

    atoms = read(args.structure)

    if len(atoms) == 0:
        print("FAIL: structure has 0 atoms")
        sys.exit(1)

    if atoms.cell.volume < 1e-6:
        print(f"FAIL: cell volume is {atoms.cell.volume:.6f} — no cell defined")
        sys.exit(1)

    distances = atoms.get_all_distances(mic=True)
    np.fill_diagonal(distances, np.inf)
    min_dist = distances.min()

    if min_dist < 1.0:
        i, j = np.unravel_index(distances.argmin(), distances.shape)
        print(
            f"FAIL: min interatomic distance = {min_dist:.3f} Å "
            f"(atoms {i}-{j}: {atoms.symbols[i]}-{atoms.symbols[j]}). "
            f"Structure has overlapping atoms — fix before submitting."
        )
        sys.exit(1)

    print(
        f"PASS: {len(atoms)} atoms, min_dist={min_dist:.3f} Å, volume={atoms.cell.volume:.1f} Å³"
    )


if __name__ == "__main__":
    main()
