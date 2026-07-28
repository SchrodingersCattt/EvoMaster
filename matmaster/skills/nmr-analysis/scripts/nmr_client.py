#!/usr/bin/env python3
"""Direct client for the deployed NMR inference service."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

DEFAULT_SERVICE_URL = "http://101.126.67.113:8090/sync_nmr_service_mcp"
DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=300.0)
SUPPORTED_MOLECULE_SUFFIXES = {".xyz", ".pdb", ".sdf", ".mol", ".mol2"}
DEFAULT_SOLVER_CONFIG = {
    "sigma_h": 1,
    "sigma_c": 10,
    "use_H_split": False,
    "split_coef": 0.8,
    "max_iter": 2,
    "num_search": 1000,
    "num_pool": 1000,
    "num_filter_pair": 200000,
    "num_filter_mol": 1000,
    "num_mutate_mol": 100,
    "topk": 1000,
    "use_stereo": False,
    "optional_halogens": ["F", "Cl", "Br", "I"],
    "max_cycle_length": 6,
    "invalid_patterns": ["[O][O]", "[R]=[R]=[R]", "[r3,r4]=[r3,r4]", "[O][F,Cl,Br,I]"],
    "include_active_hs": "yes",
}


def _parse_json_numbers(raw: str | None, name: str) -> list[float] | None:
    if raw is None:
        return None
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be a JSON array of finite numbers") from exc
    if not isinstance(values, list):
        raise ValueError(f"{name} must be a JSON array of finite numbers")
    try:
        parsed = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a JSON array of finite numbers") from exc
    if not all(math.isfinite(value) for value in parsed):
        raise ValueError(f"{name} must contain only finite numbers")
    return parsed


def _split_elements(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not values:
        raise ValueError("allowed elements must contain at least one element")
    return values


def _positive(value: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _download_url(url: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Molecule-file URLs must use HTTP or HTTPS")
    filename = Path(parsed.path).name or "molecule"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_MOLECULE_SUFFIXES:
        raise ValueError(
            "Unsupported molecule file format. Expected one of: "
            + ", ".join(sorted(SUPPORTED_MOLECULE_SUFFIXES))
        )
    response = httpx.get(url, follow_redirects=True, timeout=60.0)
    response.raise_for_status()
    path = Path(tempfile.mkdtemp(prefix="nmr-")) / filename
    path.write_bytes(response.content)
    return path


def _local_molecule_file(value: str) -> Path:
    if urllib.parse.urlparse(value).scheme:
        return _download_url(value)
    path = Path(value)
    if not path.is_file():
        raise FileNotFoundError(f"Molecule file not found: {path}")
    if path.suffix.lower() not in SUPPORTED_MOLECULE_SUFFIXES:
        raise ValueError(
            "Unsupported molecule file format. Expected one of: "
            + ", ".join(sorted(SUPPORTED_MOLECULE_SUFFIXES))
        )
    return path


def _smiles_from_file(value: str) -> list[str]:
    try:
        from rdkit import Chem
    except ImportError as exc:
        raise RuntimeError(
            "RDKit is required to read molecular structure files"
        ) from exc

    path = _local_molecule_file(value)
    suffix = path.suffix.lower()
    first_line = path.read_text(encoding="utf-8", errors="ignore").split("\n", 1)[0]
    if first_line.strip().isdigit():
        smiles = _smiles_from_xyz(path)
        return [smiles] if smiles else []
    if suffix == ".sdf":
        molecules = Chem.SDMolSupplier(str(path), removeHs=False)
        return [Chem.MolToSmiles(molecule) for molecule in molecules if molecule]
    if suffix == ".pdb":
        molecule = Chem.MolFromPDBFile(str(path), removeHs=False)
    elif suffix == ".mol":
        molecule = Chem.MolFromMolFile(str(path), removeHs=False)
    elif suffix == ".mol2":
        molecule = Chem.MolFromMol2File(str(path), removeHs=False)
    else:
        molecule = _smiles_from_xyz(path)
        return [molecule] if molecule else []
    return [Chem.MolToSmiles(molecule)] if molecule else []


def _smiles_from_xyz(path: Path) -> str | None:
    try:
        from rdkit import Chem
        from rdkit.Chem import rdDetermineBonds
    except ImportError as exc:
        raise RuntimeError(
            "RDKit is required to read molecular structure files"
        ) from exc

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) >= 2 and lines[1].strip().startswith("SMILES:"):
        smiles = lines[1].split("SMILES:", 1)[1].strip()
        if smiles and Chem.MolFromSmiles(smiles) is not None:
            return smiles
    molecule = Chem.MolFromXYZFile(str(path))
    if molecule is None:
        return None
    try:
        rdDetermineBonds.DetermineBonds(molecule)
    except Exception:
        return None
    return Chem.MolToSmiles(molecule)


def _molecule_files_to_smiles(values: list[str]) -> list[str]:
    all_smiles: list[str] = []
    errors: list[str] = []
    for value in values:
        try:
            all_smiles.extend(_smiles_from_file(value))
        except Exception as exc:
            errors.append(f"{value}: {exc}")
    if not all_smiles and errors:
        raise ValueError("Unable to parse molecule files: " + "; ".join(errors))
    return all_smiles


def _task_payload(input_data: dict[str, Any]) -> dict[str, Any]:
    return _drop_none({"name": "", "input_data": input_data})


def _solver_config(topk: int | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_SOLVER_CONFIG)
    if topk is not None:
        config["topk"] = topk
    return config


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_none(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _call_service(payload: dict[str, Any], service_url: str) -> dict[str, Any]:
    response = httpx.post(service_url, json=payload, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    body = response.json()
    result = body.get("data", {}).get("result")
    if not isinstance(result, dict):
        raise RuntimeError("NMR service returned an unexpected result format")
    return result


def _draw_svg(item: dict[str, Any]) -> str | None:
    try:
        from rdkit import Chem
        from rdkit.Chem import Draw
    except ImportError as exc:
        raise RuntimeError(
            "RDKit is required to generate NMR structure artifacts"
        ) from exc

    smiles = item.get("smiles_with_atom_order") or item.get("smiles")
    molecule = Chem.MolFromSmiles(smiles, sanitize=False)
    if molecule is None:
        return None
    try:
        shifts = item.get("atoms_shift") or []
        for element, precision in (("C", 1), ("H", 2)):
            notes: dict[int, list[str]] = {}
            for index, (atom, shift) in enumerate(zip(molecule.GetAtoms(), shifts)):
                if atom.GetSymbol() == element == "C":
                    notes.setdefault(index, []).append(f"{float(shift):.{precision}f}")
                elif atom.GetSymbol() == element == "H" and atom.GetNeighbors():
                    parent = atom.GetNeighbors()[0].GetIdx()
                    notes.setdefault(parent, []).append(f"{float(shift):.{precision}f}")
            for atom_index, values in notes.items():
                molecule.GetAtomWithIdx(atom_index).SetProp(
                    "atomNote", ", ".join(sorted(values, key=float))
                )
        drawer = Draw.MolDraw2DSVG(300, 300)
        options = drawer.drawOptions()
        options.setAnnotationColour((0, 0, 1))
        options.annotationFontScale = 0.6
        Draw.PrepareAndDrawMolecule(drawer, Chem.RemoveHs(molecule))
        drawer.FinishDrawing()
        return drawer.GetDrawingText()
    except Exception:
        return None


def _write_xyz(smiles: str, path: Path) -> bool:
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as exc:
        raise RuntimeError(
            "RDKit is required to generate NMR structure artifacts"
        ) from exc

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return False
    try:
        molecule = Chem.AddHs(molecule)
        if AllChem.EmbedMolecule(molecule, AllChem.ETKDG()) != 0:
            return False
        AllChem.UFFOptimizeMolecule(molecule)
        conformer = molecule.GetConformer()
        lines = [str(molecule.GetNumAtoms()), f"SMILES: {smiles}"]
        for atom in molecule.GetAtoms():
            position = conformer.GetAtomPosition(atom.GetIdx())
            lines.append(
                f"{atom.GetSymbol()} {position.x:.6f} {position.y:.6f} {position.z:.6f}"
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def _score(value: Any) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return 0.0


def _format_results(
    raw: dict[str, Any], output_dir: Path, include_scores: bool
) -> dict[str, Any]:
    code = raw.get("code", -1)
    if code != 0:
        return {
            "success": False,
            "code": code,
            "message": "NMR 计算失败",
            "detail": str(raw.get("msg") or "")[:300] or None,
            "data": None,
        }
    candidates = raw.get("data") or []
    if not isinstance(candidates, list) or not candidates:
        return {
            "success": True,
            "code": code,
            "message": "计算完成，但未找到满足条件的候选分子",
            "data": [],
            "count": 0,
        }

    svg_dir = output_dir / "svg"
    xyz_dir = output_dir / "xyz"
    svg_dir.mkdir(parents=True, exist_ok=True)
    xyz_dir.mkdir(parents=True, exist_ok=True)
    formatted: list[dict[str, Any]] = []
    best_svg: Path | None = None
    best_xyz: Path | None = None
    scored_candidates: list[tuple[float, dict[str, Any]]] = []

    for index, item in enumerate(candidates):
        if not isinstance(item, dict) or not item.get("smiles"):
            continue
        smiles = str(item["smiles"])
        basename = f"structure_{index + 1:03d}"
        svg = _draw_svg(item)
        svg_path = svg_dir / f"{basename}.svg"
        if svg:
            svg_path.write_text(svg, encoding="utf-8")
            if best_svg is None:
                best_svg = svg_path
        xyz_path = xyz_dir / f"{basename}.xyz"
        if _write_xyz(smiles, xyz_path) and best_xyz is None:
            best_xyz = xyz_path

        item_result: dict[str, Any] = {
            "smiles": smiles,
            "markdown": _markdown(smiles, item, index, include_scores),
        }
        if include_scores:
            item_result.update(
                {
                    "similarity_score": _score(item.get("score")),
                    "H_score": _score(item.get("H_score")),
                    "C_score": _score(item.get("C_score")),
                }
            )
        formatted.append(item_result)
        scored_candidates.append((_score(item.get("score")), item))

    if not formatted:
        return {
            "success": True,
            "code": code,
            "message": "计算完成，但未找到可格式化的候选分子",
            "data": [],
            "count": 0,
        }

    if include_scores:
        best_score, best = max(scored_candidates, key=lambda pair: pair[0])
        best_h_score = _score(best.get("H_score"))
        best_c_score = _score(best.get("C_score"))
        if best_h_score and best_c_score:
            message = (
                f"NMR 计算成功：共 {len(formatted)} 个候选，最佳得分 "
                f"{best_score:.3f} (H谱: {best_h_score:.3f}, C谱: {best_c_score:.3f})"
            )
        else:
            message = (
                f"NMR 计算成功：共 {len(formatted)} 个候选，最佳得分 {best_score:.3f}"
            )
    else:
        message = f"NMR 计算成功：共 {len(formatted)} 个分子"

    result: dict[str, Any] = {
        "success": True,
        "code": code,
        "message": message,
        "data": formatted,
        "count": len(formatted),
        "xyz_best_file": str(best_xyz.resolve()) if best_xyz else None,
        "svg_best_file": str(best_svg.resolve()) if best_svg else None,
    }
    if len(formatted) > 1:
        result["svg_files"] = str(svg_dir.resolve())
        result["xyz_files"] = str(xyz_dir.resolve())
    return result


def _markdown(
    smiles: str, item: dict[str, Any], index: int, include_scores: bool
) -> str:
    if not include_scores:
        return (
            f"## 分子 {index + 1}\n\n**SMILES**: `{smiles}`\n\n"
            "**说明**: 已生成 NMR 化学位移预测\n"
        )
    return (
        f"## 候选分子 {index + 1}\n\n**SMILES**: `{smiles}`\n\n"
        f"**相似度得分**: {_score(item.get('score'))}\n"
        f"- H谱得分: {_score(item.get('H_score'))}\n"
        f"- C谱得分: {_score(item.get('C_score'))}\n"
    )


def search(args: argparse.Namespace) -> dict[str, Any]:
    h_shifts = _parse_json_numbers(args.h_shifts, "--h-shifts")
    c_shifts = _parse_json_numbers(args.c_shifts, "--c-shifts")
    if h_shifts is None and c_shifts is None:
        raise ValueError("--h-shifts and --c-shifts cannot both be omitted")
    payload = _task_payload(
        {
            "search": {
                "H_shifts": h_shifts,
                "C_shifts": c_shifts,
                "allowed_elements": _split_elements(args.allowed_elements),
                "num_search": 1000,
                "topk": _positive(args.topk, "topk"),
            },
            "config": _solver_config(),
        }
    )
    return _format_results(
        _call_service(payload, _service_url()), Path(args.output_dir), True
    )


def predict(args: argparse.Namespace) -> dict[str, Any]:
    h_shifts = _parse_json_numbers(args.h_shifts, "--h-shifts")
    c_shifts = _parse_json_numbers(args.c_shifts, "--c-shifts")
    supplied = list(args.smiles or [])
    supplied.extend(_molecule_files_to_smiles(args.molecule_file or []))
    smiles_list = list(dict.fromkeys(smiles for smiles in supplied if smiles))
    if not smiles_list:
        raise ValueError("Provide at least one --smiles or --molecule-file input")
    payload = _task_payload(
        {
            "predict": {
                "smiles_list": smiles_list,
                "H_shifts": h_shifts,
                "C_shifts": c_shifts,
            },
            "config": _solver_config(),
        }
    )
    include_scores = bool(h_shifts) or bool(c_shifts)
    return _format_results(
        _call_service(payload, _service_url()), Path(args.output_dir), include_scores
    )


def reverse_predict(args: argparse.Namespace) -> dict[str, Any]:
    h_shifts = _parse_json_numbers(args.h_shifts, "--h-shifts")
    c_shifts = _parse_json_numbers(args.c_shifts, "--c-shifts")
    if h_shifts is None and c_shifts is None:
        raise ValueError("--h-shifts and --c-shifts cannot both be omitted")
    payload = _task_payload(
        {
            "reverse_predict": {
                "H_shifts": h_shifts,
                "C_shifts": c_shifts,
                "constraints": {
                    "formula": args.formula,
                    "allowed_elements": _split_elements(args.allowed_elements),
                },
            },
            "config": _solver_config(_positive(args.topk, "topk")),
        }
    )
    return _format_results(
        _call_service(payload, _service_url()), Path(args.output_dir), True
    )


def _service_url() -> str:
    return os.environ.get("NMR_SERVICE_URL", DEFAULT_SERVICE_URL)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Direct NMR inference client.")
    commands = parser.add_subparsers(dest="command", required=True)

    search_command = commands.add_parser("search", help="Search the NMR database.")
    _add_spectrum_arguments(search_command)
    search_command.add_argument("--allowed-elements", default=None)
    search_command.add_argument("--topk", type=int, default=10)
    search_command.add_argument("--output-dir", required=True)
    search_command.set_defaults(handler=search)

    predict_command = commands.add_parser("predict", help="Predict an NMR spectrum.")
    predict_command.add_argument("--smiles", action="append", default=[])
    predict_command.add_argument("--molecule-file", action="append", default=[])
    _add_spectrum_arguments(predict_command)
    predict_command.add_argument("--output-dir", required=True)
    predict_command.set_defaults(handler=predict)

    reverse_command = commands.add_parser(
        "reverse-predict", help="Infer candidate structures from an NMR spectrum."
    )
    _add_spectrum_arguments(reverse_command)
    reverse_command.add_argument("--allowed-elements", default=None)
    reverse_command.add_argument("--formula", default=None)
    reverse_command.add_argument("--topk", type=int, default=10)
    reverse_command.add_argument("--output-dir", required=True)
    reverse_command.set_defaults(handler=reverse_predict)
    return parser


def _add_spectrum_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--h-shifts", default=None, help="JSON array of 1H shifts.")
    parser.add_argument("--c-shifts", default=None, help="JSON array of 13C shifts.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        result = args.handler(args)
    except Exception as exc:
        result = {
            "success": False,
            "code": -1,
            "message": "NMR 计算失败",
            "detail": str(exc),
            "data": None,
        }
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
