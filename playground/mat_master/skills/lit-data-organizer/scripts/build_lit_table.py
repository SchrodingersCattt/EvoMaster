#!/usr/bin/env python3
"""
Build a canonical NotebookLM-style evidence table from structured literature data.

This script merges structured JSON outputs from PDF extraction and web extraction
pipelines into one canonical row schema and exports CSV or JSONL.
"""


import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_common'))
from longtask_runtime import (
    STATUS_COMPLETED,
    STATUS_FATAL_ERROR,
    STATUS_RETRYABLE_ERROR,
    append_event,
    build_result,
    emit_result,
    init_or_load_state,
    read_json,
    write_json,
)

CANONICAL_FIELDS = [
    'source_id',
    'source_type',
    'source_title',
    'source_url_or_path',
    'topic',
    'claim_text',
    'quote_text',
    'summary_text',
    'evidence_span',
    'tags',
    'confidence',
    'created_at',
    'material_name',
    'formula',
    'composition',
    'phase_or_polymorph',
    'independent_vars',
    'property_name',
    'property_value',
    'property_unit',
    'property_role',
    'test_method',
    'conditions',
    'uncertainty',
    'conflict_group_id',
    'conflict_note',
    'enrich_note',
    'enrich_keep',
]


DEFAULT_ALIASES: dict[str, list[str]] = {
    # mat_sn_search-papers-enhanced: doi, paperId
    'source_id': [
        'source_id',
        'source.id',
        'doc_id',
        'paper_id',
        'id',
        'doi',
        'paperId',
    ],
    'source_type': ['source_type', 'source.type', 'origin_type'],
    # mat_sn_search-papers-enhanced: enName, zhName; web-search/mat_sn_web-search: title
    'source_title': [
        'source_title',
        'source.title',
        'title',
        'document_title',
        'paper_title',
        'name',
        'enName',
        'zhName',
    ],
    # mat_sn_search-papers-enhanced: paperUrl; web-search/mat_sn_web-search: link
    'source_url_or_path': [
        'source_url_or_path',
        'source_url',
        'url',
        'source.path',
        'path',
        'file_path',
        'pdf_path',
        'source',
        'paperUrl',
        'link',
    ],
    'topic': ['topic', 'subject', 'domain', 'field'],
    'claim_text': ['claim_text', 'claim', 'finding', 'result_claim', 'statement'],
    # web-search/mat_sn_web-search: snippet
    'quote_text': ['quote_text', 'quote', 'evidence_text', 'snippet', 'text'],
    # mat_sn_search-papers-enhanced: enAbstract, zhAbstract; web-search/mat_sn_web-search: snippet
    'summary_text': [
        'summary_text',
        'summary',
        'abstract',
        'note',
        'enAbstract',
        'zhAbstract',
        'snippet',
        'pieces',
    ],
    'evidence_span': [
        'evidence_span',
        'span',
        'page_span',
        'locator',
        'section',
        'paragraph',
        'page',
    ],
    'tags': ['tags', 'keywords', 'labels', 'facet'],
    'confidence': ['confidence', 'score', 'confidence_score'],
    # mat_sn_search-papers-enhanced: coverDateStart, year (from evidence_cards)
    'created_at': [
        'created_at',
        'timestamp',
        'date',
        'published_at',
        'year',
        'coverDateStart',
    ],
    'material_name': ['material_name', 'material', 'compound', 'compound_name'],
    'formula': ['formula', 'chemical_formula', 'composition.formula'],
    'composition': ['composition', 'metal_composition', 'alloy_composition'],
    'phase_or_polymorph': ['phase_or_polymorph', 'phase', 'polymorph', 'crystal_form'],
    'independent_vars': [
        'independent_vars',
        'features',
        'inputs',
        'independent_variables',
        'data_points',
    ],
    'property_name': ['property_name', 'target_name', 'property', 'measurement_name'],
    'property_value': ['property_value', 'target_value', 'value', 'measurement_value'],
    'property_unit': ['property_unit', 'unit', 'units'],
    'property_role': ['property_role', 'role', 'variable_role'],
    'test_method': ['test_method', 'method', 'measurement_method', 'protocol'],
    'conditions': ['conditions', 'condition', 'experimental_conditions'],
    'uncertainty': ['uncertainty', 'error_bar', 'std', 'sigma'],
    'conflict_group_id': ['conflict_group_id'],
    'conflict_note': ['conflict_note'],
    'enrich_note': ['enrich_note'],
    'enrich_keep': ['enrich_keep'],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Merge structured literature JSON files into one canonical evidence table '
            'and export CSV or JSONL.'
        )
    )
    parser.add_argument(
        '--input_json',
        nargs='*',
        default=[],
        help='Input JSON files (space-separated).',
    )
    parser.add_argument(
        '--input_dir',
        help='Directory containing JSON files to ingest.',
    )
    parser.add_argument(
        '--source_type',
        choices=['auto', 'pdf', 'web', 'survey'],
        default='auto',
        help="Source type for ingested records. Use 'survey' when input is survey contract JSON (or rely on source_kind in JSON).",
    )
    parser.add_argument(
        '--schema',
        help='Optional schema JSON file for alias/default overrides.',
    )
    parser.add_argument(
        '--dedup_keys',
        default='source_url_or_path,quote_text,property_name,property_value',
        help='Comma-separated canonical keys used for deduplication.',
    )
    parser.add_argument(
        '--output',
        default=None,
        help='Output file path.',
    )
    parser.add_argument(
        '--format',
        choices=['csv', 'jsonl'],
        default=None,
        help='Output format.',
    )
    parser.add_argument(
        '--stage',
        choices=[
            'all',
            'ingest',
            'normalize',
            'template',
            'fill',
            'enrich',
            'dedup',
            'conflict',
            'export',
            'status',
        ],
        default='all',
        help='Run a specific stage or the full pipeline (default: all).',
    )
    parser.add_argument(
        '--enrich_rows',
        default=None,
        help='Path to agent-generated enrich_rows.json to load before dedup (overrides state[enrich_rows_file]).',
    )
    parser.add_argument(
        '--state',
        default='_tmp/lit_data/state.json',
        help='State file path for resumable workflow.',
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume using existing state/caches when available.',
    )
    args = parser.parse_args()
    if not args.input_json and not args.input_dir:
        parser.error('At least one of --input_json or --input_dir is required.')
    if args.stage in {'all', 'export'} and (not args.output or not args.format):
        parser.error("--output and --format are required for stage 'all' and 'export'.")
    return args


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


