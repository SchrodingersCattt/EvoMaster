"""
Build slab with a lightweight Tasker-style polarity check/fix and optional tiling.

This script is practical but not universal. Some surfaces may remain polar after
automatic attempts. In that case, it exits non-zero and asks the caller to
manually adjust settings or temporarily accept the polar slab.
"""

import argparse
import json
import math
import os
import sys
from collections import Counter

import numpy as np
from ase.build import surface
from ase.geometry import get_layers
from ase.io import read, write


def _is_metal_symbol(symbol: str) -> bool:
    """
    Lightweight metal classifier by element symbol.

    - Returns False for common nonmetals/metalloids.
    - Returns True otherwise (treated as metal-like for this script).
    """
    nonmetals = {
        "H",
        "He",
        "C",
        "N",
        "O",
        "F",
        "Ne",
        "P",
        "S",
        "Cl",
        "Ar",
        "Se",
        "Br",
        "Kr",
        "I",
        "Xe",
        "Rn",
        "At",
        "Og",
    }
    metalloids = {"B", "Si", "Ge", "As", "Sb", "Te", "Po"}
    return symbol not in nonmetals and symbol not in metalloids


def auto_charge_map(atoms):
    """
    Auto-generate nominal charges.

    - Single-element system: assign 0.0 to that element.
    - Binary all-metal system: assign 0.0 to both elements.
    - Typical ionic binary (metal + nonmetal): solve n_M * q_M + n_X * q_X = 0
      with q_M = +n_X, q_X = -n_M.
    - Other binaries (e.g., nonmetal-nonmetal / metalloid-containing):
      assign 0.0 to avoid unreliable ionic assumptions.
    """
    symbols = atoms.get_chemical_symbols()
    count = Counter(symbols)

    if len(count) == 1:
        elem = next(iter(count.keys()))
        return {elem: 0.0}
    if len(count) != 2:
        raise ValueError("自动电荷仅支持单元素或二元化合物")

    elems = list(count.keys())
    e1, e2 = elems[0], elems[1]
    n1 = count[e1]
    n2 = count[e2]
    is_m1 = _is_metal_symbol(e1)
    is_m2 = _is_metal_symbol(e2)

    # All-metal alloy: default neutral pseudo-charge for polarity heuristic.
    if is_m1 and is_m2:
        return {e1: 0.0, e2: 0.0}

    # Typical ionic binary: metal + nonmetal.
    if is_m1 and not is_m2:
        return {e1: float(n2), e2: float(-n1)}
    if (not is_m1) and is_m2:
        return {e2: float(n1), e1: float(-n2)}

    # Other binary chemistry: keep neutral pseudo-charge to avoid false polarity.
    return {e1: 0.0, e2: 0.0}


def parse_charge_map(s: str):
    """Parse charge map from JSON string or 'A:1,B:-1' format."""
    s = s.strip()
    if s.startswith("{"):
        d = json.loads(s)
        return {str(k): float(v) for k, v in d.items()}

    d = {}
    for item in s.split(","):
        sym, val = item.split(":")
        d[sym.strip()] = float(val.strip())
    return d


def get_z_layer_data(atoms, layer_tol=0.5):
    """Identify z-layers and return layer ids, mean z, and atom indices per layer."""
    z_layers, _ = get_layers(atoms, (0, 0, 1), tolerance=layer_tol)
    z_layers = np.asarray(z_layers)

    layer_ids_sorted = np.unique(z_layers)
    layer_ids_sorted.sort()

    positions = atoms.get_positions()
    layer_atoms = []
    layer_z = []

    for lid in layer_ids_sorted:
        idx = np.where(z_layers == lid)[0]
        layer_atoms.append(idx.tolist())
        layer_z.append(positions[idx, 2].mean())

    return layer_ids_sorted.tolist(), np.array(layer_z), layer_atoms


def compute_layer_charges(atoms, charge_map, layer_tol):
    """Compute per-z-layer charge using provided charge_map."""
    _, layer_z, layer_atoms = get_z_layer_data(atoms, layer_tol)
    q_layers = []

    for idxs in layer_atoms:
        qk = 0.0
        for i in idxs:
            sym = atoms[i].symbol
            qk += charge_map[sym]
        q_layers.append(qk)

    return np.array(q_layers), np.array(layer_z), layer_atoms


def dipole_moment_1d(q_layers, z):
    """Compute 1D dipole along z around mean z reference."""
    z0 = float(np.mean(z))
    return float(np.sum(q_layers * (z - z0)))


