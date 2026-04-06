#!/usr/bin/env python3
"""Split deepmd/npy system directories under a parent into train/ and valid/."""
from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import dpdata


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets-root", type=Path, required=True)
    ap.add_argument("--ratio", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    dataset_path = args.datasets_root
    dataset_dirs = [
        x
        for x in dataset_path.iterdir()
        if x.is_dir() and x.name not in ("train", "valid", "test")
    ]

    train_systems = dpdata.MultiSystems()
    valid_systems = dpdata.MultiSystems()
    mixed_type = False

    for folder in dataset_dirs:
        for f in folder.rglob("type.raw"):
            path = f.parent
            is_mixed = len(list(path.glob("*/real_atom_types.npy"))) > 0
            mixed_type = mixed_type or is_mixed
            d = dpdata.MultiSystems()
            if is_mixed:
                d.load_systems_from_file(str(path), fmt="deepmd/npy/mixed")
            else:
                k = dpdata.LabeledSystem(path, fmt="deepmd/npy")
                d.append(k)

            for s in d:
                ns = math.floor(len(s) * args.ratio)
                if random.random() < len(s) * args.ratio - ns:
                    ns += 1
                selected_indices = random.sample(range(len(s)), ns) if ns > 0 else []
                unselected_indices = list(set(range(len(s))).difference(selected_indices))

                if len(selected_indices) > 0:
                    valid_systems.append(s.sub_system(selected_indices))
                if len(unselected_indices) > 0:
                    train_systems.append(s.sub_system(unselected_indices))

    if len(valid_systems) > 0:
        target = dataset_path / "valid"
        target.mkdir(exist_ok=True)
        if mixed_type:
            valid_systems.to_deepmd_npy_mixed(target)
        else:
            valid_systems.to_deepmd_npy(target)

    if len(train_systems) > 0:
        target = dataset_path / "train"
        target.mkdir(exist_ok=True)
        if mixed_type:
            train_systems.to_deepmd_npy_mixed(target)
        else:
            train_systems.to_deepmd_npy(target)

    print(f"valid systems: {len(valid_systems)}, train systems: {len(train_systems)}")


if __name__ == "__main__":
    main()
