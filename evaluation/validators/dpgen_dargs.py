"""DP-GEN dargs validators for MATTER evaluation outputs."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


def _coerce_bool(value: Any, *, name: str) -> bool:
    """Coerce YAML/JSON-friendly boolean values without bool("false") traps."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off", ""}:
            return False
    raise ValueError(f"{name} must be a boolean")


def _resolve_file(
    workspace: Path,
    name: str,
    *,
    workspace_resolve: str = "recursive",
) -> Path | None:
    """Resolve a workspace file by exact relative path, then by basename."""
    if workspace_resolve == "root":
        if len(Path(name).parts) != 1:
            return None
        direct = workspace / name
        return direct if direct.is_file() else None

    direct = workspace / name
    if direct.is_file():
        return direct
    for path in workspace.rglob(Path(name).name):
        if path.is_file():
            return path
    return None


def _load_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return None, f"{path.name} is not valid JSON: {exc}"
    return data, None


def _arginfo(kind: str) -> Any:
    try:
        from dpgen.generator.arginfo import (  # type: ignore[import-not-found]
            run_jdata_arginfo,
            run_mdata_arginfo,
        )
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "dpgen_dargs_check requires the optional DP-GEN dependency; "
            "install the calculation extra or dpgen>=0.13.3"
        ) from exc

    if kind == "param":
        return run_jdata_arginfo()
    if kind == "machine":
        return run_mdata_arginfo()
    raise ValueError("kind must be 'param' or 'machine'")


def _machine_task_payload(
    data: dict[str, Any],
    section: str,
) -> tuple[dict[str, Any] | None, str | None]:
    if section not in data:
        return None, f"machine runtime validation: missing top-level section '{section}'"
    value = data[section]
    if isinstance(value, dict):
        task = value
    elif isinstance(value, list):
        if not value:
            return None, f"machine runtime validation: section '{section}' list is empty"
        if not isinstance(value[0], dict):
            return None, (
                f"machine runtime validation: section '{section}' first list item "
                f"is {type(value[0]).__name__}, expected object"
            )
        task = value[0]
    else:
        return None, (
            f"machine runtime validation: section '{section}' is "
            f"{type(value).__name__}, expected object or non-empty list"
        )

    command = task.get("command")
    if not isinstance(command, str) or not command.strip():
        return None, (
            f"machine runtime validation: {section}.command must be a "
            "non-empty string"
        )
    machine = task.get("machine")
    if not isinstance(machine, dict):
        return None, f"machine runtime validation: {section}.machine must be an object"
    resources = task.get("resources")
    if not isinstance(resources, dict):
        return None, f"machine runtime validation: {section}.resources must be an object"
    return task, None


def _check_machine_runtime(data: Any, *, filename: str) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, f"{filename} top-level is {type(data).__name__}, expected object"

    sections = ("train", "model_devi", "fp")
    for section in sections:
        _, err = _machine_task_payload(data, section)
        if err:
            return False, f"{filename} failed DP-GEN machine runtime validation: {err}"

    try:
        from dpgen.remote.decide_machine import convert_mdata  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "dpgen_dargs_check runtime mode requires the optional DP-GEN dependency; "
            "install the calculation extra or dpgen>=0.13.3"
        ) from exc

    try:
        converted = convert_mdata(deepcopy(data))
    except Exception as exc:  # noqa: BLE001 - mirror DP-GEN runtime conversion
        return False, f"{filename} failed DP-GEN machine runtime conversion: {exc}"

    for section in sections:
        for suffix in ("command", "machine", "resources"):
            key = f"{section}_{suffix}"
            if key not in converted:
                return False, f"{filename} runtime conversion did not produce {key}"

    return True, f"{filename} passed DP-GEN machine runtime-compatible validation"


def _check_dargs_schema(
    data: Any,
    *,
    filename: str,
    kind: str,
    strict: bool,
) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, f"{filename} top-level is {type(data).__name__}, expected object"

    try:
        arg = _arginfo(kind)
        normalized = arg.normalize_value(data, trim_pattern="_*")
        arg.check_value(normalized, strict=strict)
    except Exception as exc:  # noqa: BLE001 - dargs raises several exception types
        return False, f"{filename} failed DP-GEN {kind} dargs validation: {exc}"

    return True, f"{filename} passed DP-GEN {kind} dargs validation"


def check_dpgen_dargs(
    workspace_dir: str | Path,
    *,
    filename: str,
    kind: str,
    check: str = "schema",
    strict: bool | str = False,
    workspace_resolve: str = "recursive",
) -> tuple[bool, str]:
    """Validate a DP-GEN param/machine JSON file.

    The check is intentionally non-executing: it does not run DP-GEN, submit jobs,
    or verify filesystem paths. ``check='schema'`` uses official dargs schema;
    ``check='runtime'`` is only for machine.json and mirrors DP-GEN's deprecated
    list-form compatibility at ``convert_mdata()`` level.
    """
    if not filename:
        return False, "dpgen_dargs_check: no filename provided"
    if kind not in {"param", "machine"}:
        return False, "dpgen_dargs_check: kind must be 'param' or 'machine'"
    if check not in {"schema", "dargs", "runtime"}:
        return False, "dpgen_dargs_check: check must be 'schema', 'dargs', or 'runtime'"
    if check == "runtime" and kind != "machine":
        return False, "dpgen_dargs_check: check 'runtime' is only supported for kind 'machine'"
    try:
        strict_bool = _coerce_bool(strict, name="strict")
    except ValueError as exc:
        return False, f"dpgen_dargs_check: {exc}"

    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename, workspace_resolve=workspace_resolve)
    if fpath is None:
        return False, f"{filename} not found in workspace"

    data, err = _load_json(fpath)
    if err:
        return False, err
    if data is None:
        return False, f"{filename} could not be loaded"

    if check == "runtime":
        return _check_machine_runtime(data, filename=filename)

    return _check_dargs_schema(
        data,
        filename=filename,
        kind=kind,
        strict=strict_bool,
    )
