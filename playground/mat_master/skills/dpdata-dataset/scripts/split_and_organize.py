#!/usr/bin/env python3
"""
Organize deepmd/npy system directories into train/ and val/ folders.

Modes:
  copy  — copy directories
  symlink — ln -s (default)

Inputs:
  --systems-root DIR   parent containing one folder per system
  --train-ids train.txt   one folder name per line (not full path)
  --val-ids val.txt
  --out DIR            created as out/train, out/val
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def read_ids(path: Path) -> list[str]:
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--systems-root", type=Path, required=True)
    ap.add_argument("--train-ids", type=Path, required=True)
    ap.add_argument("--val-ids", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--mode", choices=("copy", "symlink"), default="symlink")
    args = ap.parse_args()

    train_d = args.out / "train"
    val_d = args.out / "val"
    train_d.mkdir(parents=True, exist_ok=True)
    val_d.mkdir(parents=True, exist_ok=True)

    def place(name: str, dest_root: Path) -> None:
        src = args.systems_root / name
        dst = dest_root / name
        if not src.is_dir():
            raise FileNotFoundError(src)
        if dst.exists():
            return
        if args.mode == "copy":
            shutil.copytree(src, dst)
        else:
            dst.symlink_to(src.resolve(), target_is_directory=True)

    for n in read_ids(args.train_ids):
        place(n, train_d)
    for n in read_ids(args.val_ids):
        place(n, val_d)
    print("Done:", train_d, val_d)


if __name__ == "__main__":
    main()
