#!/usr/bin/env python3
"""Parse powder-XRD data and identify phases using the vendored reference database."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
VENDOR_DIR = SKILL_DIR / "vendor"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

RAW_SUFFIXES = {".xrdml", ".xy", ".asc", ".txt", ".mdi", ".raw"}


def _load_vendor() -> tuple[Any, Any, Any, Any]:
    from xrd_core.adapter import InMemoryXRDResult
    from xrd_core.parse import analyze_data, parse_file
    from xrd_core.search_element import search_elements
    from xrd_core.vis import XRDVis

    return InMemoryXRDResult, analyze_data, parse_file, (search_elements, XRDVis)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _split_elements(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _require_positive(value: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def parse_pattern(
    input_path: Path, output_dir: Path, baseline_mode: str
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if input_path.suffix.lower() not in RAW_SUFFIXES:
        raise ValueError(
            "Unsupported raw XRD format. Expected one of: "
            + ", ".join(sorted(RAW_SUFFIXES))
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _, analyze_data, parse_file, (_, XRDVis) = _load_vendor()
    parsed = parse_file(input_path.name, input_path.read_bytes())
    if parsed is None:
        raise RuntimeError("Parser returned no data")
    analyzed = analyze_data(input_path.name, parsed)
    data = analyzed.get("data") or []
    features = analyzed.get("features") or []
    if len(data) < 3 or not data[0] or not features:
        raise RuntimeError("Analysis returned empty data or features")

    import pandas as pd

    base_name = input_path.stem
    raw_path = output_dir / f"{base_name}_raw_data.csv"
    features_path = output_dir / f"{base_name}_features.csv"
    chart_path = output_dir / f"{base_name}_chart_option.echarts"
    pd.DataFrame({"2Theta": data[0], "Intensity": data[1], "Baseline": data[2]}).to_csv(
        raw_path, index=False
    )
    pd.DataFrame(
        features,
        columns=["2Theta[°]", "Intensity(a.u.)", "FWHM", "Grain size"],
    ).to_csv(features_path, index=False)
    _write_json(chart_path, XRDVis({"data": data}).get_echart_option(baseline_mode))

    return {
        "status": "success",
        "file_name": input_path.name,
        "peaks_count": len(features),
        "scan_range": f"{min(data[0]):.2f} - {max(data[0]):.2f}",
        "raw_data_path": str(raw_path.resolve()),
        "features_path": str(features_path.resolve()),
        "chart_option_path": str(chart_path.resolve()),
    }


async def identify_phases_async(
    input_path: Path,
    output_dir: Path,
    include_any: list[str],
    include_all: list[str],
    exclude: list[str],
    top_n: int,
    show_top_n: int,
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Processed CSV not found: {input_path}")
    if input_path.suffix.lower() in RAW_SUFFIXES:
        raise ValueError(
            "Invalid input format. Run the parse subcommand before identify."
        )
    _require_positive(top_n, "top_n")
    _require_positive(show_top_n, "show_top_n")

    import pandas as pd

    frame = pd.read_csv(input_path)
    required_columns = {"2Theta", "Intensity"}
    if not required_columns.issubset(frame.columns):
        raise ValueError(
            "Invalid CSV format. Expected columns '2Theta' and 'Intensity'."
        )
    x = frame["2Theta"].tolist()
    y = frame["Intensity"].tolist()
    if not x or not y:
        raise ValueError("Processed CSV contains no diffraction data")

    output_dir.mkdir(parents=True, exist_ok=True)
    InMemoryXRDResult, _, _, (search_elements, _) = _load_vendor()
    file_name = input_path.stem.replace("_raw_data", "")
    chemistry = [False, include_any, include_all, exclude]
    result = InMemoryXRDResult({file_name: {"data": [x, y]}})
    key = f"cli_{uuid.uuid4().hex}"
    search_result = await search_elements(chemistry, [x, y], file_name, result, key, [])
    all_phases = search_result[0]
    if not all_phases:
        return {
            "status": "success",
            "message": "No matching phases found.",
            "top_phases": [],
        }

    top_phases = all_phases[:top_n]
    count_to_plot = min(len(all_phases), show_top_n)
    plot_result = await search_elements(
        chemistry,
        [x, y],
        file_name,
        result,
        f"{key}_plot",
        list(range(count_to_plot)),
    )
    top_path = output_dir / f"{file_name}_top{top_n}_phases.csv"
    all_path = output_dir / f"{file_name}_all_phases.csv"
    chart_path = output_dir / f"{file_name}_phase_id_chart.echarts"
    pd.DataFrame(top_phases).to_csv(top_path, index=False)
    pd.DataFrame(all_phases).to_csv(all_path, index=False)
    _write_json(chart_path, plot_result[2])
    return {
        "status": "success",
        "message": (
            f"Identified {len(all_phases)} phases. Top {len(top_phases)} matches "
            "extracted."
        ),
        "top_phases": top_phases,
        "top_phases_csv_path": str(top_path.resolve()),
        "all_phases_path": str(all_path.resolve()),
        "chart_option_path": str(chart_path.resolve()),
    }


def identify_phases(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(identify_phases_async(**kwargs))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse XRD files and identify phases.")
    commands = parser.add_subparsers(dest="command", required=True)

    parse_command = commands.add_parser("parse", help="Parse a raw XRD file.")
    parse_command.add_argument("--input", required=True, help="Raw XRD input file.")
    parse_command.add_argument(
        "--output-dir", required=True, help="Artifact directory."
    )
    parse_command.add_argument(
        "--baseline-mode",
        default="Non_removal baseline",
        choices=["Non_removal baseline", "Removal baseline"],
    )

    identify_command = commands.add_parser(
        "identify", help="Identify phases from a processed CSV."
    )
    identify_command.add_argument("--input", required=True, help="Processed CSV input.")
    identify_command.add_argument(
        "--output-dir", required=True, help="Artifact directory."
    )
    identify_command.add_argument("--chem-include-any", default="")
    identify_command.add_argument("--chem-include-all", default="")
    identify_command.add_argument("--chem-exclude", default="")
    identify_command.add_argument("--top-n", type=int, default=5)
    identify_command.add_argument("--show-top-n", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "parse":
            result = parse_pattern(
                Path(args.input), Path(args.output_dir), args.baseline_mode
            )
        else:
            result = identify_phases(
                input_path=Path(args.input),
                output_dir=Path(args.output_dir),
                include_any=_split_elements(args.chem_include_any),
                include_all=_split_elements(args.chem_include_all),
                exclude=_split_elements(args.chem_exclude),
                top_n=args.top_n,
                show_top_n=args.show_top_n,
            )
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
