#!/usr/bin/env python3
"""Copy energy.npy to property.npy under set.* dirs when property.npy is missing.

Scans train*, valid*, test* trees for .../set.*/energy.npy.

Usage:
  python fix_property_npy.py
"""
from __future__ import annotations

import glob
import os
import shutil

from tqdm import tqdm


def main() -> None:
    datasets_path = glob.glob("train*/*/*/") + glob.glob("valid*/*/*/") + glob.glob(
        "test*/*/*/"
    )
    for dd in tqdm(datasets_path):
        fake_energy_file = dd + "energy.npy"
        property_file = dd + "property.npy"
        if not os.path.exists(property_file) and os.path.exists(fake_energy_file):
            shutil.copyfile(fake_energy_file, property_file)
    print("Done")


if __name__ == "__main__":
    main()
