#!/usr/bin/env python3
"""
generate_kpoints.py — Generate VASP KPOINTS file from structure (POSCAR/CIF).

Uses pymatgen to compute appropriate k-point meshes (Gamma-centered
Monkhorst-Pack) or high-symmetry k-paths for band structure calculations.

Supports:
  - Automatic mesh: uniform mesh with target k-point density
  - Line-mode: high-symmetry k-path for band structure
  - Custom mesh: explicit N1×N2×N3 specification
  - Constant-product mesh: ensure N*a ≈ constant (uniform reciprocal sampling)

Usage:
  # Automatic mesh (density-based, suitable for SCF/DOS):
  python generate_kpoints.py --structure POSCAR --mode auto --density 40

  # Line-mode k-path (for band structure):
  python generate_kpoints.py --structure POSCAR --mode line --npoints 40

  # Custom mesh:
  python generate_kpoints.py --structure POSCAR --mode mesh --mesh 8 8 8

  # Constant product (N*a ≈ value):
  python generate_kpoints.py --structure POSCAR --mode constant --product 40

  # Slab (auto with vacuum detection, reduce to 1 in vacuum direction):
  python generate_kpoints.py --structure POSCAR --mode slab --density 40

Output: KPOINTS file written to --output (default: KPOINTS) and JSON summary to stdout.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def _load_structure(filepath: str):
    """Load structure with pymatgen."""
    from pymatgen.core import Structure

    return Structure.from_file(filepath)


def _detect_vacuum_direction(structure) -> int | None:
    """Detect vacuum direction (0=a, 1=b, 2=c) from lattice vectors.
    Returns None if no clear vacuum direction found."""
    abc = structure.lattice.abc
    # Heuristic: vacuum direction has length > 2× the average of others
    for i in range(3):
        others = [abc[j] for j in range(3) if j != i]
        if abc[i] > 2.0 * max(others):
            return i
    # Also check fractional span - if atoms only occupy a thin layer
    frac = np.array([s.frac_coords for s in structure])
    if len(frac) > 0:
        spans = np.ptp(frac, axis=0)
        for i in range(3):
            if spans[i] < 0.5 and abc[i] > 12.0:
                return i
    return None


def generate_auto_mesh(structure, density: float = 40.0) -> tuple[list[int], str]:
    """Generate Gamma-centered mesh with target k-point density.
    density = target k-points per Å⁻¹ along each reciprocal lattice direction.
    Returns (mesh, comment)."""
    from pymatgen.io.vasp.inputs import Kpoints

    # Use pymatgen's automatic mesh generation
    kpoints = Kpoints.automatic_density(structure, density * structure.num_sites)
    mesh = list(kpoints.kpts[0])
    mesh = [max(1, int(round(k))) for k in mesh]
    comment = f"Auto Gamma-centered mesh (density={density})"
    return mesh, comment


def generate_constant_product_mesh(
    structure, product: float = 40.0
) -> tuple[list[int], str]:
    """Generate mesh where N_i × a_i ≈ product for each direction.
    Ensures uniform reciprocal-space sampling regardless of cell shape."""
    abc = structure.lattice.abc
    mesh = [max(1, int(round(product / a))) for a in abc]
    comment = f"Constant-product mesh (N*a≈{product})"
    return mesh, comment


def generate_slab_mesh(structure, density: float = 40.0) -> tuple[list[int], str]:
    """Generate mesh appropriate for slab geometry.
    Detects vacuum direction and sets that to 1."""
    abc = structure.lattice.abc
    vac_dir = _detect_vacuum_direction(structure)

    # Base mesh from constant product
    product = density
    mesh = [max(1, int(round(product / a))) for a in abc]

    if vac_dir is not None:
        mesh[vac_dir] = 1
        comment = f"Slab mesh (vacuum along {'abc'[vac_dir]}, set to 1)"
    else:
        # No vacuum detected; use smallest direction = 1 as heuristic
        max_idx = int(np.argmax(abc))
        mesh[max_idx] = 1
        comment = f"Slab mesh (largest axis {'abc'[max_idx]} set to 1, no clear vacuum detected)"

    return mesh, comment


def generate_line_mode(structure, npoints: int = 40) -> str:
    """Generate line-mode KPOINTS for band structure using pymatgen HighSymmKpath."""
    from pymatgen.symmetry.bandstructure import HighSymmKpath

    kpath = HighSymmKpath(structure)
    path = kpath.kpath["path"]
    kpts = kpath.kpath["kpoints"]

    lines = [f"k-points along high-symmetry path (pymatgen HighSymmKpath)"]
    # Count line segments
    n_segments = sum(len(seg) - 1 for seg in path)
    lines.append(f"{n_segments * npoints}")
    lines.append("Line-mode")
    lines.append("Reciprocal")

    for segment in path:
        for i in range(len(segment) - 1):
            start_label = segment[i]
            end_label = segment[i + 1]
            start_coords = kpts[start_label]
            end_coords = kpts[end_label]

            # Write start point
            lines.append(
                f"  {start_coords[0]:.6f}  {start_coords[1]:.6f}  {start_coords[2]:.6f}  "
                f"! {start_label}"
            )
            # Write end point
            lines.append(
                f"  {end_coords[0]:.6f}  {end_coords[1]:.6f}  {end_coords[2]:.6f}  "
                f"! {end_label}"
            )
            lines.append("")

    return "\n".join(lines) + "\n"


def format_mesh_kpoints(mesh: list[int], comment: str, shift: list[float] | None = None) -> str:
    """Format a uniform mesh KPOINTS file."""
    if shift is None:
        shift = [0, 0, 0]
    lines = [
        comment,
        "0",  # automatic
        "Gamma",
        f"  {mesh[0]}  {mesh[1]}  {mesh[2]}",
        f"  {shift[0]}  {shift[1]}  {shift[2]}",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate VASP KPOINTS file from structure."
    )
    ap.add_argument(
        "--structure",
        required=True,
        help="Structure file (POSCAR, CIF, etc.)",
    )
    ap.add_argument(
        "--mode",
        required=True,
        choices=["auto", "line", "mesh", "constant", "slab"],
        help=(
            "Generation mode: "
            "auto=density-based Gamma mesh, "
            "line=high-symmetry k-path, "
            "mesh=explicit N1×N2×N3, "
            "constant=N*a≈product, "
            "slab=auto with vacuum detection"
        ),
    )
    ap.add_argument(
        "--density",
        type=float,
        default=40.0,
        help="K-point density parameter (default: 40). Higher = denser mesh.",
    )
    ap.add_argument(
        "--product",
        type=float,
        default=40.0,
        help="Constant product N*a (default: 40 Å). Used with --mode constant.",
    )
    ap.add_argument(
        "--mesh",
        type=int,
        nargs=3,
        default=None,
        metavar=("N1", "N2", "N3"),
        help="Explicit mesh (used with --mode mesh).",
    )
    ap.add_argument(
        "--npoints",
        type=int,
        default=40,
        help="Points per segment for line-mode (default: 40).",
    )
    ap.add_argument(
        "--output",
        default="KPOINTS",
        help="Output file path (default: KPOINTS).",
    )
    args = ap.parse_args()

    # Load structure
    struct_path = Path(args.structure)
    if not struct_path.exists():
        print(json.dumps({"success": False, "error": f"Structure file not found: {struct_path}"}))
        sys.exit(1)

    try:
        structure = _load_structure(str(struct_path))
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Failed to load structure: {e}"}))
        sys.exit(1)

    # Generate KPOINTS
    result = {
        "success": True,
        "mode": args.mode,
        "structure": str(struct_path),
        "lattice_abc": [round(x, 4) for x in structure.lattice.abc],
        "n_atoms": len(structure),
        "formula": structure.composition.reduced_formula,
    }

    if args.mode == "line":
        try:
            kpoints_text = generate_line_mode(structure, args.npoints)
            result["type"] = "line-mode"
            result["npoints_per_segment"] = args.npoints
        except Exception as e:
            print(json.dumps({"success": False, "error": f"Failed to generate k-path: {e}"}))
            sys.exit(1)
    elif args.mode == "mesh":
        if args.mesh is None:
            print(json.dumps({"success": False, "error": "--mesh N1 N2 N3 required for mode=mesh"}))
            sys.exit(1)
        mesh = args.mesh
        comment = f"Explicit mesh {mesh[0]}×{mesh[1]}×{mesh[2]}"
        kpoints_text = format_mesh_kpoints(mesh, comment)
        result["mesh"] = mesh
        result["type"] = "uniform"
    elif args.mode == "constant":
        mesh, comment = generate_constant_product_mesh(structure, args.product)
        kpoints_text = format_mesh_kpoints(mesh, comment)
        result["mesh"] = mesh
        result["type"] = "uniform"
        result["actual_products"] = [
            round(mesh[i] * structure.lattice.abc[i], 2) for i in range(3)
        ]
    elif args.mode == "slab":
        mesh, comment = generate_slab_mesh(structure, args.density)
        kpoints_text = format_mesh_kpoints(mesh, comment)
        result["mesh"] = mesh
        result["type"] = "slab"
        vac_dir = _detect_vacuum_direction(structure)
        if vac_dir is not None:
            result["vacuum_direction"] = "abc"[vac_dir]
    else:  # auto
        mesh, comment = generate_auto_mesh(structure, args.density)
        kpoints_text = format_mesh_kpoints(mesh, comment)
        result["mesh"] = mesh
        result["type"] = "uniform"

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(kpoints_text, encoding="utf-8")
    result["output_file"] = str(output_path)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
