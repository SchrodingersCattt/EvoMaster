#!/usr/bin/env python3
"""
Batch DeepProperty inference over deepmd/npy system directories.

Usage:
  python run_inference.py --model model.ckpt-200000.pt --list systems.txt [--head HEAD]

systems.txt: one system path per line.
Prints MAE and RMSE vs energies in dpdata (property-as-energy convention) or property.npy if present.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def read_list(path: Path) -> list[Path]:
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(Path(s))
    return out


def remap_atom_types(atom_types, data_type_map, model_type_map):
    names = list(data_type_map)
    return np.array(
        [np.where(np.array(model_type_map) == names[t])[0][0] for t in atom_types],
        dtype=np.int32,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--list", dest="list_file", type=Path, required=True)
    ap.add_argument("--head", type=str, default=None)
    args = ap.parse_args()

    from deepmd.pt.infer.deep_eval import DeepProperty
    import dpdata

    kwargs = {}
    if args.head:
        kwargs["head"] = args.head
    model = DeepProperty(str(args.model), **kwargs)
    model_type_map = model.get_type_map()

    preds, gts = [], []
    for sys_path in read_list(args.list_file):
        vs = dpdata.LabeledSystem(str(sys_path), fmt="deepmd/npy")
        coords = vs.data["coords"]
        cells = None if (sys_path / "nopbc").exists() else vs.data["cells"]
        atom_types = remap_atom_types(vs.data["atom_types"], vs.data["atom_names"], model_type_map)
        pred = model.eval(coords=coords, atom_types=atom_types, cells=cells)[0]
        pred = np.asarray(pred).reshape(-1)
        gt = np.asarray(vs.data["energies"], dtype=np.float64).reshape(-1)
        preds.append(pred[0])
        gts.append(gt[0])

    preds = np.array(preds)
    gts = np.array(gts)
    mae = np.mean(np.abs(preds - gts))
    rmse = float(np.sqrt(np.mean((preds - gts) ** 2)))
    print(f"n={len(preds)}  MAE={mae:g}  RMSE={rmse:g}")


if __name__ == "__main__":
    main()
