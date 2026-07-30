#!/usr/bin/env python3
"""Run PXRD service workflows from a MatMaster Worker workspace."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx

RAW_SUFFIXES = {".asc", ".csv", ".dat", ".mdi", ".raw", ".txt", ".xy", ".xye", ".xrdml"}
DEFAULT_TIMEOUT = httpx.Timeout(180.0, connect=15.0)


def _split_values(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _require_positive(value: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _service_url() -> str:
    value = os.environ.get("PXRD_SERVICE_URL", "").strip()
    if not value:
        raise RuntimeError(
            "PXRD_SERVICE_URL is not configured. Set it to the approved internal "
            "XRD service endpoint."
        )
    return value.rstrip("/")


def _identity_headers() -> dict[str, str]:
    """Return trusted workload-attribution headers from the runtime environment.

    These are injected by the Worker runtime from the persisted session row.
    They must never be accepted from CLI arguments or user input.
    """
    user_id = os.environ.get("BOHRIUM_USER_ID", "").strip()
    org_id = os.environ.get("BOHRIUM_ORG_ID", "").strip()
    if not user_id or not org_id:
        raise RuntimeError(
            "XRD service requires BOHRIUM_USER_ID and BOHRIUM_ORG_ID in the "
            "runtime environment. These are injected by the Worker runtime "
            "from the authenticated session and cannot be provided manually."
        )
    return {"X-User-Id": user_id, "X-Org-Id": org_id}


def _write_artifacts(response: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    result = response.get("result")
    artifacts = response.get("artifacts")
    if not isinstance(result, dict) or not isinstance(artifacts, list):
        raise RuntimeError("XRD service returned an unexpected response format")

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_paths: dict[str, str] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise RuntimeError("XRD service returned an invalid artifact")
        key = artifact.get("key")
        name = artifact.get("name")
        content = artifact.get("content")
        if (
            not isinstance(key, str)
            or not isinstance(name, str)
            or not isinstance(content, str)
        ):
            raise RuntimeError("XRD service returned an invalid artifact")
        path = output_dir / Path(name).name
        path.write_text(content, encoding="utf-8")
        artifact_paths[key] = str(path.resolve())
    result["artifacts"] = artifact_paths
    if len(artifact_paths) == 3:
        for key, path in artifact_paths.items():
            if key == "raw_data_path" or key.endswith("_raw_data_path"):
                result["raw_data_path"] = path
            elif key == "features_path" or key.endswith("_features_path"):
                result["features_path"] = path
            elif key == "chart_option_path" or key.endswith("_chart_option_path"):
                result["chart_option_path"] = path
    return result


def _post(
    path: str,
    files: dict[str, Path],
    form: dict[str, str | int | float],
    output_dir: Path,
) -> dict[str, Any]:
    headers = _identity_headers()
    handles: dict[str, Any] = {}
    try:
        uploads = {}
        for key, input_path in files.items():
            handles[key] = input_path.open("rb")
            uploads[key] = (input_path.name, handles[key], "application/octet-stream")
        try:
            response = httpx.post(
                f"{_service_url()}{path}",
                files=uploads,
                data=form,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise RuntimeError(f"XRD service rejected the request: {detail}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"XRD service request failed: {exc}") from exc
    finally:
        for handle in handles.values():
            handle.close()
    try:
        return _write_artifacts(response.json(), output_dir)
    except ValueError as exc:
        raise RuntimeError("XRD service returned invalid JSON") from exc


def parse_pattern(
    input_path: Path,
    output_dir: Path,
    baseline_mode: str,
    profile: str = "standard",
    trace_ids: list[str] | None = None,
    wavelength: float = 1.540598,
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if input_path.suffix.lower() not in RAW_SUFFIXES:
        raise ValueError(
            "Unsupported raw XRD format. Expected one of: "
            + ", ".join(sorted(RAW_SUFFIXES))
        )
    return _post(
        "/v1/pxrd/parse",
        {"file": input_path},
        {
            "baseline_mode": baseline_mode,
            "profile": profile,
            "trace_ids": ",".join(trace_ids or []),
            "wavelength": wavelength,
        },
        output_dir,
    )


def identify_phases(
    input_path: Path,
    output_dir: Path,
    include_any: list[str],
    include_all: list[str],
    exclude: list[str],
    top_n: int,
    show_top_n: int,
    trace_ids: list[str] | None = None,
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Processed CSV not found: {input_path}")
    if input_path.suffix.lower() != ".csv":
        raise ValueError(
            "Invalid input format. Run the parse subcommand before identify."
        )
    _require_positive(top_n, "top_n")
    _require_positive(show_top_n, "show_top_n")
    return _post(
        "/v1/pxrd/identify",
        {"file": input_path},
        {
            "chem_include_any": ",".join(include_any),
            "chem_include_all": ",".join(include_all),
            "chem_exclude": ",".join(exclude),
            "top_n": top_n,
            "show_top_n": show_top_n,
            "trace_ids": ",".join(trace_ids or []),
        },
        output_dir,
    )


def simulate_pattern(
    cif_path: Path,
    output_dir: Path,
    radiation: str,
    wavelength: float | None,
    two_theta_min: float,
    two_theta_max: float,
) -> dict[str, Any]:
    if not cif_path.is_file():
        raise FileNotFoundError(f"CIF file not found: {cif_path}")
    return _post(
        "/v1/pxrd/simulate",
        {"cif": cif_path},
        _simulation_form(radiation, wavelength, two_theta_min, two_theta_max),
        output_dir,
    )


def compare_pattern(
    pattern_path: Path,
    cif_path: Path,
    output_dir: Path,
    radiation: str,
    wavelength: float | None,
    two_theta_min: float,
    two_theta_max: float,
    trace_ids: list[str],
    tolerance: float,
) -> dict[str, Any]:
    if not pattern_path.is_file():
        raise FileNotFoundError(f"Pattern file not found: {pattern_path}")
    if not cif_path.is_file():
        raise FileNotFoundError(f"CIF file not found: {cif_path}")
    form = _simulation_form(radiation, wavelength, two_theta_min, two_theta_max)
    form.update({"trace_ids": ",".join(trace_ids), "tolerance": tolerance})
    return _post(
        "/v1/pxrd/compare",
        {"pattern": pattern_path, "cif": cif_path},
        form,
        output_dir,
    )


def _simulation_form(
    radiation: str,
    wavelength: float | None,
    two_theta_min: float,
    two_theta_max: float,
) -> dict[str, str | float]:
    form: dict[str, str | float] = {
        "radiation": radiation,
        "two_theta_min": two_theta_min,
        "two_theta_max": two_theta_max,
    }
    if wavelength is not None:
        form["wavelength"] = wavelength
    return form


def _add_output_dir(command: argparse.ArgumentParser) -> None:
    command.add_argument("--output-dir", required=True, help="Artifact directory.")


def _add_simulation_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--radiation", default="cu-ka1")
    command.add_argument("--wavelength", type=float)
    command.add_argument("--two-theta-min", type=float, default=5.0)
    command.add_argument("--two-theta-max", type=float, default=90.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PXRD service workflows.")
    commands = parser.add_subparsers(dest="command", required=True)

    parse_command = commands.add_parser("parse", help="Parse raw PXRD pattern(s).")
    parse_command.add_argument("--input", required=True, help="Raw XRD input file.")
    _add_output_dir(parse_command)
    parse_command.add_argument(
        "--baseline-mode",
        default="Non_removal baseline",
        choices=["Non_removal baseline", "Removal baseline"],
    )
    parse_command.add_argument(
        "--profile", choices=["standard", "legacy"], default="standard"
    )
    parse_command.add_argument("--trace-ids", default="")
    parse_command.add_argument("--wavelength", type=float, default=1.540598)

    identify_command = commands.add_parser(
        "identify", help="Screen processed PXRD CSV trace(s) against references."
    )
    identify_command.add_argument("--input", required=True, help="Processed CSV input.")
    _add_output_dir(identify_command)
    identify_command.add_argument("--chem-include-any", default="")
    identify_command.add_argument("--chem-include-all", default="")
    identify_command.add_argument("--chem-exclude", default="")
    identify_command.add_argument("--top-n", type=int, default=5)
    identify_command.add_argument("--show-top-n", type=int, default=1)
    identify_command.add_argument("--trace-ids", default="")

    simulate_command = commands.add_parser(
        "simulate", help="Calculate an ideal PXRD stick pattern from a CIF."
    )
    simulate_command.add_argument("--cif", required=True, help="Input CIF file.")
    _add_output_dir(simulate_command)
    _add_simulation_arguments(simulate_command)

    compare_command = commands.add_parser(
        "compare", help="Compare experimental PXRD pattern(s) with a CIF pattern."
    )
    compare_command.add_argument("--input", required=True, help="Experimental pattern.")
    compare_command.add_argument("--cif", required=True, help="Input CIF file.")
    _add_output_dir(compare_command)
    _add_simulation_arguments(compare_command)
    compare_command.add_argument("--trace-ids", default="")
    compare_command.add_argument("--tolerance", type=float, default=0.2)

    # --- Composed workflow commands ---
    phase_id_command = commands.add_parser(
        "phase-id", help="Parse a raw pattern then screen against reference phases."
    )
    phase_id_command.add_argument("--input", required=True, help="Raw XRD input file.")
    _add_output_dir(phase_id_command)
    phase_id_command.add_argument(
        "--baseline-mode",
        default="Non_removal baseline",
        choices=["Non_removal baseline", "Removal baseline"],
    )
    phase_id_command.add_argument("--profile", choices=["standard", "legacy"], default="standard")
    phase_id_command.add_argument("--trace-ids", default="")
    phase_id_command.add_argument("--wavelength", type=float, default=1.540598)
    phase_id_command.add_argument("--chem-include-any", default="")
    phase_id_command.add_argument("--chem-include-all", default="")
    phase_id_command.add_argument("--chem-exclude", default="")
    phase_id_command.add_argument("--top-n", type=int, default=5)
    phase_id_command.add_argument("--show-top-n", type=int, default=1)

    validate_cif_command = commands.add_parser(
        "validate-cif", help="Parse a raw pattern then compare with a CIF model."
    )
    validate_cif_command.add_argument("--input", required=True, help="Raw XRD input file.")
    validate_cif_command.add_argument("--cif", required=True, help="CIF file to compare.")
    _add_output_dir(validate_cif_command)
    validate_cif_command.add_argument(
        "--baseline-mode",
        default="Non_removal baseline",
        choices=["Non_removal baseline", "Removal baseline"],
    )
    validate_cif_command.add_argument("--profile", choices=["standard", "legacy"], default="standard")
    validate_cif_command.add_argument("--trace-ids", default="")
    validate_cif_command.add_argument("--radiation", default="cu-ka1")
    validate_cif_command.add_argument("--two-theta-min", type=float, default=5.0)
    validate_cif_command.add_argument("--two-theta-max", type=float, default=90.0)
    validate_cif_command.add_argument("--tolerance", type=float, default=0.2)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output_dir = Path(args.output_dir)
        if args.command == "parse":
            result = parse_pattern(
                Path(args.input),
                output_dir,
                args.baseline_mode,
                args.profile,
                _split_values(args.trace_ids),
                args.wavelength,
            )
        elif args.command == "identify":
            result = identify_phases(
                Path(args.input),
                output_dir,
                _split_values(args.chem_include_any),
                _split_values(args.chem_include_all),
                _split_values(args.chem_exclude),
                args.top_n,
                args.show_top_n,
                _split_values(args.trace_ids),
            )
        elif args.command == "simulate":
            result = simulate_pattern(
                Path(args.cif),
                output_dir,
                args.radiation,
                args.wavelength,
                args.two_theta_min,
                args.two_theta_max,
            )
        elif args.command == "phase-id":
            result = _run_phase_id(args, output_dir)
        elif args.command == "validate-cif":
            result = _run_validate_cif(args, output_dir)
        else:
            result = compare_pattern(
                Path(args.input),
                Path(args.cif),
                output_dir,
                args.radiation,
                args.wavelength,
                args.two_theta_min,
                args.two_theta_max,
                _split_values(args.trace_ids),
                args.tolerance,
            )
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["status"] == "success" else 1


def _run_phase_id(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    """Composed workflow: parse → identify."""
    parse_result = parse_pattern(
        Path(args.input),
        output_dir,
        args.baseline_mode,
        args.profile,
        _split_values(args.trace_ids),
        args.wavelength,
    )
    # Find the first raw-data CSV artifact for identify input
    artifacts = parse_result.get("artifacts", {})
    csv_path = parse_result.get("raw_data_path")
    if not csv_path:
        for key, path in artifacts.items():
            if key.endswith("_raw_data_path"):
                csv_path = path
                break
    if not csv_path:
        raise RuntimeError("parse did not produce a raw_data CSV for identify.")
    identify_result = identify_phases(
        Path(csv_path),
        output_dir,
        _split_values(args.chem_include_any),
        _split_values(args.chem_include_all),
        _split_values(args.chem_exclude),
        args.top_n,
        args.show_top_n,
        _split_values(args.trace_ids),
    )
    return {
        "status": "success",
        "workflow": "phase-id",
        "parse": parse_result,
        "identify": identify_result,
    }


def _run_validate_cif(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    """Composed workflow: parse → compare with CIF."""
    parse_result = parse_pattern(
        Path(args.input),
        output_dir,
        args.baseline_mode,
        args.profile,
        _split_values(args.trace_ids),
        args.wavelength,
    )
    compare_result = compare_pattern(
        Path(args.input),
        Path(args.cif),
        output_dir,
        args.radiation,
        args.wavelength,
        args.two_theta_min,
        args.two_theta_max,
        _split_values(args.trace_ids),
        args.tolerance,
    )
    return {
        "status": "success",
        "workflow": "validate-cif",
        "parse": parse_result,
        "compare": compare_result,
    }


if __name__ == "__main__":
    raise SystemExit(main())