def _get_by_path(data: Any, path: str) -> Any:
    if not path:
        return None
    if isinstance(data, dict) and path in data:
        return data[path]
    cur = data
    for part in path.split('.'):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError:
                return None
            if idx < 0 or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            return None
        if cur is None:
            return None
    return cur


def _stringify(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _normalize_tags(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, list):
        return ','.join(str(v).strip() for v in value if str(v).strip())
    return _stringify(value)


def _normalize_json_field(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _stringify(value)


def _first_non_empty(record: dict[str, Any], candidates: list[str]) -> Any:
    for key in candidates:
        value = _get_by_path(record, key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and len(value) == 0:
            continue
        if isinstance(value, dict) and len(value) == 0:
            continue
        return value
    return None


def _infer_source_type(record: dict[str, Any], requested: str) -> str:
    if requested in {'pdf', 'web'}:
        return requested

    source_hint = _stringify(
        _first_non_empty(
            record,
            [
                'source_type',
                'source.type',
                'source_url_or_path',
                'source_url',
                'url',
                'file_path',
                'path',
            ],
        )
    ).lower()
    if 'pdf' in source_hint:
        return 'pdf'
    if source_hint.startswith('http://') or source_hint.startswith('https://'):
        return 'web'
    if source_hint.endswith('.pdf'):
        return 'pdf'

    page_like = _first_non_empty(record, ['page', 'page_number', 'evidence_span'])
    if page_like is not None:
        return 'pdf'
    return 'web'


def _stable_id(parts: list[str]) -> str:
    joined = '|'.join(parts)
    return hashlib.sha1(joined.encode('utf-8')).hexdigest()[:12]


def _canonical_aliases(schema_cfg: dict[str, Any]) -> dict[str, list[str]]:
    aliases = {k: list(v) for k, v in DEFAULT_ALIASES.items()}
    schema_aliases = schema_cfg.get('field_aliases', {})
    if isinstance(schema_aliases, dict):
        for field, alias_values in schema_aliases.items():
            if field not in CANONICAL_FIELDS:
                continue
            if isinstance(alias_values, str):
                alias_list = [alias_values]
            elif isinstance(alias_values, list):
                alias_list = [str(v) for v in alias_values]
            else:
                continue
            aliases[field] = [field] + [a for a in alias_list if a != field]
    else:
        for field in CANONICAL_FIELDS:
            aliases[field] = [field] + [a for a in aliases[field] if a != field]
    return aliases


def _extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in (
        'evidence_cards',
        'lit_evidence_table',
        'records',
        'items',
        'data',
        'results',
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payload]


def _is_survey_input_from_metadata(input_paths: list[Path]) -> bool:
    """True if any input JSON has source_kind == 'survey' (schema_version 2 contract)."""
    for p in input_paths:
        if not p.suffix.lower() == '.json' or not p.exists():
            continue
        try:
            data = load_json(p)
            if isinstance(data, dict) and data.get('source_kind') == 'survey':
                return True
        except Exception:
            continue
    return False


def _load_survey_context(
    input_paths: list[Path],
    explicit_survey_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load topic and key_concepts from survey contract (collected_*.json with source_kind survey).

    If explicit_survey_path is set, use that file only. Otherwise scan input_paths for
    source_kind == 'survey'. Returns {"topic": str, "key_concepts": list[str]} or {} if none found.
    """
    paths_to_try: list[Path] = []
    if explicit_survey_path:
        p = Path(explicit_survey_path)
        if p.exists():
            paths_to_try.append(p)
    if not paths_to_try:
        for p in input_paths:
            if p.suffix.lower() != '.json' or not p.exists():
                continue
            try:
                data = load_json(p)
                if isinstance(data, dict) and data.get('source_kind') == 'survey':
                    paths_to_try.append(p)
                    break
            except Exception:
                continue
    if not paths_to_try:
        return {}
    data = load_json(paths_to_try[0])
    if not isinstance(data, dict):
        return {}
    topic = data.get('topic')
    if topic is not None and not isinstance(topic, str):
        topic = str(topic)
    key_concepts = data.get('key_concepts')
    if isinstance(key_concepts, list):
        key_concepts = [str(c) for c in key_concepts if c is not None]
    else:
        key_concepts = []
    return {'topic': topic or '', 'key_concepts': key_concepts}


def _parse_enrich_batch_response(
    text: str, batch_start_idx: int
) -> list[dict[str, Any]] | None:
    """Parse LLM JSON array response. Returns list of dicts with idx, keep, material_name, etc., or None."""
    if not text or not text.strip():
        return None
    # Strip markdown code fence if present
    stripped = text.strip()
    if stripped.startswith('```'):
        stripped = re.sub(r'^```(?:json)?\s*', '', stripped)
        stripped = re.sub(r'\s*```\s*$', '', stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    out = []
    batch_len = len(data)
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        raw_idx = item.get('idx')
        if raw_idx is None:
            idx = batch_start_idx + i
        elif isinstance(raw_idx, int):
            if batch_start_idx <= raw_idx < batch_start_idx + batch_len:
                idx = raw_idx
            elif 0 <= raw_idx < batch_len:
                idx = batch_start_idx + raw_idx
            else:
                idx = batch_start_idx + i
        else:
            try:
                idx = int(raw_idx)
                if idx < batch_start_idx or idx >= batch_start_idx + batch_len:
                    idx = batch_start_idx + i
            except (TypeError, ValueError):
                idx = batch_start_idx + i
        out.append(
            {
                'idx': idx,
                'keep': item.get('keep', True),
                'material_name': _stringify(item.get('material_name')),
                'property_name': _stringify(item.get('property_name')),
                'property_value': _stringify(item.get('property_value')),
                'property_unit': _stringify(item.get('property_unit')),
                'enrich_note': _stringify(item.get('enrich_note')),
            }
        )
    return out if out else None


def _auto_discover_tool_outputs(input_paths: list[Path]) -> list[Path]:
    """Discover mat_sn_* auto-saved JSON files when the primary inputs yielded 0 records
    or when supplementing survey-only input.

    Looks for _tmp/tool_outputs/ by walking up from each input path, then falls
    back to the current working directory. Returns deduplicated discovered paths.
    """
    discovered: list[Path] = []
    seen: set[str] = set()

    def _find_tool_outputs_dir(start: Path) -> Path | None:
        """Traverse up to find _tmp/tool_outputs/ directory."""
        for parent in [start] + list(start.parents):
            candidate = parent / '_tmp' / 'tool_outputs'
            if candidate.is_dir():
                return candidate
        return None

    search_roots: list[Path | None] = [
        _find_tool_outputs_dir(p.parent) for p in input_paths
    ]
    # also try CWD
    search_roots.append(_find_tool_outputs_dir(Path.cwd()))

    for tool_outputs_dir in search_roots:
        if not tool_outputs_dir or not tool_outputs_dir.is_dir():
            continue
        for subdir in sorted(tool_outputs_dir.iterdir()):
            if not subdir.is_dir() or not subdir.name.startswith('mat_sn_'):
                continue
            for json_file in sorted(subdir.glob('*.json')):
                key = str(json_file.resolve())
                if key not in seen:
                    seen.add(key)
                    discovered.append(json_file)

    return discovered


def _load_schema(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    schema_path = Path(path)
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    data = load_json(schema_path)
    if not isinstance(data, dict):
        raise ValueError('Schema config must be a JSON object.')
    return data


def _resolve_input_paths(input_json: list[str], input_dir: str | None) -> list[Path]:
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
            # Search results from mat_sn_* tools are saved in subdirectories
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


_FINGERPRINT_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MB


def _fingerprint_file(path: Path) -> str:
    size = path.stat().st_size
    if size > _FINGERPRINT_SIZE_LIMIT:
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


def _cache_dir_for(state_path: Path) -> Path:
    d = state_path.parent / 'cache'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rows_file_for(state_path: Path, stage: str) -> Path:
    return state_path.parent / f"{stage}_rows.json"


def _load_rows(path: Path) -> list[dict[str, str]]:
    data = read_json(path, default=[])
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def _save_rows(path: Path, rows: list[dict[str, str]]) -> None:
    write_json(path, rows)


def _ingest_and_cache_normalized_rows(
    *,
    input_paths: list[Path],
    source_type: str,
    schema_cfg: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    resume: bool,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Normalize inputs with per-file cache keyed by file fingerprint."""
    cache_dir = _cache_dir_for(state_path)
    processed = state.get('processed_inputs', {})
    if not isinstance(processed, dict):
        processed = {}

    all_rows: list[dict[str, str]] = []
    raw_total = 0
    reused = 0
    rebuilt = 0

    for path in input_paths:
        fp = _fingerprint_file(path)
        key = str(path.resolve())
        cache_file = cache_dir / f"{path.stem}_{fp[:12]}.json"

        can_reuse = False
        if resume and key in processed:
            prev = processed.get(key) or {}
            if (
                isinstance(prev, dict)
                and prev.get('fingerprint') == fp
                and cache_file.exists()
            ):
                can_reuse = True

        if can_reuse:
            rows = _load_rows(cache_file)
            all_rows.extend(rows)
            raw_total += int(processed.get(key, {}).get('raw_records', 0) or 0)
            reused += 1
        else:
            rows, raw_count = normalize_records(
                input_paths=[path],
                source_type=source_type,
                schema_cfg=schema_cfg,
            )
            _save_rows(cache_file, rows)
            processed[key] = {
                'fingerprint': fp,
                'cache_file': str(cache_file),
                'raw_records': raw_count,
                'normalized_records': len(rows),
                'updated_at': datetime.now(UTC).isoformat(),
            }
            all_rows.extend(rows)
            raw_total += raw_count
            rebuilt += 1

    state['processed_inputs'] = processed
    state['ingest_stats'] = {
        'inputs_total': len(input_paths),
        'reused_from_cache': reused,
        'rebuilt': rebuilt,
        'raw_records': raw_total,
        'normalized_records': len(all_rows),
    }
    return all_rows, state


def _infer_property_role(
    row: dict[str, str],
    schema_cfg: dict[str, Any],
) -> str:
    explicit = row.get('property_role', '').strip().lower()
    if explicit in {'independent', 'dependent', 'intermediate'}:
        return explicit

    prop_name = row.get('property_name', '').strip().lower()
    role_map = schema_cfg.get('property_role_map', {})
    if isinstance(role_map, dict):
        mapped = str(role_map.get(prop_name, '')).strip().lower()
        if mapped in {'independent', 'dependent', 'intermediate'}:
            return mapped

    for key, role in (
        ('intermediate_properties', 'intermediate'),
        ('independent_properties', 'independent'),
        ('dependent_properties', 'dependent'),
    ):
        values = schema_cfg.get(key, [])
        if isinstance(values, list):
            lowered = {str(v).strip().lower() for v in values}
            if prop_name and prop_name in lowered:
                return role

    if row.get('property_name') and row.get('property_value'):
        return 'dependent'
    return ''


def normalize_records(
    input_paths: list[Path],
    source_type: str,
    schema_cfg: dict[str, Any],
) -> tuple[list[dict[str, str]], int]:
    aliases = _canonical_aliases(schema_cfg)
    defaults = schema_cfg.get('defaults', {})
    if not isinstance(defaults, dict):
        defaults = {}

    raw_count = 0
    normalized: list[dict[str, str]] = []

    for path in input_paths:
        payload = load_json(path)
        records = _extract_records(payload)
        raw_count += len(records)

        for record in records:
            row: dict[str, str] = {}
            for field in CANONICAL_FIELDS:
                candidates = aliases.get(field, [field])
                value = _first_non_empty(record, candidates)
                if value is None:
                    value = defaults.get(field)

                if field == 'tags':
                    row[field] = _normalize_tags(value)
                elif field in {'independent_vars', 'conditions'}:
                    row[field] = _normalize_json_field(value)
                elif field == 'evidence_span':
                    if value is None:
                        page_value = _first_non_empty(record, ['page', 'page_number'])
                        if page_value is not None:
                            value = f"page:{_stringify(page_value)}"
                    row[field] = _stringify(value)
                else:
                    row[field] = _stringify(value)

            if not row['source_url_or_path']:
                row['source_url_or_path'] = str(path)

            if not row['source_type']:
                row['source_type'] = _infer_source_type(record, source_type)
            else:
                row['source_type'] = row['source_type'].lower()

            if not row['created_at']:
                row['created_at'] = datetime.now(UTC).isoformat()

            row['property_role'] = _infer_property_role(row, schema_cfg)

            if not row['source_id']:
                row['source_id'] = _stable_id(
                    [
                        row.get('source_url_or_path', ''),
                        row.get('claim_text', ''),
                        row.get('quote_text', ''),
                        row.get('property_name', ''),
                        row.get('property_value', ''),
                    ]
                )

            normalized.append(row)

    return normalized, raw_count


def deduplicate_rows(
    rows: list[dict[str, str]], dedup_keys: list[str]
) -> tuple[list[dict[str, str]], int]:
    if not dedup_keys:
        return rows, 0

    seen: set[tuple[str, ...]] = set()
    output: list[dict[str, str]] = []
    dropped = 0

    for row in rows:
        values = [row.get(key, '').strip().lower() for key in dedup_keys]
        if all(not v for v in values):
            row_hash = hashlib.sha1(
                json.dumps(row, ensure_ascii=False, sort_keys=True).encode('utf-8')
            ).hexdigest()
            key = ('__row_hash__', row_hash)
        else:
            key = tuple(values)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        output.append(row)
    return output, dropped


def annotate_conflicts(rows: list[dict[str, str]]) -> int:
    groups: dict[tuple[str, str, str], list[int]] = {}
    for idx, row in enumerate(rows):
        material_key = next(
            (
                row.get(field, '').strip().lower()
                for field in ('material_name', 'formula', 'composition')
                if row.get(field, '').strip()
            ),
            '',
        )
        property_name = row.get('property_name', '').strip().lower()
        property_unit = row.get('property_unit', '').strip().lower()
        if not material_key or not property_name:
            continue
        groups.setdefault((material_key, property_name, property_unit), []).append(idx)

    conflict_count = 0
    for group_key, indices in groups.items():
        values = {
            rows[i].get('property_value', '').strip().lower()
            for i in indices
            if rows[i].get('property_value', '').strip()
        }
        if len(values) <= 1:
            continue

        conflict_id = f"conflict-{_stable_id([*group_key])}"
        for i in indices:
            rows[i]['conflict_group_id'] = conflict_id
            if not rows[i].get('conflict_note', '').strip():
                rows[i]['conflict_note'] = (
                    'Conflicting measurements detected for the same material-property '
                    'group across sources or methods.'
                )
        conflict_count += 1
    return conflict_count


def write_output(rows: list[dict[str, str]], output_path: Path, fmt: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == 'csv':
        with output_path.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=CANONICAL_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {field: row.get(field, '') for field in CANONICAL_FIELDS}
                )
        return

    if fmt == 'jsonl':
        with output_path.open('w', encoding='utf-8') as f:
            for row in rows:
                payload = {field: row.get(field, '') for field in CANONICAL_FIELDS}
                f.write(json.dumps(payload, ensure_ascii=False))
                f.write('\n')
        return

    raise ValueError(f"Unsupported format: {fmt}")


def main() -> None:
    try:
        args = parse_args()
        state_path = Path(args.state)
        events_path = state_path.parent / 'events.jsonl'
        result_path = state_path.parent / 'result.json'
        state = init_or_load_state(
            state_path=state_path,
            task_type='lit_data_table',
            stage=args.stage,
            resume=args.resume,
            extra={
                'dedup_keys': [
                    k.strip() for k in args.dedup_keys.split(',') if k.strip()
                ],
                'source_type': args.source_type,
            },
        )

        if args.stage == 'status':
            emit_result(
                build_result(
                    status=STATUS_COMPLETED,
                    stage='status',
                    message='Loaded workflow state.',
                    result_path=result_path,
                    payload={'state': state},
                )
            )
            return

        schema_cfg = _load_schema(args.schema)
        input_paths = _resolve_input_paths(args.input_json, args.input_dir)
        dedup_keys = [k.strip() for k in args.dedup_keys.split(',') if k.strip()]

        # Stage: ingest/normalize/all
        normalized_rows: list[dict[str, str]]
        raw_count = 0
        normalized_path = _rows_file_for(state_path, 'normalized')
        if args.stage in {'all', 'ingest', 'normalize'}:
            normalized_rows, state = _ingest_and_cache_normalized_rows(
                input_paths=input_paths,
                source_type=args.source_type,
                schema_cfg=schema_cfg,
                state=state,
                state_path=state_path,
                resume=args.resume,
            )
            raw_count = int(state.get('ingest_stats', {}).get('raw_records', 0) or 0)

            # Fallback: if primary inputs yielded 0 records, auto-discover raw
            # mat_sn_* tool outputs from _tmp/tool_outputs/ and re-ingest them.
            # This handles the case where collected.json has empty evidence_cards.
            if len(normalized_rows) == 0:
                fallback_paths = _auto_discover_tool_outputs(input_paths)
                if fallback_paths:
                    print(
                        json.dumps(
                            {
                                'info': 'Primary inputs yielded 0 records; auto-discovering raw tool outputs.',
                                'fallback_files_found': len(fallback_paths),
                            },
                            ensure_ascii=False,
                        )
                    )
                    fallback_rows, state = _ingest_and_cache_normalized_rows(
                        input_paths=fallback_paths,
                        source_type=args.source_type,
                        schema_cfg=schema_cfg,
                        state=state,
                        state_path=state_path,
                        resume=False,
                    )
                    if fallback_rows:
                        normalized_rows = fallback_rows
                        raw_count = int(
                            state.get('ingest_stats', {}).get('raw_records', 0) or 0
                        )
                        input_paths = fallback_paths
            # Supplement: when input is survey contract (source_kind in JSON) or
            # caller passed --source_type survey, merge in _tmp/tool_outputs/mat_sn_*.
            elif (
                _is_survey_input_from_metadata(input_paths)
                or args.source_type == 'survey'
            ):
                supplement_paths = _auto_discover_tool_outputs(input_paths)
                existing_resolved = {str(p.resolve()) for p in input_paths}
                extra = [
                    p
                    for p in supplement_paths
                    if str(p.resolve()) not in existing_resolved
                ]
                if extra:
                    print(
                        json.dumps(
                            {
                                'info': 'Survey-only input; supplementing with tool_outputs.',
                                'supplement_files_found': len(extra),
                            },
                            ensure_ascii=False,
                        )
                    )
                    supplement_rows, state = _ingest_and_cache_normalized_rows(
                        input_paths=extra,
                        source_type=args.source_type,
                        schema_cfg=schema_cfg,
                        state=state,
                        state_path=state_path,
                        resume=False,
                    )
                    if supplement_rows:
                        seen_dedup = set()
                        merged = []
                        keys_use = dedup_keys or ['source_url_or_path', 'quote_text']
                        for row in normalized_rows + supplement_rows:
                            key = tuple(row.get(k) for k in keys_use)
                            if key in seen_dedup:
                                continue
                            seen_dedup.add(key)
                            merged.append(row)
                        normalized_rows = merged
                        raw_count = len(normalized_rows)
                        state.setdefault('ingest_stats', {})['raw_records'] = raw_count
                        state.setdefault('ingest_stats', {})[
                            'normalized_records'
                        ] = raw_count

            _save_rows(normalized_path, normalized_rows)
            state['normalized_rows_file'] = str(normalized_path)
            write_json(state_path, state)
            append_event(
                events_path=events_path,
                status=STATUS_COMPLETED,
                stage='normalize',
                message='Normalized rows prepared.',
                payload=state.get('ingest_stats', {}),
            )
            if args.stage in {'ingest', 'normalize'}:
                emit_result(
                    build_result(
                        status=STATUS_COMPLETED,
                        stage=args.stage,
                        message=f"Stage '{args.stage}' completed.",
                        result_path=result_path,
                        payload={
                            'input_files': [str(p) for p in input_paths],
                            'normalized_rows': len(normalized_rows),
                            'normalized_rows_file': str(normalized_path),
                        },
                    )
                )
                return
        else:
            if not normalized_path.exists():
                emit_result(
                    build_result(
                        status=STATUS_RETRYABLE_ERROR,
                        stage=args.stage,
                        message='Missing normalized stage data. Run --stage normalize first or use --stage all.',
                        result_path=result_path,
                    )
                )
                return
            normalized_rows = _load_rows(normalized_path)

        # Stage: enrich — agent-side fill only; no LLM called here.
        # The agent writes enrich_rows.json and updates state["enrich_rows_file"] before calling --stage dedup.
        rows_for_dedup: list[dict[str, str]] = normalized_rows
        enrich_rows_file = args.enrich_rows or state.get('enrich_rows_file')
        if enrich_rows_file:
            enrich_path = Path(enrich_rows_file)
            if enrich_path.exists():
                enriched_rows = _load_rows(enrich_path)
                rows_for_dedup = [
                    r for r in enriched_rows if r.get('enrich_keep') != 'false'
                ]
                state['enrich_rows_file'] = str(enrich_path)
                write_json(state_path, state)

        # Stage: dedup/all
        deduped_path = _rows_file_for(state_path, 'deduped')
        deduped_rows: list[dict[str, str]]
        dropped = 0
        if args.stage in {'all', 'dedup'}:
            deduped_rows, dropped = deduplicate_rows(rows_for_dedup, dedup_keys)
            _save_rows(deduped_path, deduped_rows)
            state['deduped_rows_file'] = str(deduped_path)
            state['dedup_stats'] = {
                'deduplicated_records': len(deduped_rows),
                'dropped_duplicates': dropped,
            }
            write_json(state_path, state)
            append_event(
                events_path=events_path,
                status=STATUS_COMPLETED,
                stage='dedup',
                message='Dedup stage completed.',
                payload=state['dedup_stats'],
            )
            if args.stage == 'dedup':
                emit_result(
                    build_result(
                        status=STATUS_COMPLETED,
                        stage='dedup',
                        message="Stage 'dedup' completed.",
                        result_path=result_path,
                        payload=state['dedup_stats'],
                    )
                )
                return
        else:
            if not deduped_path.exists():
                emit_result(
                    build_result(
                        status=STATUS_RETRYABLE_ERROR,
                        stage=args.stage,
                        message='Missing dedup stage data. Run --stage dedup first or use --stage all.',
                        result_path=result_path,
                    )
                )
                return
            deduped_rows = _load_rows(deduped_path)

        # Stage: conflict/all
        conflict_path = _rows_file_for(state_path, 'conflicts')
        conflicts = 0
        if args.stage in {'all', 'conflict'}:
            conflicts = annotate_conflicts(deduped_rows)
            _save_rows(conflict_path, deduped_rows)
            state['conflict_rows_file'] = str(conflict_path)
            state['conflict_stats'] = {'conflict_groups': conflicts}
            write_json(state_path, state)
            append_event(
                events_path=events_path,
                status=STATUS_COMPLETED,
                stage='conflict',
                message='Conflict annotation completed.',
                payload=state['conflict_stats'],
            )
            if args.stage == 'conflict':
                emit_result(
                    build_result(
                        status=STATUS_COMPLETED,
                        stage='conflict',
                        message="Stage 'conflict' completed.",
                        result_path=result_path,
                        payload=state['conflict_stats'],
                    )
                )
                return
        else:
            if not conflict_path.exists():
                emit_result(
                    build_result(
                        status=STATUS_RETRYABLE_ERROR,
                        stage=args.stage,
                        message='Missing conflict stage data. Run --stage conflict first or use --stage all.',
                        result_path=result_path,
                    )
                )
                return
            deduped_rows = _load_rows(conflict_path)

        # Stage: export/all
        output_path = Path(args.output) if args.output else None
        if args.stage in {'all', 'export'}:
            if output_path is None or not args.format:
                emit_result(
                    build_result(
                        status=STATUS_FATAL_ERROR,
                        stage='export',
                        message='Export requires --output and --format.',
                        result_path=result_path,
                    )
                )
                sys.exit(1)
            write_output(deduped_rows, output_path, args.format)
            state['output_file'] = str(output_path)
            state['output_format'] = args.format
            write_json(state_path, state)

        summary = {
            'status': 'ok',
            'table_name': 'lit_evidence_table',
            'input_files': [str(p) for p in input_paths],
            'raw_records': raw_count
            or int(state.get('ingest_stats', {}).get('raw_records', 0) or 0),
            'normalized_records': int(
                state.get('ingest_stats', {}).get(
                    'normalized_records', len(normalized_rows)
                )
            ),
            'deduplicated_records': len(deduped_rows),
            'dropped_duplicates': dropped
            or int(state.get('dedup_stats', {}).get('dropped_duplicates', 0) or 0),
            'conflict_groups': conflicts
            or int(state.get('conflict_stats', {}).get('conflict_groups', 0) or 0),
            'output_file': str(output_path) if output_path else '',
            'output_format': args.format or '',
            'dedup_keys': dedup_keys,
            'stage': args.stage,
            'state_file': str(state_path),
        }
        append_event(
            events_path=events_path,
            status=STATUS_COMPLETED,
            stage=args.stage,
            message=f"Stage '{args.stage}' completed.",
            payload=summary,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        emit_result(
            build_result(
                status=STATUS_COMPLETED,
                stage=args.stage,
                message=f"Stage '{args.stage}' completed.",
                result_path=result_path,
                payload=summary,
            )
        )
    except Exception as exc:
        error = {'status': 'error', 'message': str(exc)}
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        emit_result(
            build_result(
                status=STATUS_FATAL_ERROR,
                stage='build_lit_table',
                message=f"Fatal error: {exc}",
            )
        )
        sys.exit(1)


if __name__ == '__main__':
    main()