def check_tasker(q_layers, z, dipole_tol=1e-6):
    """
    A practical heuristic:
    - all layer charges ~0 => Type I-like
    - non-zero layer charges but total dipole ~0 => Type II-like
    - otherwise polar (Type III-ish)
    """
    if np.allclose(q_layers, 0.0, atol=1e-12):
        return True, "Type I (each z-layer neutral)", 0.0

    p = dipole_moment_1d(q_layers, z)
    if abs(p) < dipole_tol:
        return True, "Type II (dipole cancelled)", p

    return False, "Polar (Type III-ish)", p


def crop_by_z_layers(parent, layer_atoms, keep_from, keep_to):
    """Crop structure by z-layer index window [keep_from, keep_to)."""
    keep_atom_indices = []
    for k in range(keep_from, keep_to):
        keep_atom_indices.extend(layer_atoms[k])
    keep_atom_indices = sorted(set(keep_atom_indices))
    return parent[keep_atom_indices]


def tile_slab(atoms, min_x=None, min_y=None, repeat=(1, 1, 1)):
    """
    Tile slab either by target in-plane minimum size or fixed repeat.
    """
    if min_x is not None or min_y is not None:
        x_length = np.linalg.norm(atoms.cell[0])
        y_length = np.linalg.norm(atoms.cell[1])
        nx = int(math.ceil(min_x / x_length)) if min_x is not None else 1
        ny = int(math.ceil(min_y / y_length)) if min_y is not None else 1
        n_xyz = (nx, ny, 1)
        print(f"[TILE] min_x={min_x}, min_y={min_y} => repeat={n_xyz}")
    else:
        n_xyz = repeat
        print(f"[TILE] 直接重复 => repeat={n_xyz}")

    return atoms.repeat(n_xyz)


def get_output_format(filename):
    """Select ASE output format by file extension."""
    ext = os.path.splitext(filename)[1].lower()
    format_map = {
        ".vasp": "vasp",
        ".poscar": "vasp",
        ".cif": "cif",
        ".xyz": "xyz",
        ".json": "json",
        ".traj": "traj",
    }
    return format_map.get(ext, "vasp")


def estimate_layers_from_thickness(bulk, miller, thickness, vacuum):
    """Estimate repeat layers from target slab thickness (angstrom)."""
    test_slab = surface(bulk, miller, layers=2, vacuum=vacuum)
    z_min = test_slab.positions[:, 2].min()
    z_max = test_slab.positions[:, 2].max()
    single_layer_height = (z_max - z_min) / 2.0
    return int(round(thickness / single_layer_height))


def build_slab_with_tasker_fix(
    bulk,
    miller,
    repeat_layers=None,
    thickness=None,
    vacuum=15.0,
    charge_map=None,
    layer_tol=0.5,
    dipole_tol=1e-6,
    max_extra_zlayers=6,
    verbose=True,
):
    """
    Build slab and heuristically try extra layers/cropping when initial slab is polar.
    """
    if thickness is not None:
        if repeat_layers is not None:
            raise ValueError("不能同时指定 repeat_layers 和 thickness")
        repeat_layers = estimate_layers_from_thickness(bulk, miller, thickness, vacuum)
        if verbose:
            print(f"[THICKNESS] 厚度 {thickness} Å => 估计层数 {repeat_layers}")
    elif repeat_layers is None:
        raise ValueError("必须指定 repeat_layers 或 thickness")

    base = surface(bulk, miller, layers=repeat_layers, vacuum=vacuum)
    q_base, z_base, _ = compute_layer_charges(base, charge_map, layer_tol)
    ok, reason, dip = check_tasker(q_base, z_base, dipole_tol)

    if verbose:
        print(f"[BASE] repeat_layers={repeat_layers}, z_layers={len(q_base)}")
        print("  Q =", q_base)
        print("  dipole =", dip, "=>", reason)

    if ok:
        return base, {"repeat_layers": repeat_layers}

    for extra in range(1, max_extra_zlayers + 1):
        parent_repeat = repeat_layers

        while True:
            parent_repeat += 1
            parent = surface(bulk, miller, layers=parent_repeat, vacuum=vacuum)
            q_parent, _, layer_atoms = compute_layer_charges(
                parent, charge_map, layer_tol
            )
            if len(q_parent) >= len(q_base) + extra + 2:
                break

        target_len = len(q_base) + extra
        n_parent = len(q_parent)
        start = (n_parent - target_len) // 2
        windows = [
            (start, start + target_len),  # center
            (0, target_len),  # bottom-anchored
            (n_parent - target_len, n_parent),  # top-anchored
        ]

        for a, b in windows:
            cand = crop_by_z_layers(parent, layer_atoms, a, b)
            q_cand, z_cand, _ = compute_layer_charges(cand, charge_map, layer_tol)
            ok2, _, dip2 = check_tasker(q_cand, z_cand, dipole_tol=dipole_tol)

            if verbose:
                print(f"[TRY] extra={extra} window=({a},{b}) dip={dip2:.3e} ok={ok2}")

            if ok2:
                return cand, {"extra_layers": extra, "window": [a, b]}

    raise RuntimeError(
        "No nonpolar solution found by automatic Tasker fix. "
        "Please manually adjust termination/layers (or temporarily accept a polar slab)."
    )


