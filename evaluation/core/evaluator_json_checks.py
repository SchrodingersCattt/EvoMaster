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
    workspace = evidence.workspace_root
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
    workspace = evidence.workspace_root
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


def check_tool_name_used(
    evidence: EvidenceBundle | None, ref: ReferenceAnswer
) -> tuple[bool, str]:
    """Check that the agent called a specific tool at least once."""
    if evidence is None:
        return False, 'no tool call evidence available'
    expected_tool = str(ref.value).strip()
    if not expected_tool:
        return False, 'tool_name_used: empty tool name in reference answer'
    calls = evidence.tool_calls
    found = [c for c in calls if c.tool_name == expected_tool]
    if found:
        return True, f'{expected_tool} called {len(found)} time(s)'
    all_tools = sorted({c.tool_name for c in calls})
    return False, f'{expected_tool} not found in tool calls; tools used: {all_tools}'
