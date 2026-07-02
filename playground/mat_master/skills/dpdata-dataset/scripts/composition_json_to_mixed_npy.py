#!/usr/bin/env python3
"""
Build per-composition DeepMD `deepmd/npy/mixed` datasets from composition JSON rows.

This is intended for alloy property finetuning when the experimental input is a
table / JSON of measured compositions plus scalar labels, rather than pre-built
DeepMD datasets.

Example:
  python composition_json_to_mixed_npy.py \
    --input ./iter03_experimental_batch.json \
    --type-map-file ./type_map.txt \
    --template-fcc ./struct_template/fcc-Ni_mp-23_conventional_standard.cif \
    --out-root ./new_data_iter03 \
    --property-key TEC \
    --seeds 10
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import dpdata
import numpy as np
from pymatgen.core import Element, Structure


DEFAULT_SUPERCELLS: dict[str, tuple[int, int, int]] = {
    "fcc": (5, 5, 5),
    "bcc": (6, 6, 6),
    "hcp": (6, 6, 6),
}


def parse_supercell(value: str) -> tuple[int, int, int]:
    parts = [int(x.strip()) for x in value.split(",") if x.strip()]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("supercell must be three comma-separated ints")
    return tuple(parts)  # type: ignore[return-value]


def load_type_map(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def composition_to_molar(
    composition: dict[str, float], type_map: list[str], fraction_mode: str
) -> list[float]:
    values: list[float] = []
    for symbol in type_map:
        amount = float(composition.get(symbol, 0.0) or 0.0)
        if fraction_mode == "mass" and amount > 0:
            amount = float(amount / Element(symbol).atomic_mass)
        values.append(amount)
    return values


def normalize_counts(values: list[float], total: int) -> list[int]:
    if total <= 0:
        raise ValueError("total atom count must be positive")
    if not values or sum(values) <= 0:
        raise ValueError("composition contains no positive entries")

    total_value = sum(values)
    counts = [int(round(v / total_value * total)) for v in values]

    for idx, value in enumerate(values):
        if value > 0 and counts[idx] == 0:
            counts[idx] = 1

    diff = sum(counts) - total
    while diff != 0:
        candidates = [i for i, c in enumerate(counts) if c > 1]
        max_idx = max(candidates or range(len(counts)), key=lambda i: counts[i])
        if diff > 0:
            counts[max_idx] -= 1
            diff -= 1
        else:
            counts[max_idx] += 1
            diff += 1

    if sum(counts) != total:
        raise ValueError(f"failed to normalize counts to {total}: got {sum(counts)}")
    return counts


def load_template(path: Path, supercell: tuple[int, int, int]) -> Structure:
    structure = Structure.from_file(str(path))
    structure.make_supercell(supercell)
    return structure


def substitute_structure(
    template: Structure, type_map: list[str], counts: list[int], seed: int
) -> Structure:
    rng = np.random.default_rng(seed)
    structure = template.copy()
    natoms = len(structure)
    if sum(counts) != natoms:
        raise ValueError(f"sum(counts)={sum(counts)} != natoms={natoms}")

    available = np.arange(natoms)
    for symbol, count in zip(type_map, counts):
        if count <= 0:
            continue
        chosen = rng.choice(available, size=count, replace=False)
        for idx in chosen.tolist():
            structure.replace(int(idx), Element(symbol))
        available = np.setdiff1d(available, chosen)
    return structure


def formula_token(type_map: list[str], counts: list[int]) -> str:
    parts = [f"{symbol}{count}" for symbol, count in zip(type_map, counts) if count > 0]
    return "_".join(parts) if parts else "empty"


def labeled_system(
    structure: Structure, type_map: list[str], prop: float
) -> dpdata.LabeledSystem:
    dp_sys = dpdata.System(structure, fmt="pymatgen/structure", type_map=type_map)
    dp_sys.data["energies"] = np.array([prop], dtype=np.float64)
    dp_sys.data["forces"] = np.zeros_like(dp_sys.data["coords"])
    return dpdata.LabeledSystem(data=dp_sys.data, type_map=type_map)


def ensure_property_files(root: Path) -> None:
    for energy_file in root.rglob("energy.npy"):
        prop_file = energy_file.with_name("property.npy")
        if not prop_file.exists():
            shutil.copyfile(energy_file, prop_file)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--type-map-file", type=Path, required=True)
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--template-fcc", type=Path, default=None)
    ap.add_argument("--template-bcc", type=Path, default=None)
    ap.add_argument("--template-hcp", type=Path, default=None)
    ap.add_argument("--fcc-supercell", type=parse_supercell, default=DEFAULT_SUPERCELLS["fcc"])
    ap.add_argument("--bcc-supercell", type=parse_supercell, default=DEFAULT_SUPERCELLS["bcc"])
    ap.add_argument("--hcp-supercell", type=parse_supercell, default=DEFAULT_SUPERCELLS["hcp"])
    ap.add_argument("--composition-key", default="composition")
    ap.add_argument("--property-key", default="TEC")
    ap.add_argument("--phase-key", default="phase")
    ap.add_argument("--id-key", default="id")
    ap.add_argument("--fraction-mode", choices=["molar", "mass"], default="molar")
    ap.add_argument("--seeds", type=int, default=10)
    args = ap.parse_args()

    type_map = load_type_map(args.type_map_file)
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    args.out_root.mkdir(parents=True, exist_ok=True)

    template_paths: dict[str, Path] = {
        "fcc": args.template_fcc,
        "bcc": args.template_bcc,
        "hcp": args.template_hcp,
    }
    supercells: dict[str, tuple[int, int, int]] = {
        "fcc": args.fcc_supercell,
        "bcc": args.bcc_supercell,
        "hcp": args.hcp_supercell,
    }
    templates: dict[str, Structure] = {}
    manifest: list[dict[str, object]] = []

    for row_idx, row in enumerate(rows):
        phase = str(row.get(args.phase_key, "fcc")).lower()
        if phase not in template_paths or template_paths[phase] is None:
            raise ValueError(f"no template configured for phase={phase!r}")
        if phase not in templates:
            templates[phase] = load_template(template_paths[phase], supercells[phase])  # type: ignore[arg-type]

        composition = row[args.composition_key]
        prop = float(row[args.property_key])
        sample_id = str(row.get(args.id_key, f"row{row_idx:03d}")).replace("/", "_")

        molar_values = composition_to_molar(composition, type_map, args.fraction_mode)
        natoms = len(templates[phase])
        counts = normalize_counts(molar_values, natoms)
        token = formula_token(type_map, counts)
        out_dir = args.out_root / f"{sample_id}_{token}_{phase}"

        ms = dpdata.MultiSystems()
        for seed_idx in range(args.seeds):
            structure = substitute_structure(
                templates[phase], type_map, counts, seed=1000 * row_idx + seed_idx
            )
            ms.append(labeled_system(structure, type_map, prop))

        out_dir.mkdir(parents=True, exist_ok=True)
        ms.to_deepmd_npy_mixed(str(out_dir))
        ensure_property_files(out_dir)

        manifest.append(
            {
                "id": sample_id,
                "phase": phase,
                "property": prop,
                "natoms": natoms,
                "counts": {symbol: count for symbol, count in zip(type_map, counts) if count > 0},
                "output_dir": str(out_dir),
            }
        )
        print(f"Wrote {out_dir}")

    manifest_path = args.out_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote manifest {manifest_path}")


if __name__ == "__main__":
    main()