def process_single(params: dict) -> dict:
    """
    Process a single bulk structure into a slab. Returns a result dict.

    Required keys in params:
        input (str): path to bulk structure file
        miller (list[int]): Miller indices [h, k, l]
        output (str): output file path

    Optional keys:
        repeat_layers (int), thickness (float), vacuum (float, default 15.0),
        charge (str or None), layer_tol (float, default 1.0),
        tile_repeat (list[int] or None), tile_min_x (float or None),
        tile_min_y (float or None), quiet (bool, default False)
    """
    input_path = params["input"]
    miller = params["miller"]
    output_path = params["output"]
    repeat_layers = params.get("repeat_layers")
    thickness = params.get("thickness")
    vacuum = params.get("vacuum", 15.0)
    charge = params.get("charge")
    layer_tol = params.get("layer_tol", 1.0)
    tile_repeat = params.get("tile_repeat")
    tile_min_x = params.get("tile_min_x")
    tile_min_y = params.get("tile_min_y")
    quiet = params.get("quiet", False)

    result = {
        "input": input_path,
        "output": output_path,
        "success": False,
        "error": None,
    }

    try:
        bulk = read(input_path)

        if charge is None:
            charge_map = auto_charge_map(bulk)
        else:
            charge_map = parse_charge_map(charge) if isinstance(charge, str) else charge

        slab, meta = build_slab_with_tasker_fix(
            bulk=bulk,
            miller=tuple(miller),
            repeat_layers=repeat_layers,
            thickness=thickness,
            vacuum=vacuum,
            charge_map=charge_map,
            layer_tol=layer_tol,
            verbose=(not quiet),
        )

        if tile_repeat is not None:
            slab = tile_slab(slab, repeat=tuple(tile_repeat))
        elif tile_min_x is not None or tile_min_y is not None:
            slab = tile_slab(slab, min_x=tile_min_x, min_y=tile_min_y)

        output_format = get_output_format(output_path)
        if output_format == "vasp":
            write(output_path, slab, format="vasp", vasp5=True)
        else:
            write(output_path, slab, format=output_format)

        if not quiet:
            print(f"\n[FINAL] 保存到 {output_path}")
            print(f"  格式: {output_format}")
            print(f"  原子数: {len(slab)}")
            print(f"  晶胞尺寸: {np.linalg.norm(slab.cell, axis=1)}")
            print(f"  charge_map = {charge_map}")
            print(f"  layer_tol = {layer_tol}")
            print(f"  meta = {meta}")

        result["success"] = True
        result["n_atoms"] = len(slab)
        result["meta"] = meta

    except Exception as exc:
        result["error"] = str(exc)
        print(f"[ERROR] {input_path}: 自动构建失败: {exc}")

    return result


def _auto_output_path(input_path: str, output_dir: str) -> str:
    """Generate output path in output_dir as {stem}_slab{ext}."""
    stem = os.path.splitext(os.path.basename(input_path))[0]
    ext = os.path.splitext(input_path)[1] or ".vasp"
    return os.path.join(output_dir, f"{stem}_slab{ext}")


