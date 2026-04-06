#!/usr/bin/env python3
"""
Template: write one DeepMD deepmd/npy system directory with a scalar property label.

Usage:
  python prep_property_npy.py --out ./datasets/sys001 \\
    --coords coords.npy --box box.npy --types types.npy \\
    --symbols H,C,N,O --property 8420.0

coords.npy: (natoms, 3) Cartesian Angstrom
box.npy: (3, 3) cell matrix rows = lattice vectors (pymatgen-style), Angstrom
types.npy: (natoms,) int indices into --symbols list (0-based)

Alternatively pass --type-map-file with one element symbol per line (full 118 list).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def write_system(
    out_dir: Path,
    coords: np.ndarray,
    box: np.ndarray,
    atom_type_indices: np.ndarray,
    type_map: list[str],
    prop: float,
    nopbc: bool,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    set_dir = out_dir / "set.000"
    set_dir.mkdir(parents=True, exist_ok=True)

    n = coords.shape[0]
    coord_flat = coords.reshape(1, -1).astype(np.float64)
    box_flat = box.reshape(1, 9).astype(np.float64)
    energy = np.array([prop], dtype=np.float64)
    force = np.zeros((1, n, 3), dtype=np.float64)

    np.savetxt(out_dir / "type.raw", atom_type_indices.astype(np.int32), fmt="%d")
    (out_dir / "type_map.raw").write_text("\n".join(type_map) + "\n", encoding="utf-8")
    np.save(set_dir / "coord.npy", coord_flat)
    np.save(set_dir / "box.npy", box_flat)
    np.save(set_dir / "energy.npy", energy)
    np.save(set_dir / "force.npy", force)
    np.save(set_dir / "property.npy", energy.copy())
    if nopbc:
        (out_dir / "nopbc").write_text("", encoding="utf-8")


def parse_symbols(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--coords", type=Path, required=True)
    p.add_argument("--box", type=Path, required=True)
    p.add_argument("--types", type=Path, required=True)
    p.add_argument("--symbols", type=str, default="", help="Comma-separated subset symbols matching type indices")
    p.add_argument("--type-map-file", type=Path, default=None, help="Full type_map.raw content (one symbol per line)")
    p.add_argument("--property", type=float, required=True)
    p.add_argument("--nopbc", action="store_true")
    args = p.parse_args()

    coords = np.load(args.coords)
    box = np.load(args.box)
    types = np.load(args.types)

    if args.type_map_file is not None:
        type_map = [ln.strip() for ln in args.type_map_file.read_text().splitlines() if ln.strip()]
    else:
        syms = parse_symbols(args.symbols)
        if not syms:
            raise SystemExit("Provide --symbols or --type-map-file")
        type_map = syms

    write_system(args.out, coords, box, types, type_map, args.property, args.nopbc)
    print("Wrote", args.out)


if __name__ == "__main__":
    main()
