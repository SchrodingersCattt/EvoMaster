#!/usr/bin/env python3
"""Pack one pymatgen Structure + scalar label into deepmd/npy via dpdata."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import dpdata
from pymatgen.core import Structure


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--structure", type=Path, required=True)
    ap.add_argument("--property", type=float, required=True)
    ap.add_argument("--type-map", type=Path, required=True, dest="type_map")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    type_map = [
        ln.strip()
        for ln in args.type_map.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    suf = args.structure.suffix.lower()
    if suf == ".json":
        s = Structure.from_dict(
            json.loads(args.structure.read_text(encoding="utf-8"))
        )
    else:
        s = Structure.from_file(str(args.structure))

    dp_sys = dpdata.System(s, fmt="pymatgen/structure", type_map=type_map)
    forces = np.zeros_like(dp_sys.data["coords"])
    dp_sys.data["energies"] = np.array([args.property], dtype=np.float64)
    dp_sys.data["forces"] = np.array(forces)
    lab = dpdata.LabeledSystem(data=dp_sys.data, type_map=type_map)
    args.out.mkdir(parents=True, exist_ok=True)
    lab.to_deepmd_npy(str(args.out))
    print("Wrote", args.out)


if __name__ == "__main__":
    main()
