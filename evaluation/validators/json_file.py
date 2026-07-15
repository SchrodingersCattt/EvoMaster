"""JSON file validators: schema, numeric range, key-value, and artifact checks.

Pure functions — accept workspace_dir and parameters, not EvidenceBundle.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for


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
    schema: dict[str, object] | bool | None,
) -> tuple[bool, str]:
    """Validate a JSON file against a standard JSON Schema."""
    if not filename:
        return False, 'json_file_schema: no filename provided'
    if not isinstance(schema, (dict, bool)):
        return False, 'json_file_schema: no valid schema provided'
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'{filename} not found in workspace'
    try:
        data = json.loads(fpath.read_text(encoding='utf-8'))
    except ValueError as exc:
        return False, f'{filename} is not valid JSON: {exc}'

    validator_cls = validator_for(schema)
    try:
        validator_cls.check_schema(schema)
    except SchemaError as exc:
        return False, f'json_file_schema: invalid schema: {exc.message}'

    errors = sorted(
        validator_cls(schema).iter_errors(data),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        shown: list[str] = []
        for error in errors[:5]:
            path = '$'
            for part in error.absolute_path:
                if isinstance(part, int):
                    path += f'[{part}]'
                else:
                    path += f'.{part}'
            shown.append(f'{path}: {error.message}')
        suffix = f'; {len(errors) - 5} more error(s)' if len(errors) > 5 else ''
        return False, f'{filename} failed JSON Schema: {"; ".join(shown)}{suffix}'
    return True, f'{filename} is valid JSON matching the configured schema'


def check_json_file_numeric_range(
    workspace_dir: str | Path,
    *,
    filename: str,
    key: str,
    expected: float | None = None,
    tolerance: float = 0.0,
    min: float | None = None,
    max: float | None = None,
) -> tuple[bool, str]:
    """Check a numeric value inside a JSON file is within range.

    Supports two modes:
    - ``expected`` + ``tolerance``: value must be within expected ± tolerance
    - ``min`` / ``max``: value must be within [min, max] (inclusive)

    If both are provided, ``min``/``max`` takes precedence.
    """
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

    if min is not None or max is not None:
        lo = float(min) if min is not None else float('-inf')
        hi = float(max) if max is not None else float('inf')
        if lo <= val <= hi:
            return True, f'{key} = {val} (within [{lo}, {hi}])'
        return False, f'{key} = {val} outside [{lo}, {hi}]'

    if expected is None:
        return False, "json_file_numeric_range: missing 'expected' in ref"
    diff = abs(val - float(expected))
    if diff <= float(tolerance):
        return True, f'{key} = {val} (expected {expected} ± {tolerance})'
    return (
        False,
        f'{key} = {val} outside range {expected} ± {tolerance} (diff={diff:.4f})',
    )


def check_json_file_key_values(
    workspace_dir: str | Path,
    *,
    filename: str,
    checks: list[dict[str, object]],
) -> tuple[bool, str]:
    """Check that specific keys in a JSON file contain expected substrings or match patterns.

    Each check dict has:
      - key: dot-path to the value (e.g. "image" or "config.machine")
      - contains: substring that must appear in the string value (case-insensitive)
      - pattern: regex the string value must match (optional, alternative to contains)
    """

    if not filename:
        return False, "json_file_key_values: no filename provided"
    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f"no file matching '{filename}' in {workspace_dir}"
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except ValueError as exc:
        return False, f"{filename} is not valid JSON: {exc}"

    passed: list[str] = []
    failed: list[str] = []
    for chk in checks or []:
        key = chk.get("key", "")
        val = _traverse_dotted(data, key)
        if val is None:
            failed.append(f"key '{key}' not found")
            continue
        val_str = str(val)
        contains = chk.get("contains")
        pattern = chk.get("pattern")
        if contains is not None:
            if str(contains).lower() in val_str.lower():
                passed.append(f"{key}: contains '{contains}'")
            else:
                failed.append(f"{key}: '{contains}' not in '{val_str}'")
        elif pattern is not None:
            if re.search(str(pattern), val_str):
                passed.append(f"{key}: matches pattern")
            else:
                failed.append(f"{key}: pattern not matched in '{val_str}'")
        else:
            passed.append(f"{key}: exists")

    if failed:
        return False, f"{filename}: {'; '.join(failed)}"
    return True, f"{filename}: all {len(passed)} checks passed"


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
