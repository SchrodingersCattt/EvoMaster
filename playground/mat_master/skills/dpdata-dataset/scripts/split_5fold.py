#!/usr/bin/env python3
"""5-fold split of per-system deepmd/npy/mixed directories under a glob.

Holds out a small test fraction from all systems, then KFold-splits the rest
into train_fold_1 .. train_fold_5 (each fold is validation systems for that split).

Usage:
  python split_5fold.py --datasets-glob 'datasets/*' --test-ratio 0.02 --seed 42

Requires: scikit-learn (sklearn.model_selection.KFold)
"""
from __future__ import annotations

import argparse
import glob
import random

import dpdata
from sklearn.model_selection import KFold
from tqdm import tqdm


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets-glob", type=str, default="datasets/*")
    ap.add_argument("--test-ratio", type=float, default=0.02)
    ap.add_argument("--num-folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    datasets = sorted(glob.glob(args.datasets_glob))
    datasets = [d for d in datasets if __import__("os").path.isdir(d)]
    if not datasets:
        raise SystemExit(f"No directories matched: {args.datasets_glob}")

    num_tst = max(1, int(args.test_ratio * len(datasets)))
    random.shuffle(datasets)
    tst_datasets = datasets[:num_tst]
    remaining = datasets[num_tst:]

    ms_tst = dpdata.MultiSystems()
    for dataset in tqdm(tst_datasets, desc="test"):
        ms_temp = dpdata.MultiSystems().load_systems_from_file(dataset, fmt="deepmd/npy/mixed")
        ms_tst.append(ms_temp)
    ms_tst.to_deepmd_npy_mixed("test")
    print(f"Wrote test/ with {len(ms_tst)} systems from {len(tst_datasets)} dirs.")

    ms_trn_folds = [dpdata.MultiSystems() for _ in range(args.num_folds)]
    kf = KFold(n_splits=args.num_folds, shuffle=True, random_state=args.seed)
    fold_indices = list(kf.split(remaining))

    for fold, (_, val_idx) in enumerate(fold_indices):
        for idx in val_idx:
            dataset = remaining[idx]
            ms_temp = dpdata.MultiSystems().load_systems_from_file(dataset, fmt="deepmd/npy/mixed")
            ms_trn_folds[fold].append(ms_temp)

    for fold, ms_fold in enumerate(ms_trn_folds):
        fold_name = f"train_fold_{fold + 1}"
        out = f"train_{fold_name}"
        ms_fold.to_deepmd_npy_mixed(out)
        print(f"Saved {out} with {len(ms_fold)} systems.")


if __name__ == "__main__":
    main()
