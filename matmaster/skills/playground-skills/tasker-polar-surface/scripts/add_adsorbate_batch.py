#!/usr/bin/env python3
"""Batch adsorbate placement on slab surfaces using ASE.

Usage:
  # Single file
  python add_adsorbate_batch.py -s slab.vasp -a CO.xyz --shift "0.5,0.5" --height 2.0 -o slab_CO.cif

  # Multi slab + shared params
  python add_adsorbate_batch.py -s slab1.vasp slab2.cif -a CO.xyz --shift ontop --height 1.8 --output-dir ./ads_slabs/

  # Batch JSON config (each entry has independent params)
  python add_adsorbate_batch.py --batch ads_config.json

Batch config JSON format:
  [
    {"surface": "slab1.vasp", "adsorbate": "CO.xyz", "shift": [0.5, 0.5], "height": 2.0, "output": "slab1_CO.cif"},
    {"surface": "slab2.vasp", "adsorbate": "OH.xyz", "shift": "ontop", "height": 1.5, "output": "slab2_OH.cif"}
  ]

Output: JSON summary {"results": [{"surface":..,"adsorbate":..,"output":..,"success":bool,"error":..},..]}
Exit code: 0 = all success, 1 = any failure.
"""
import argparse
import json
import os
import sys

import numpy as np

SITE_KEYWORDS = {"ontop", "fcc", "hcp", "bridge"}


def _parse_shift(shift):
    """Parse shift spec: fractional coords [x,y], string 'x,y', or ASE site keyword."""
    if isinstance(shift, list):
        return [float(v) for v in shift[:2]]
    s = str(shift).strip().lower()
    if s in SITE_KEYWORDS:
        return s
    parts = s.replace(" ", ",").split(",")
    return [float(x) for x in parts if x.strip()]


def _place_adsorbate(slab, ads, shift, height):
    """Place adsorbate on slab. Handles both fractional coords and ASE keywords."""
    from ase.build import add_adsorbate

    parsed = _parse_shift(shift)
    h = float(height)

    if isinstance(parsed, str):
        # ASE keyword: ontop, fcc, hcp, bridge (works for FCC 111)
        add_adsorbate(slab, ads, h, position=parsed)
    elif isinstance(parsed, list) and len(parsed) >= 2:
        # Fractional coordinates -> absolute xy position
        cell = slab.get_cell()
        pos_x = parsed[0] * cell[0][0] + parsed[1] * cell[1][0]
        pos_y = parsed[0] * cell[0][1] + parsed[1] * cell[1][1]
        add_adsorbate(slab, ads, h, position=(pos_x, pos_y))
    else:
        # Fallback: cell center
        add_adsorbate(slab, ads, h)


def _auto_output(surface_path, output_dir=None):
    """Generate output path: {stem}_ads.cif"""
    stem = os.path.splitext(os.path.basename(surface_path))[0]
    name = f"{stem}_ads.cif"
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        return os.path.join(output_dir, name)
    return name


def process_single(surface_path, adsorbate_path, shift, height, output_path, quiet=False):
    """Place adsorbate on one slab. Returns result dict."""
    try:
        from ase.io import read, write

        slab = read(surface_path)
        ads = read(adsorbate_path)
        n_slab = len(slab)

        _place_adsorbate(slab, ads, shift, height)

        if not output_path:
            output_path = _auto_output(surface_path)

        write(output_path, slab)

        result = {
            "surface": surface_path,
            "adsorbate": adsorbate_path,
            "output": output_path,
            "success": True,
            "n_atoms_slab": n_slab,
            "n_atoms_total": len(slab),
            "n_atoms_adsorbate": len(slab) - n_slab,
            "shift": str(shift),
            "height": float(height),
        }
        if not quiet:
            print(f"OK  {output_path}  ({len(slab)} atoms, +{len(slab)-n_slab} ads)",
                  file=sys.stderr)
        return result

    except Exception as e:
        result = {
            "surface": surface_path,
            "adsorbate": adsorbate_path,
            "output": output_path or "",
            "success": False,
            "error": str(e),
        }
        if not quiet:
            print(f"FAIL  {surface_path}: {e}", file=sys.stderr)
        return result


def process_batch(config_path, quiet=False):
    """Process batch config JSON. Each entry can have independent params."""
    with open(config_path) as f:
        configs = json.load(f)

    results = []
    for entry in configs:
        r = process_single(
            surface_path=entry["surface"],
            adsorbate_path=entry["adsorbate"],
            shift=entry.get("shift", [0.5, 0.5]),
            height=entry.get("height", 2.0),
            output_path=entry.get("output"),
            quiet=quiet,
        )
        results.append(r)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Batch adsorbate placement on slab surfaces (ASE-based)")
    parser.add_argument("-s", "--surface", nargs="+",
                        help="Surface slab file(s)")
    parser.add_argument("-a", "--adsorbate",
                        help="Adsorbate molecule file (XYZ, CIF, POSCAR)")
    parser.add_argument("-o", "--output",
                        help="Output file (single-file mode only)")
    parser.add_argument("--output-dir",
                        help="Output directory for multi-file mode (auto-names: {stem}_ads.cif)")
    parser.add_argument("--shift", default="0.5,0.5",
                        help='Adsorption position: fractional "x,y" or keyword '
                             '(ontop/fcc/hcp/bridge). Default: "0.5,0.5"')
    parser.add_argument("--height", type=float, default=2.0,
                        help="Adsorption height in Angstrom (default: 2.0)")
    parser.add_argument("--batch",
                        help="Batch config JSON file (overrides -s/-a)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress verbose output to stderr")

    args = parser.parse_args()

    if args.batch:
        results = process_batch(args.batch, quiet=args.quiet)
    elif args.surface and args.adsorbate:
        results = []
        if len(args.surface) == 1 and not args.output_dir:
            # Single-file mode
            r = process_single(
                args.surface[0], args.adsorbate,
                args.shift, args.height, args.output,
                quiet=args.quiet)
            results.append(r)
        else:
            # Multi-file + shared params
            out_dir = args.output_dir or "."
            os.makedirs(out_dir, exist_ok=True)
            for surf in args.surface:
                out_path = _auto_output(surf, out_dir)
                r = process_single(
                    surf, args.adsorbate,
                    args.shift, args.height, out_path,
                    quiet=args.quiet)
                results.append(r)
    else:
        parser.error("Provide either --batch CONFIG.json or (-s SURFACE(s) -a ADSORBATE)")
        return

    # Output summary JSON to stdout
    print(json.dumps({"results": results}, indent=2))

    # Exit code: 0 all success, 1 any failure
    if any(not r["success"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
