"""JSON file and tool-name verify helpers for the MATTER evaluator."""

from __future__ import annotations

import json
from pathlib import Path

from .evidence import EvidenceBundle
from .schemas import ReferenceAnswer


def check_json_file_schema(
    evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    """Check that a JSON file is valid and contains required keys."""
    if evidence is None:
        return False, 'no EvidenceBundle provided'
    cfg = ref.value if isinstance(ref.value, dict) else {}
    filename = cfg.get('filename', '')
    required_keys = cfg.get('required_keys', [])
    if not filename:
        return False, 'json_file_schema: no filename in reference answer'
    workspace = evidence.workspace_dir
    if not workspace:
        return False, 'no workspace root'
    fpath = Path(workspace) / filename
    if not fpath.exists():
        for p in Path(workspace).rglob(filename):
            fpath = p
            break
        else:
            return False, f'{filename} not found in workspace'
    try:
        data = json.loads(fpath.read_text(encoding='utf-8'))
    except ValueError as exc:
        return False, f'{filename} is not valid JSON: {exc}'
    if not isinstance(data, dict):
        return False, f'{filename} top-level is {type(data).__name__}, expected object'
    missing = [k for k in required_keys if k not in data]
    if missing:
        return False, f'{filename} missing keys: {missing}'
    return True, f'{filename} valid JSON with all {len(required_keys)} required keys'


def check_json_file_numeric_range(
    evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    """Check a numeric value inside a JSON file is within expected range."""
    if evidence is None:
        return False, 'no EvidenceBundle provided'
    cfg = ref.value if isinstance(ref.value, dict) else {}
    filename = cfg.get('filename', '')
    key_path = cfg.get('key', '')
    expected = cfg.get('expected')
    tolerance = cfg.get('tolerance', 0.0)
    if not filename or not key_path or expected is None:
        return False, 'json_file_numeric_range: need filename, key, expected in ref'
    workspace = evidence.workspace_dir
    if not workspace:
        return False, 'no workspace root'
    fpath = Path(workspace) / filename
    if not fpath.exists():
        for p in Path(workspace).rglob(filename):
            fpath = p
            break
        else:
            return False, f'{filename} not found in workspace'
    try:
        data = json.loads(fpath.read_text(encoding='utf-8'))
    except ValueError as exc:
        return False, f'{filename} is not valid JSON: {exc}'
    parts = key_path.split('.')
    val = data
    for part in parts:
        if isinstance(val, dict) and part in val:
            val = val[part]
        elif isinstance(val, list) and part.isdigit() and int(part) < len(val):
            val = val[int(part)]
        else:
            return False, f'key path {key_path!r} not found in {filename}'
    try:
        val = float(val)
    except (TypeError, ValueError):
        return False, f'{key_path} = {val!r} is not numeric'
    diff = abs(val - float(expected))
    if diff <= float(tolerance):
        return True, f'{key_path} = {val} (expected {expected} ± {tolerance})'
    return (
        False,
        f'{key_path} = {val} outside range {expected} ± {tolerance} (diff={diff:.4f})',
    )


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


def _resolve_file(workspace: Path, name: str) -> Path | None:
    """Try direct child first, then recursive glob."""
    direct = workspace / name
    if direct.is_file():
        return direct
    for p in workspace.rglob(Path(name).name):
        if p.is_file():
            return p
    return None


def check_json_file_artifacts(
    evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    """Check that files referenced inside a JSON array actually exist in workspace.

    ``ref.value`` schema::

        filename: candidate_structures.json
        path_key: structure_name       # key in each entry holding a filename
        entries_key: ''                # optional: navigate to array inside a top-level object
        expected_count: 10
        count_tolerance: 0
        count_mode: at_least           # "at_least" (default) or "exact"
        file_check: null               # reserved for future per-file content validation
    """
    if evidence is None:
        return False, 'no EvidenceBundle provided'
    cfg = ref.value if isinstance(ref.value, dict) else {}
    filename = cfg.get('filename', '')
    path_key = cfg.get('path_key', '')
    if not filename or not path_key:
        return False, 'json_file_artifacts: need filename and path_key in ref'
    entries_key = cfg.get('entries_key', '')
    expected_count = int(cfg.get('expected_count', 0))
    count_tolerance = int(cfg.get('count_tolerance', 0))
    count_mode = cfg.get('count_mode', 'at_least')  # "at_least" or "exact"

    workspace = evidence.workspace_dir
    if not workspace:
        return False, 'no workspace root'
    ws = Path(workspace)
    fpath = ws / filename
    if not fpath.exists():
        for p in ws.rglob(filename):
            fpath = p
            break
        else:
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
