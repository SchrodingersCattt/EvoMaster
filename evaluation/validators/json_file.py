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


def _walk_json(value: object):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _is_positive_id(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    return isinstance(value, str) and value.isdigit() and int(value) > 0


def _collect_json_identifiers(value: object) -> set[int]:
    identifiers: set[int] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            normalised = re.sub(r'[^a-z0-9]', '', str(key).lower())
            key_is_identifier = normalised in {'id', 'ids'} or normalised.endswith(
                (
                    'identifier',
                    'identifiers',
                    'bohrid',
                    'bohrids',
                    'jobid',
                    'jobids',
                    'taskid',
                    'taskids',
                )
            )
            if key_is_identifier:
                identifiers.update(
                    int(candidate)
                    for candidate in _walk_json(child)
                    if _is_positive_id(candidate)
                )
            else:
                identifiers.update(_collect_json_identifiers(child))
    elif isinstance(value, list):
        for child in value:
            identifiers.update(_collect_json_identifiers(child))
    return identifiers


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


def check_bohr_job_stop_record(
    workspace_dir: str | Path,
    *,
    filename: str,
    image: str,
    machine_type: str,
    command: str,
    job_name_prefix: str,
) -> tuple[bool, str]:
    """Validate a Bohr job-stop record without prescribing its JSON layout."""
    if not all((filename, image, machine_type, command, job_name_prefix)):
        return False, 'bohr_job_stop_record: incomplete verifier configuration'

    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'{filename} not found in workspace'
    try:
        data = json.loads(fpath.read_text(encoding='utf-8'))
    except ValueError as exc:
        return False, f'{filename} is not valid JSON: {exc}'

    def _normalise_key(value: object) -> str:
        return re.sub(r'[^a-z0-9]', '', str(value).lower())

    strings = [value.strip() for value in _walk_json(data) if isinstance(value, str)]
    missing_values = [
        label
        for label, expected in (
            ('image', image),
            ('machine type', machine_type),
            ('command', command),
        )
        if expected not in strings
    ]
    if missing_values:
        return False, f'{filename}: missing {", ".join(missing_values)} evidence'
    if not any(value.startswith(job_name_prefix) for value in strings):
        return False, f'{filename}: no job name starts with {job_name_prefix!r}'

    mappings = [value for value in _walk_json(data) if isinstance(value, dict)]
    has_job_id = any(
        _is_positive_id(value)
        and 'group' not in _normalise_key(key)
        and (
            _normalise_key(key).endswith('jobid')
            or _normalise_key(key).endswith('taskid')
        )
        for mapping in mappings
        for key, value in mapping.items()
    )
    if not has_job_id:
        return False, f'{filename}: no positive job/task ID evidence'

    raw_status_keys = {'status', 'statuscode'}
    web_status_keys = {'webstatus', 'webstatuscode'}
    status_records = 0
    raw_statuses: list[int] = []
    web_statuses: list[int] = []
    for mapping in mappings:
        record_has_status = False
        for key, value in mapping.items():
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            normalised = _normalise_key(key)
            if normalised in raw_status_keys:
                raw_statuses.append(value)
                record_has_status = True
            elif normalised in web_status_keys:
                web_statuses.append(value)
                record_has_status = True
        status_records += int(record_has_status)

    if status_records < 2:
        return False, f'{filename}: fewer than two status query records'
    if not any(status in {0, 1, 3} for status in raw_statuses):
        return False, f'{filename}: no active raw status evidence'
    if 5 not in web_statuses:
        return False, f'{filename}: no stopped webStatus evidence'
    if not any(
        re.search(r'\b(?:bohr\s+job\s+)?terminate\b', value, re.I) for value in strings
    ):
        return False, f'{filename}: no graceful terminate action evidence'

    return (
        True,
        f'{filename}: job identity, configuration, status history, and graceful stop '
        'are recorded',
    )


def check_bohr_job_upgrade_record(
    workspace_dir: str | Path,
    *,
    filename: str,
    seed_id: int,
    source_machine_pattern: str,
    target_machine_pattern: str,
    image: str,
    command: str,
) -> tuple[bool, str]:
    """Validate a Bohr job upgrade record without prescribing its JSON layout."""
    if (
        not filename
        or seed_id <= 0
        or not all((source_machine_pattern, target_machine_pattern, image, command))
    ):
        return False, 'bohr_job_upgrade_record: incomplete verifier configuration'

    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'{filename} not found in workspace'
    try:
        data = json.loads(fpath.read_text(encoding='utf-8'))
    except ValueError as exc:
        return False, f'{filename} is not valid JSON: {exc}'

    values = list(_walk_json(data))
    strings = [value.strip() for value in values if isinstance(value, str)]
    identifiers = _collect_json_identifiers(data)

    if seed_id not in identifiers:
        return False, f'{filename}: supplied source task identifier is not recorded'
    if not any(identifier != seed_id for identifier in identifiers):
        return False, f'{filename}: no distinct positive resubmitted job identifier'
    if not any(re.search(source_machine_pattern, value) for value in strings):
        return False, f'{filename}: source machine evidence is missing'
    if not any(re.search(target_machine_pattern, value) for value in strings):
        return False, f'{filename}: target A100 machine evidence is missing'
    if image not in strings:
        return False, f'{filename}: preserved image evidence is missing'
    if command not in strings:
        return False, f'{filename}: preserved command evidence is missing'

    return (
        True,
        f'{filename}: source and resubmitted jobs, machine change, image, and command '
        'are recorded',
    )


def check_bohr_gpu_comparison_record(
    workspace_dir: str | Path,
    *,
    filename: str,
) -> tuple[bool, str]:
    """Check that a recommended Bohr machine is an in-stock listed candidate."""
    if not filename:
        return False, 'bohr_gpu_comparison_record: no filename provided'

    root = Path(workspace_dir)
    fpath = _resolve_file(root, filename)
    if fpath is None:
        return False, f'{filename} not found in workspace'
    try:
        data = json.loads(fpath.read_text(encoding='utf-8'))
    except ValueError as exc:
        return False, f'{filename} is not valid JSON: {exc}'

    if not isinstance(data, dict):
        return False, f'{filename}: top-level value must be an object'
    candidates = data.get('available_machines')
    recommendation = data.get('recommendation')
    if not isinstance(candidates, list) or not isinstance(recommendation, dict):
        return False, f'{filename}: missing candidate list or recommendation object'

    selected = recommendation.get('machine_type')
    if not isinstance(selected, str) or not selected.strip():
        return False, f'{filename}: recommendation has no machine_type'

    def _normalise_machine_type(value: object) -> str:
        if not isinstance(value, str):
            return ''
        return re.sub(r'\s+', ' ', value).strip().casefold()

    selected_normalised = _normalise_machine_type(selected)
    matches = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and _normalise_machine_type(candidate.get('machine_type'))
        == selected_normalised
    ]
    if not matches:
        return False, f'{filename}: recommended machine is not in available_machines'
    if not any(candidate.get('has_stock') is True for candidate in matches):
        return False, f'{filename}: recommended machine is not marked in stock'

    return True, f'{filename}: recommended machine is a listed in-stock candidate'


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
