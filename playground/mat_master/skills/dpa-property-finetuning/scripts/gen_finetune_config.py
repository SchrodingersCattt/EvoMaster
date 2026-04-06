#!/usr/bin/env python3
"""
Merge a base DeePMD input.json with train/validation system path lists.

Usage:
  python gen_finetune_config.py --base base.json --train-list train.txt --val-list val.txt -o input.json

Each list file: one system directory path per line (relative or absolute).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_list(path: Path) -> list[str]:
    lines = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            lines.append(s)
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, required=True, help="Template input.json")
    ap.add_argument("--train-list", type=Path, required=True)
    ap.add_argument("--val-list", type=Path, required=True)
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    cfg: dict[str, Any] = json.loads(args.base.read_text(encoding="utf-8"))
    train = read_list(args.train_list)
    val = read_list(args.val_list)
    tr = cfg.setdefault("training", {})
    tr.setdefault("training_data", {})["systems"] = train
    tr.setdefault("validation_data", {})["systems"] = val
    args.output.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {args.output} ({len(train)} train, {len(val)} val systems)")


if __name__ == "__main__":
    main()
