"""JSON file validators: schema, numeric range, and artifact checks.

Pure functions — accept workspace_dir and parameters, not EvidenceBundle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _resolve_file(workspace: Path, name: str) -> Path | None:
    """Try direct child first, then recursive glob."""
    direct = workspace / name
    if direct.is_file():
        return direct
    for p in workspace.rglob(Path(name).name):
        if p.is_file():
            return p
    return None


def _traverse_dotted(obj: object, dotted_key: str) -> object | None:
    """Navigate into *obj* along a dot-separated key path."""
    val = obj
    for part in dotted_key.split('.'):
        if isinstance(val, dict) and part in val:
            val = val[part]
        elif isinstance(val, list) and part.isdigit() and int(part) < len(val):
            val = val[int(part)]
        else:
            return None
    return val


def check_json_file_schema(
    workspace_dir: str | Path,
    *,
    filename: str,
    required_keys: list[str] | None = None,
) -> tuple[bool, str]:
    """Check that a JSON file is valid and contains required keys."""
    if not filename:
        return False, 'json_file_schema: no filename provided'
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'{filename} not found in workspace'
    try:
        data = json.loads(fpath.read_text(encoding='utf-8'))
    except ValueError as exc:
        return False, f'{filename} is not valid JSON: {exc}'
    if not isinstance(data, dict):
        return False, f'{filename} top-level is {type(data).__name__}, expected object'
    keys = required_keys or []
    missing = [k for k in keys if k not in data]
    if missing:
        return False, f'{filename} missing keys: {missing}'
    return True, f'{filename} valid JSON with all {len(keys)} required keys'


def check_json_file_numeric_range(
    workspace_dir: str | Path,
    *,
    filename: str,
    key: str,
    expected: float,
    tolerance: float = 0.0,
) -> tuple[bool, str]:
    """Check a numeric value inside a JSON file is within expected range."""
    if not filename or not key:
        return False, 'json_file_numeric_range: need filename and key'
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'{filename} not found in workspace'
    try:
        data = json.loads(fpath.read_text(encoding='utf-8'))
    except ValueError as exc:
        return False, f'{filename} is not valid JSON: {exc}'
    val = _traverse_dotted(data, key)
    if val is None:
        return False, f'key path {key!r} not found in {filename}'
    try:
        val = float(val)
    except (TypeError, ValueError):
        return False, f'{key} = {val!r} is not numeric'
    diff = abs(val - float(expected))
    if diff <= float(tolerance):
        return True, f'{key} = {val} (expected {expected} ± {tolerance})'
    return (
        False,
        f'{key} = {val} outside range {expected} ± {tolerance} (diff={diff:.4f})',
    )


def check_json_file_artifacts(
    workspace_dir: str | Path,
    *,
    filename: str,
    path_key: str,
    entries_key: str = '',
    expected_count: int = 0,
    count_tolerance: int = 0,
    count_mode: str = 'at_least',
) -> tuple[bool, str]:
    """Check that files referenced inside a JSON array exist in workspace."""
    if not filename or not path_key:
        return False, 'json_file_artifacts: need filename and path_key'
    ws = Path(workspace_dir)
    fpath = _resolve_file(ws, filename)
    if fpath is None:
        return False, f'{filename} not found in workspace'
    try:
        data = json.loads(fpath.read_text(encoding='utf-8'))
    except ValueError as exc:
        return False, f'{filename} is not valid JSON: {exc}'

    entries: list[object]
    if entries_key:
        nav = _traverse_dotted(data, entries_key)
        if not isinstance(nav, list):
            return False, f'{entries_key!r} in {filename} is not an array'
        entries = nav
    elif isinstance(data, list):
        entries = data
    else:
        return False, f'{filename} top-level is {type(data).__name__}, expected array'

    found: list[str] = []
    missing: list[str] = []
    for entry in entries:
        val = _traverse_dotted(entry, path_key)
        if not isinstance(val, str) or not val:
            continue
        resolved = _resolve_file(ws, val)
        if resolved is not None:
            found.append(val)
        else:
            missing.append(val)

    if count_mode == 'exact':
        ok = abs(len(found) - expected_count) <= count_tolerance
    else:
        ok = len(found) >= expected_count - count_tolerance
    parts = [f'{len(found)} artifact(s) found']
    if missing:
        show = missing[:5]
        tail = "..." if len(missing) > 5 else ""
        parts.append(f'{len(missing)} missing ({", ".join(show)}{tail})')
    parts.append(f'expected {expected_count}±{count_tolerance}')
    return ok, '; '.join(parts)
