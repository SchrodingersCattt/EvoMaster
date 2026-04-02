"""I/O, path resolution, caching and tool-output discovery for build_lit_table."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# Allow standalone import (e.g. tests); same path as build_lit_table.py so longtask_runtime is found
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_common'))
from longtask_runtime import read_json as _read_json  # noqa: E402
from longtask_runtime import write_json as _write_json  # noqa: E402


def load_json(path: Path) -> Any:
    with path.open('r', encoding='utf-8') as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Fallback: NDJSON (multiple JSON objects per file, one per line)
    objects: list[Any] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            objects.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not objects:
        raise json.JSONDecodeError('No valid JSON found', str(path), 0)
    if len(objects) == 1:
        return objects[0]
    # Merge multiple objects: flatten nested "data" lists into one
    merged: list[Any] = []
    for obj in objects:
        if isinstance(obj, dict) and 'data' in obj and isinstance(obj['data'], list):
            merged.extend(obj['data'])
        elif isinstance(obj, list):
            merged.extend(obj)
        elif isinstance(obj, dict):
            merged.append(obj)
    return {'data': merged} if merged else objects


def load_schema(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    schema_path = Path(path)
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    data = load_json(schema_path)
    if not isinstance(data, dict):
        raise ValueError('Schema config must be a JSON object.')
    return data


def resolve_input_paths(input_json: list[str], input_dir: str | None) -> list[Path]:
    paths: list[Path] = []
    for p in input_json:
        path = Path(p)
        if not path.exists():
            raise FileNotFoundError(f"Input JSON file not found: {path}")
        if path.is_file():
            paths.append(path)
    if input_dir:
        directory = Path(input_dir)
        if not directory.exists() or not directory.is_dir():
            raise FileNotFoundError(f"Input directory not found: {directory}")
        direct = sorted(directory.glob('*.json'))
        if direct:
            paths.extend(direct)
        else:
            paths.extend(sorted(directory.glob('**/*.json')))

    unique_paths = []
    seen = set()
    for path in paths:
        rp = str(path.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        unique_paths.append(path)
    if not unique_paths:
        raise ValueError('No input JSON files found.')
    return unique_paths


FINGERPRINT_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MB


def fingerprint_file(path: Path) -> str:
    size = path.stat().st_size
    if size > FINGERPRINT_SIZE_LIMIT:
        mtime = path.stat().st_mtime
        return f"meta:{size}:{mtime}"
    h = hashlib.sha1()
    with path.open('rb') as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def cache_dir_for(state_path: Path) -> Path:
    d = state_path.parent / 'cache'
    d.mkdir(parents=True, exist_ok=True)
    return d


def rows_file_for(state_path: Path, stage: str) -> Path:
    return state_path.parent / f"{stage}_rows.json"


def load_rows(path: Path) -> list[dict[str, str]]:
    data = _read_json(path, default=[])
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def save_rows(path: Path, rows: list[dict[str, str]]) -> None:
    _write_json(path, rows)


def auto_discover_tool_outputs(input_paths: list[Path]) -> list[Path]:
    """Discover mat_sn_* and web-search auto-saved JSON files.

    Looks for _tmp/tool_outputs/ by walking up from each input path, then falls
    back to the current working directory. Returns deduplicated discovered paths.
    """
    discovered: list[Path] = []
    seen: set[str] = set()

    def _find_tool_outputs_dir(start: Path) -> Path | None:
        for parent in [start] + list(start.parents):
            candidate = parent / '_tmp' / 'tool_outputs'
            if candidate.is_dir():
                return candidate
        return None

    search_roots: list[Path | None] = [
        _find_tool_outputs_dir(p.parent) for p in input_paths
    ]
    search_roots.append(_find_tool_outputs_dir(Path.cwd()))

    for tool_outputs_dir in search_roots:
        if not tool_outputs_dir or not tool_outputs_dir.is_dir():
            continue
        for subdir in sorted(tool_outputs_dir.iterdir()):
            if not subdir.is_dir():
                continue
            if not (subdir.name.startswith('mat_sn_') or subdir.name == 'web-search'):
                continue
            for json_file in sorted(subdir.glob('*.json')):
                key = str(json_file.resolve())
                if key not in seen:
                    seen.add(key)
                    discovered.append(json_file)

    return discovered