def main():
    parser = argparse.ArgumentParser(description="生成满足 Tasker 条件的非极性 slab")

    parser.add_argument(
        "-i", "--input", nargs="+", default=["POSCAR"], help="输入结构文件（支持多个）"
    )
    parser.add_argument(
        "-m", "--miller", nargs=3, type=int, default=None, help="Miller 指数 (h k l)"
    )
    parser.add_argument("-o", "--output", default=None, help="输出文件名（单文件模式）")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="批量模式输出目录（自动命名为 {stem}_slab{ext}）",
    )

    parser.add_argument(
        "--batch", default=None, help="批量配置 JSON 文件（每条可有独立参数）"
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("-L", "--repeat-layers", type=int, help="重复层数")
    mode_group.add_argument("-T", "--thickness", type=float, help="目标厚度（Å）")

    parser.add_argument(
        "-v", "--vacuum", type=float, default=15.0, help="真空层厚度（Å）"
    )
    parser.add_argument(
        "--charge", default=None, help="电荷映射（格式：'Cu:1.0,O:-2.0' 或 JSON）"
    )
    parser.add_argument(
        "--layer-tol", type=float, default=1, help="层识别容差（默认: 0.5）"
    )

    tile_group = parser.add_mutually_exclusive_group()
    tile_group.add_argument(
        "--tile-min-x", type=float, default=None, help="x方向最小尺寸（Å）"
    )
    tile_group.add_argument(
        "--tile-min-y", type=float, default=None, help="y方向最小尺寸（Å）"
    )
    tile_group.add_argument(
        "--tile-repeat",
        nargs=3,
        type=int,
        default=None,
        metavar=("NX", "NY", "NZ"),
        help="扩胞重复次数",
    )

    parser.add_argument("--quiet", action="store_true", help="静默模式")

    args = parser.parse_args()

    # --batch mode: read JSON config, each entry has independent params
    if args.batch is not None:
        with open(args.batch, encoding="utf-8") as f:
            batch_configs = json.load(f)

        results = []
        for entry in batch_configs:
            params = {
                "input": entry["input"],
                "miller": entry.get("miller", args.miller),
                "output": entry.get("output", _auto_output_path(entry["input"], ".")),
                "repeat_layers": entry.get("repeat_layers", args.repeat_layers),
                "thickness": entry.get("thickness", args.thickness),
                "vacuum": entry.get("vacuum", args.vacuum),
                "charge": entry.get("charge", args.charge),
                "layer_tol": entry.get("layer_tol", args.layer_tol),
                "tile_repeat": entry.get(
                    "tile_repeat", list(args.tile_repeat) if args.tile_repeat else None
                ),
                "tile_min_x": entry.get("tile_min_x", args.tile_min_x),
                "tile_min_y": entry.get("tile_min_y", args.tile_min_y),
                "quiet": entry.get("quiet", args.quiet),
            }
            if params["miller"] is None:
                print(f"[ERROR] {entry['input']}: 缺少 miller 参数")
                results.append(
                    {
                        "input": entry["input"],
                        "output": params["output"],
                        "success": False,
                        "error": "缺少 miller 参数",
                    }
                )
                continue
            if params["repeat_layers"] is None and params["thickness"] is None:
                print(f"[ERROR] {entry['input']}: 必须指定 repeat_layers 或 thickness")
                results.append(
                    {
                        "input": entry["input"],
                        "output": params["output"],
                        "success": False,
                        "error": "必须指定 repeat_layers 或 thickness",
                    }
                )
                continue
            results.append(process_single(params))

        print(json.dumps({"results": results}, indent=2, ensure_ascii=False))
        any_failed = any(not r["success"] for r in results)
        sys.exit(1 if any_failed else 0)

    # Multi-file or single-file mode via -i
    input_files = args.input

    if args.miller is None:
        parser.error("-m/--miller 是必填参数（非 --batch 模式）")
    if args.repeat_layers is None and args.thickness is None:
        parser.error("必须指定 -L/--repeat-layers 或 -T/--thickness（非 --batch 模式）")

    if len(input_files) == 1 and args.batch is None:
        # Single-file mode (backward compatible)
        output_path = args.output if args.output else "POSCAR_slab"
        params = {
            "input": input_files[0],
            "miller": args.miller,
            "output": output_path,
            "repeat_layers": args.repeat_layers,
            "thickness": args.thickness,
            "vacuum": args.vacuum,
            "charge": args.charge,
            "layer_tol": args.layer_tol,
            "tile_repeat": list(args.tile_repeat) if args.tile_repeat else None,
            "tile_min_x": args.tile_min_x,
            "tile_min_y": args.tile_min_y,
            "quiet": args.quiet,
        }
        result = process_single(params)
        if not result["success"]:
            print("[ACTION] 请手动调整终止面/层数，或与用户确认是否暂时接受极性 slab。")
            sys.exit(1)
    else:
        # Multi-file mode with shared parameters
        output_dir = args.output_dir if args.output_dir else "."
        if output_dir != "." and not os.path.isdir(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        results = []
        for inp in input_files:
            output_path = _auto_output_path(inp, output_dir)
            params = {
                "input": inp,
                "miller": args.miller,
                "output": output_path,
                "repeat_layers": args.repeat_layers,
                "thickness": args.thickness,
                "vacuum": args.vacuum,
                "charge": args.charge,
                "layer_tol": args.layer_tol,
                "tile_repeat": list(args.tile_repeat) if args.tile_repeat else None,
                "tile_min_x": args.tile_min_x,
                "tile_min_y": args.tile_min_y,
                "quiet": args.quiet,
            }
            results.append(process_single(params))

        print(json.dumps({"results": results}, indent=2, ensure_ascii=False))
        any_failed = any(not r["success"] for r in results)
        sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
