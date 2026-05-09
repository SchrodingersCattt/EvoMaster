"""Write a normalized input_prep_manifest.json for prepared engine inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _as_list(values: list[str] | None) -> list[str]:
    return [str(v) for v in values or []]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _diagnostic_counts(data: dict[str, Any]) -> dict[str, int]:
    diagnostics = data.get("diagnostics", [])
    if isinstance(diagnostics, dict):
        return {
            "errors": int(diagnostics.get("errors", 0) or 0),
            "warnings": int(diagnostics.get("warnings", 0) or 0),
            "blockers": int(diagnostics.get("blockers", 0) or 0),
        }
    errors = 0
    warnings = 0
    blockers = 0
    if isinstance(diagnostics, list):
        for item in diagnostics:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity", "")).lower()
            if severity == "error":
                errors += 1
                blockers += 1
            elif severity == "warning":
                warnings += 1
            elif severity == "blocker":
                blockers += 1
    if isinstance(data.get("blockers"), list):
        blockers = max(blockers, len(data["blockers"]))
    if isinstance(data.get("errors"), list):
        errors = max(errors, len(data["errors"]))
    if isinstance(data.get("warnings"), list):
        warnings = max(warnings, len(data["warnings"]))
    return {"errors": errors, "warnings": warnings, "blockers": blockers}


def _parse_submit_ready(value: str, counts: dict[str, int]) -> bool:
    lowered = value.lower().strip()
    if lowered == "auto":
        return counts["errors"] == 0 and counts["blockers"] == 0
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    raise ValueError("--submit-ready must be true, false, or auto")


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    input_dir = Path(args.input_dir)
    diagnosis_path = Path(args.diagnosis) if args.diagnosis else None
    diagnosis_data = _load_json(diagnosis_path) if diagnosis_path else {}
    counts = _diagnostic_counts(diagnosis_data)
    submit_ready = _parse_submit_ready(args.submit_ready, counts)

    diagnostics = {
        "file": diagnosis_path.name if diagnosis_path else "",
        **counts,
    }
    return {
        "software": args.software,
        "task": args.task,
        "input_dir": input_dir.name,
        "generated_files": _as_list(args.generated_file),
        "user_provided_files": _as_list(args.user_provided_file),
        "diagnostics": diagnostics,
        "auxiliary_files": _as_list(args.auxiliary_file),
        "assumptions": _as_list(args.assumption),
        "submit_ready": submit_ready,
        "bohrium_command": args.bohrium_command or "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write a normalized input_prep_manifest.json."
    )
    parser.add_argument("--software", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--diagnosis", default="")
    parser.add_argument("--generated-file", action="append", default=[])
    parser.add_argument("--user-provided-file", action="append", default=[])
    parser.add_argument("--auxiliary-file", action="append", default=[])
    parser.add_argument("--assumption", action="append", default=[])
    parser.add_argument(
        "--submit-ready", choices=["true", "false", "auto"], default="auto"
    )
    parser.add_argument("--bohrium-command", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    manifest = build_manifest(args)
    output = (
        Path(args.output)
        if args.output
        else Path(args.input_dir) / "input_prep_manifest.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(str(output))


if __name__ == "__main__":
    main()
