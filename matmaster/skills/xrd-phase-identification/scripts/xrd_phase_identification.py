#!/usr/bin/env python3
"""Parse powder-XRD data and identify phases using the vendored reference database."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

RAW_SUFFIXES = {".xrdml", ".xy", ".asc", ".txt", ".mdi", ".raw"}
DEFAULT_SERVICE_URL = "http://221.194.152.152:8010"
DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


def _split_elements(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _require_positive(value: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _service_url() -> str:
    return os.environ.get("XRD_SERVICE_URL", DEFAULT_SERVICE_URL).rstrip("/")


def _write_artifacts(response: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    result = response.get("result")
    artifacts = response.get("artifacts")
    if not isinstance(result, dict) or not isinstance(artifacts, list):
        raise RuntimeError("XRD service returned an unexpected response format")

    output_dir.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        key = artifact.get("key")
        name = artifact.get("name")
        content = artifact.get("content")
        if not isinstance(key, str) or not isinstance(name, str) or not isinstance(content, str):
            raise RuntimeError("XRD service returned an invalid artifact")
        path = output_dir / Path(name).name
        path.write_text(content, encoding="utf-8")
        result[key] = str(path.resolve())
    return result


def _post(
    path: str,
    input_path: Path,
    form: dict[str, str | int],
    output_dir: Path,
) -> dict[str, Any]:
    with input_path.open("rb") as handle:
        files = {"file": (input_path.name, handle, "application/octet-stream")}
        try:
            response = httpx.post(
                f"{_service_url()}{path}",
                files=files,
                data=form,
                timeout=DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            raise RuntimeError(f"XRD service rejected the request: {detail}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"XRD service request failed: {exc}") from exc
    try:
        return _write_artifacts(response.json(), output_dir)
    except ValueError as exc:
        raise RuntimeError("XRD service returned invalid JSON") from exc


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

    return _post(
        "/v1/xrd/parse",
        input_path,
        {"baseline_mode": baseline_mode},
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
) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"Processed CSV not found: {input_path}")
    if input_path.suffix.lower() in RAW_SUFFIXES:
        raise ValueError(
            "Invalid input format. Run the parse subcommand before identify."
        )
    _require_positive(top_n, "top_n")
    _require_positive(show_top_n, "show_top_n")

    return _post(
        "/v1/xrd/identify",
        input_path,
        {
            "chem_include_any": ",".join(include_any),
            "chem_include_all": ",".join(include_all),
            "chem_exclude": ",".join(exclude),
            "top_n": top_n,
            "show_top_n": show_top_n,
        },
        output_dir,
    )


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
