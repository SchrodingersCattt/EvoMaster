#!/usr/bin/env python3
"""Expand one deepmd/npy/mixed directory into per-system deepmd/npy folders.

Usage:
  python mixed_to_npy.py --mixed-dir ./test --out-root ./test_npy
"""
from __future__ import annotations

import argparse
from pathlib import Path

import dpdata


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mixed-dir", type=Path, required=True)
    ap.add_argument("--out-root", type=Path, required=True)
    args = ap.parse_args()

    ms = dpdata.MultiSystems().load_systems_from_file(
        str(args.mixed_dir), fmt="deepmd/npy/mixed"
    )
    args.out_root.mkdir(parents=True, exist_ok=True)
    for idx, ss in enumerate(ms):
        d = args.out_root / f"sys_{idx:05d}"
        ss.to_deepmd_npy(str(d))
        print(d)


if __name__ == "__main__":
    main()
