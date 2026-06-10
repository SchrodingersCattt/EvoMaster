#!/usr/bin/env python3
"""passivate_surface.py — Hydrogen-passivate surface dangling bonds.

Saturates under-coordinated surface atoms on BOTH top and bottom surfaces
of a slab by placing H atoms along missing tetrahedral bond directions.
Default parameters target Si slabs (Si-H ~ 1.48 A, Si-Si cutoff 2.6 A).

Usage::

    python passivate_surface.py input_slab.cif [-o passivated.cif] \
        [--element Si] [--bond-length 1.48] [--cutoff 2.6] \
        [--target-coordination 4] [--surface-fraction 0.15]

Works with pymatgen (local) — no GPU required.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Geometry: missing tetrahedral directions
# ---------------------------------------------------------------------------


def _missing_tetrahedral(
    existing_bond_vecs: list[np.ndarray], n_missing: int
) -> list[np.ndarray]:
    """Return *n_missing* unit vectors completing a tetrahedron."""
    n = len(existing_bond_vecs)
    if n_missing <= 0 or n >= 4:
        return []

    if n == 0:
        tet = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], float)
        tet /= np.linalg.norm(tet[0])
        return [tet[i] for i in range(min(n_missing, 4))]

    if n == 3:
        s = -(existing_bond_vecs[0] + existing_bond_vecs[1] + existing_bond_vecs[2])
        nrm = np.linalg.norm(s)
        return [s / nrm] if nrm > 1e-3 else [np.array([0, 0, 1.0])]

    if n == 2:
        v1, v2 = existing_bond_vecs[0], existing_bond_vecs[1]
        s = -(v1 + v2)
        s_nrm = np.linalg.norm(s)
        if s_nrm < 1e-3:
            perp = np.cross(v1, [0, 0, 1])
            if np.linalg.norm(perp) < 1e-3:
                perp = np.cross(v1, [0, 1, 0])
            perp /= np.linalg.norm(perp)
            return [perp, -perp][:n_missing]
        s_hat = s / s_nrm
        n_vec = np.cross(v1, v2)
        n_nrm = np.linalg.norm(n_vec)
        if n_nrm < 1e-3:
            return [s_hat]
        n_hat = n_vec / n_nrm
        alpha = np.arccos(1.0 / np.sqrt(3.0))  # ~54.74 deg
        d1 = s_hat * np.cos(alpha) + n_hat * np.sin(alpha)
        d2 = s_hat * np.cos(alpha) - n_hat * np.sin(alpha)
        return [d1 / np.linalg.norm(d1), d2 / np.linalg.norm(d2)][:n_missing]

    if n == 1:
        v1 = existing_bond_vecs[0]
        cos_t, sin_t = -1.0 / 3.0, np.sqrt(8.0 / 9.0)
        perp = np.cross(v1, [0, 0, 1])
        if np.linalg.norm(perp) < 1e-3:
            perp = np.cross(v1, [0, 1, 0])
        perp /= np.linalg.norm(perp)
        dirs = []
        for phi in [0, 2 * np.pi / 3, 4 * np.pi / 3]:
            p = perp * np.cos(phi) + np.cross(v1, perp) * np.sin(phi)
            d = v1 * cos_t + p * sin_t
            dirs.append(d / np.linalg.norm(d))
        return dirs[:n_missing]

    return []


# ---------------------------------------------------------------------------
# Core passivation
# ---------------------------------------------------------------------------


def passivate(
    structure,
    element: str = "Si",
    cutoff: float = 2.6,
    bond_length: float = 1.48,
    target_coord: int = 4,
    surface_frac: float = 0.15,
):
    """Add H to under-coordinated *element* atoms on slab surfaces.

    Returns ``(new_structure, n_H_added)``.
    """
    coords = np.array([s.coords for s in structure])
    z = coords[:, 2]
    z_min, z_max = z.min(), z.max()
    thickness = z_max - z_min
    z_top_cut = z_max - surface_frac * thickness
    z_bot_cut = z_min + surface_frac * thickness

    h_cutoff = bond_length + 0.3  # for counting existing H neighbours

    h_positions: list[np.ndarray] = []

    for site in structure:
        if str(site.specie) != element:
            continue
        if not (site.coords[2] >= z_top_cut or site.coords[2] <= z_bot_cut):
            continue

        # Collect bond vectors to same-element and H neighbours
        bond_vecs: list[np.ndarray] = []
        neighbours = structure.get_neighbors(site, max(cutoff, h_cutoff))
        for nb in neighbours:
            nb_site, dist = nb[0], nb[1]
            sp = str(nb_site.specie)
            if (sp == element and dist < cutoff) or (sp == "H" and dist < h_cutoff):
                # Minimum-image bond vector
                frac_diff = nb_site.frac_coords - site.frac_coords
                frac_diff -= np.round(frac_diff)
                cart_vec = structure.lattice.get_cartesian_coords(frac_diff)
                bond_vecs.append(cart_vec / np.linalg.norm(cart_vec))

        n_missing = target_coord - len(bond_vecs)
        if n_missing <= 0:
            continue

        for d in _missing_tetrahedral(bond_vecs, n_missing):
            h_positions.append(site.coords + d * bond_length)

    # Build new structure
    new_struct = structure.copy()
    for pos in h_positions:
        new_struct.append("H", pos, coords_are_cartesian=True)

    return new_struct, len(h_positions)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify(
    structure,
    element: str,
    cutoff: float,
    bond_length: float,
    target_coord: int,
    surface_frac: float,
) -> int:
    """Print per-atom coordination for surface atoms; return #under-coordinated."""
    coords = np.array([s.coords for s in structure])
    z = coords[:, 2]
    z_min, z_max = z.min(), z.max()
    thickness = z_max - z_min
    z_top_cut = z_max - surface_frac * thickness
    z_bot_cut = z_min + surface_frac * thickness
    h_cutoff = bond_length + 0.3

    under = 0
    for i, site in enumerate(structure):
        if str(site.specie) != element:
            continue
        if not (site.coords[2] >= z_top_cut or site.coords[2] <= z_bot_cut):
            continue
        nbs = structure.get_neighbors(site, max(cutoff, h_cutoff))
        coord = sum(
            1
            for nb in nbs
            if (str(nb[0].specie) == element and nb[1] < cutoff)
            or (str(nb[0].specie) == "H" and nb[1] < h_cutoff)
        )
        surface = "top" if site.coords[2] >= z_top_cut else "bot"
        if coord < target_coord:
            under += 1
            print(
                f"  WARN {element}[{i}] z={site.coords[2]:.2f} "
                f"coord={coord} ({surface})"
            )
    return under


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description="Passivate slab surface with H")
    p.add_argument("structure", help="Input slab file (CIF / POSCAR)")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--element", default="Si")
    p.add_argument(
        "--bond-length",
        type=float,
        default=1.48,
        help="E-H bond length in Ang (default 1.48 for Si-H)",
    )
    p.add_argument(
        "--cutoff",
        type=float,
        default=2.6,
        help="E-E bond cutoff in Ang (default 2.6 for Si-Si)",
    )
    p.add_argument("--target-coordination", type=int, default=4)
    p.add_argument(
        "--surface-fraction",
        type=float,
        default=0.15,
        help="Fraction of slab thickness defining surface zone",
    )
    args = p.parse_args()

    from pymatgen.core import Structure

    struct = Structure.from_file(args.structure)
    print(f"Input: {len(struct)} atoms, {struct.composition.reduced_formula}")

    result, n_h = passivate(
        struct,
        args.element,
        args.cutoff,
        args.bond_length,
        args.target_coordination,
        args.surface_fraction,
    )

    out = args.output or f"{Path(args.structure).stem}_passivated.cif"
    result.to(filename=out)

    print(f"Added {n_h} H atoms → {len(result)} atoms total")
    print(f"Formula: {result.composition.reduced_formula}")

    under = verify(
        result,
        args.element,
        args.cutoff,
        args.bond_length,
        args.target_coordination,
        args.surface_fraction,
    )
    if under == 0:
        print(
            f"OK: all surface {args.element} atoms have "
            f"coordination >= {args.target_coordination}"
        )
    else:
        print(f"WARNING: {under} surface atoms still under-coordinated")

    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
