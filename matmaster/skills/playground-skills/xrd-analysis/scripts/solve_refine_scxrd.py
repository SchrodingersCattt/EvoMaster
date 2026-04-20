#!/usr/bin/env python3
"""
solve_refine_scxrd.py — Single-crystal XRD structure solution, refinement & CIF.

Pipeline:
  1. Parse HKL file (SHELX HKLF4) and P4P/INS file (cell, space group)
  2. Try SHELX (shelxs+shelxl) if installed  →  best quality
  3. Fallback: Python charge-flipping + least-squares refinement
  4. Write CIF and print JSON summary

Dependencies: numpy, scipy.
Optional:     pymatgen (space group ops), shelxs/shelxl (preferred if on PATH).

Usage:
  python solve_refine_scxrd.py --hkl data.hkl --p4p crystal.p4p -o refined.cif
  python solve_refine_scxrd.py --hkl data.hkl --ins crystal.ins -o refined.cif
  python solve_refine_scxrd.py --hkl data.hkl --cell "12 8 14 90 95 90" \\
         --sg P21 --wavelength 0.71073 -o refined.cif
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys

import numpy as np

from solve_refine_scxrd_lib import (
    _assign_types,
    _cell_volume,
    _charge_flipping,
    _find_atoms,
    _formula_from_atoms,
    _get_sg_ops,
    _refine,
    _try_shelx,
    _write_cif,
    parse_hkl,
    parse_ins,
    parse_p4p,
)


def main():
    ap = argparse.ArgumentParser(
        description="SCXRD structure solution & CIF generation"
    )
    ap.add_argument("--hkl", required=True, help="HKL file (SHELX HKLF4)")
    ap.add_argument("--p4p", help="Bruker P4P file")
    ap.add_argument("--ins", help="SHELX INS file (alternative to P4P)")
    ap.add_argument("--cell", help='Manual cell "a b c alpha beta gamma"')
    ap.add_argument("--sg", help="Space group symbol or number")
    ap.add_argument("--wavelength", type=float, help="Wavelength in Å")
    ap.add_argument("--elements", help='Expected elements, e.g. "C H N O S"')
    ap.add_argument(
        "--grid", type=int, default=96, help="Charge-flipping grid (default 96)"
    )
    ap.add_argument("--cycles", type=int, default=800, help="CF cycles (default 800)")
    ap.add_argument(
        "--trials", type=int, default=3, help="CF random trials (default 3)"
    )
    ap.add_argument("-o", "--output", default="refined.cif", help="Output CIF path")
    args = ap.parse_args()

    # ── Gather cell, wavelength, space group ──
    cell = wl = sg_str = None
    elements = None

    if args.p4p:
        info = parse_p4p(args.p4p)
        cell, wl, sg_str = info["cell"], info["wavelength"], info.get("sg")
    if args.ins:
        info = parse_ins(args.ins)
        cell = cell or info["cell"]
        wl = wl or info["wavelength"]
        elements = info.get("elements") or None
    if args.cell:
        cell = [float(x) for x in args.cell.split()]
    if args.sg:
        sg_str = args.sg
    if args.wavelength:
        wl = args.wavelength
    if args.elements:
        elements = args.elements.split()
    wl = wl or 0.71073

    if cell is None:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": "No cell parameters found. Provide --p4p, --ins, or --cell.",
                }
            )
        )
        sys.exit(1)

    sg_ops, sg_number = _get_sg_ops(sg_str)
    sg_symbol = sg_str or f"P (#{sg_number})"

    print(f"Cell: {cell}", file=sys.stderr)
    print(f"SG: {sg_symbol} (#{sg_number}), wavelength: {wl} Å", file=sys.stderr)

    # ── Read reflections ──
    hkl_data = parse_hkl(args.hkl)
    n_ref = len(hkl_data["fsq"])
    print(f"Reflections: {n_ref}", file=sys.stderr)

    # ── Try SHELX first ──
    shelx_cif = _try_shelx(args.hkl, cell, wl, sg_number, elements)
    if shelx_cif:
        shutil.copy(shelx_cif, args.output)
        print(
            json.dumps(
                {
                    "success": True,
                    "method": "SHELX",
                    "cif": args.output,
                    "cell_volume": round(_cell_volume(cell), 2),
                    "space_group": sg_symbol,
                    "space_group_number": sg_number,
                },
                indent=2,
            )
        )
        return

    print("SHELX not available; using Python charge-flipping…", file=sys.stderr)

    # ── Charge flipping ──
    f_obs = np.sqrt(np.maximum(hkl_data["fsq"], 0))
    rho = _charge_flipping(
        hkl_data["hkl"],
        f_obs,
        cell,
        sg_ops,
        grid=args.grid,
        cycles=args.cycles,
        n_trials=args.trials,
    )

    # ── Find atoms ──
    frac_coords, peak_vals = _find_atoms(rho, cell, sg_ops)
    if len(frac_coords) == 0:
        print(
            json.dumps(
                {"success": False, "error": "No atoms found in charge-flipping density"}
            )
        )
        sys.exit(1)

    # Limit to reasonable number (avoid noise peaks)
    V = _cell_volume(cell)
    max_atoms = max(int(V / 10), 60)  # ~10 ų per atom
    frac_coords = frac_coords[:max_atoms]
    peak_vals = peak_vals[:max_atoms]

    types = _assign_types(peak_vals, elements)
    atoms = [
        {"elem": t, "frac": list(fc), "B": 2.0} for t, fc in zip(types, frac_coords)
    ]
    print(f"Atoms found: {len(atoms)}", file=sys.stderr)

    # ── Refine ──
    try:
        atoms_ref, rfactors = _refine(atoms, hkl_data, cell, wl, sg_ops, max_iter=8)
    except Exception as e:
        print(f"Refinement error: {e}; writing unrefined CIF", file=sys.stderr)
        atoms_ref = atoms
        rfactors = {
            "R1": 0.99,
            "wR2": 0.99,
            "GOOF": 0.0,
            "n_obs": n_ref,
            "n_params": 1 + 4 * len(atoms),
            "scale": 1.0,
        }

    # ── Write CIF ──
    formula = _formula_from_atoms(atoms_ref, sg_ops)
    vol = _write_cif(
        args.output,
        cell,
        sg_symbol,
        sg_number,
        atoms_ref,
        rfactors,
        wl,
        formula,
        z_formula=len(sg_ops),
        sg_ops=sg_ops,
    )

    summary = {
        "success": True,
        "method": "charge_flipping",
        "cif": args.output,
        "cell_volume": vol,
        "space_group": sg_symbol,
        "space_group_number": sg_number,
        "R1": rfactors["R1"],
        "wR2": rfactors["wR2"],
        "GOOF": rfactors["GOOF"],
        "n_atoms_asym": len(atoms_ref),
        "formula": formula,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
