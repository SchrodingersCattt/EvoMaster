"""DP-GEN dargs validators for MATTER evaluation outputs."""

from __future__ import annotations

import json
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


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return None, f"{path.name} is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"{path.name} top-level is {type(data).__name__}, expected object"
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


def check_dpgen_dargs(
    workspace_dir: str | Path,
    *,
    filename: str,
    kind: str,
    check: str = "schema",
    strict: bool | str = False,
    workspace_resolve: str = "recursive",
) -> tuple[bool, str]:
    """Validate a DP-GEN param/machine JSON file with official dargs schema.

    The check is intentionally non-executing: it does not run DP-GEN, submit jobs,
    or verify filesystem paths. With ``strict=False`` it still catches required
    keys, types, and invalid variants while allowing harmless extra keys/comments.
    """
    if not filename:
        return False, "dpgen_dargs_check: no filename provided"
    if kind not in {"param", "machine"}:
        return False, "dpgen_dargs_check: kind must be 'param' or 'machine'"
    if check not in {"schema", "dargs"}:
        return False, "dpgen_dargs_check: check must be 'schema' or 'dargs'"
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
    assert data is not None

    try:
        arg = _arginfo(kind)
        normalized = arg.normalize_value(data, trim_pattern="_*")
        arg.check_value(normalized, strict=strict_bool)
    except Exception as exc:  # noqa: BLE001 - dargs raises several exception types
        return False, f"{filename} failed DP-GEN {kind} dargs validation: {exc}"

    return True, f"{filename} passed DP-GEN {kind} dargs validation"
