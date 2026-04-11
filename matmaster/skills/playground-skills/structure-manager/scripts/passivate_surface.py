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
    surface: str = "both",
):
    """Add H to under-coordinated *element* atoms on slab surfaces.

    *surface*: ``"both"`` (default), ``"top"``, or ``"bottom"`` — select which
    surface(s) to passivate.

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
        sz = site.coords[2]
        if surface == "top":
            if sz < z_top_cut:
                continue
        elif surface == "bottom":
            if sz > z_bot_cut:
                continue
        else:  # both
            if not (sz >= z_top_cut or sz <= z_bot_cut):
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
# Detailed reporting (bond lengths, angles, per-surface stats)
# ---------------------------------------------------------------------------


def _detailed_report(
    structure,
    element: str,
    cutoff: float,
    bond_length: float,
) -> dict:
    """Compute bond-length and angle statistics for passivation H atoms.

    Returns dict with keys: top_h, bot_h, si_h_bonds, si_si_h_angles.
    """
    h_cutoff = bond_length + 0.3
    coords_z = np.array([s.coords[2] for s in structure])
    z_mid = (coords_z.min() + coords_z.max()) / 2.0

    top_h = bot_h = 0
    el_h_bonds: list[float] = []
    el_el_h_angles: list[float] = []

    for i, h_site in enumerate(structure):
        if str(h_site.specie) != "H":
            continue

        # Nearest *element* atom = bonded parent
        best_dist = float("inf")
        best_idx: int | None = None
        for nb in structure.get_neighbors(h_site, h_cutoff):
            if str(nb[0].specie) == element and nb[1] < best_dist:
                best_dist = nb[1]
                best_idx = nb[2]

        if best_idx is None:
            continue

        parent = structure[best_idx]
        el_h_bonds.append(best_dist)
        if parent.coords[2] >= z_mid:
            top_h += 1
        else:
            bot_h += 1

        # Element–Element–H angles (vertex = parent atom)
        h_fd = h_site.frac_coords - parent.frac_coords
        h_fd -= np.round(h_fd)
        v_h = structure.lattice.get_cartesian_coords(h_fd)
        norm_vh = np.linalg.norm(v_h)
        if norm_vh < 1e-6:
            continue

        for nb2 in structure.get_neighbors(parent, cutoff):
            if str(nb2[0].specie) != element or nb2[1] >= cutoff:
                continue
            el_fd = nb2[0].frac_coords - parent.frac_coords
            el_fd -= np.round(el_fd)
            v_el = structure.lattice.get_cartesian_coords(el_fd)
            norm_vel = np.linalg.norm(v_el)
            if norm_vel < 1e-6:
                continue
            cos_a = float(np.clip(np.dot(v_el, v_h) / (norm_vel * norm_vh), -1, 1))
            el_el_h_angles.append(float(np.degrees(np.arccos(cos_a))))

    return {
        "top_h": top_h,
        "bot_h": bot_h,
        "el_h_bonds": el_h_bonds,
        "el_el_h_angles": el_el_h_angles,
    }


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
    p.add_argument(
        "--surface",
        choices=["both", "top", "bottom"],
        default="both",
        help="Which surface(s) to passivate (default: both)",
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
        args.surface,
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

    # --- Detailed passivation report ---
    report = _detailed_report(result, args.element, args.cutoff, args.bond_length)

    print(f"\n=== PASSIVATION REPORT ===")
    print(f"  H atoms added: {n_h}")
    print(f"  Top surface H: {report['top_h']}")
    print(f"  Bottom surface H: {report['bot_h']}")
    if report["el_h_bonds"]:
        bl = report["el_h_bonds"]
        print(
            f"  {args.element}-H bond lengths: "
            f"mean={np.mean(bl):.4f} A, "
            f"range=[{np.min(bl):.4f}, {np.max(bl):.4f}]"
        )
    if report["el_el_h_angles"]:
        aa = report["el_el_h_angles"]
        print(
            f"  {args.element}-{args.element}-H angles: "
            f"mean={np.mean(aa):.2f} deg, "
            f"range=[{np.min(aa):.2f}, {np.max(aa):.2f}]"
        )
    coord_ok = under == 0
    print(
        f"  Post-passivation: all surface {args.element} "
        f">= CN{args.target_coordination}: {'YES' if coord_ok else 'NO'}"
    )
    print("=========================")

    # Write JSON summary alongside the structure file
    import json as _json

    json_report = {
        "input_atoms": len(struct),
        "output_atoms": len(result),
        "h_added": n_h,
        "h_top_surface": report["top_h"],
        "h_bottom_surface": report["bot_h"],
        "formula": result.composition.reduced_formula,
        "element_h_bond_length_mean_A": (
            round(float(np.mean(report["el_h_bonds"])), 4)
            if report["el_h_bonds"]
            else None
        ),
        "element_element_h_angle_mean_deg": (
            round(float(np.mean(report["el_el_h_angles"])), 2)
            if report["el_el_h_angles"]
            else None
        ),
        "all_surface_coordinated": coord_ok,
        "output_file": str(out),
    }
    report_path = str(Path(out).with_suffix("")) + "_report.json"
    Path(report_path).write_text(_json.dumps(json_report, indent=2) + "\n")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
