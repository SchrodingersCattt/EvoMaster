"""Record extraction, field normalization and canonical row building."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from build_lit_table_io import load_json
from build_lit_table_schema import CANONICAL_FIELDS, canonical_aliases


def get_by_path(data: Any, path: str) -> Any:
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


def stringify(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def normalize_tags(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, list):
        return ','.join(str(v).strip() for v in value if str(v).strip())
    return stringify(value)


def normalize_json_field(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return stringify(value)


def first_non_empty(record: dict[str, Any], candidates: list[str]) -> Any:
    for key in candidates:
        value = get_by_path(record, key)
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


def infer_source_type(record: dict[str, Any], requested: str) -> str:
    if requested in {'pdf', 'web'}:
        return requested

    source_hint = stringify(
        first_non_empty(
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

    page_like = first_non_empty(record, ['page', 'page_number', 'evidence_span'])
    if page_like is not None:
        return 'pdf'
    return 'web'


def stable_id(parts: list[str]) -> str:
    joined = '|'.join(parts)
    return hashlib.sha1(joined.encode('utf-8')).hexdigest()[:12]


def extract_records(payload: Any) -> list[dict[str, Any]]:
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


def infer_property_role(
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
    aliases = canonical_aliases(schema_cfg)
    defaults = schema_cfg.get('defaults', {})
    if not isinstance(defaults, dict):
        defaults = {}

    raw_count = 0
    normalized: list[dict[str, str]] = []

    for path in input_paths:
        payload = load_json(path)
        records = extract_records(payload)
        raw_count += len(records)

        for record in records:
            row: dict[str, str] = {}
            for field in CANONICAL_FIELDS:
                candidates = aliases.get(field, [field])
                value = first_non_empty(record, candidates)
                if value is None:
                    value = defaults.get(field)

                if field == 'tags':
                    row[field] = normalize_tags(value)
                elif field in {'independent_vars', 'conditions'}:
                    row[field] = normalize_json_field(value)
                elif field == 'evidence_span':
                    if value is None:
                        page_value = first_non_empty(record, ['page', 'page_number'])
                        if page_value is not None:
                            value = f"page:{stringify(page_value)}"
                    row[field] = stringify(value)
                else:
                    row[field] = stringify(value)

            if not row['source_url_or_path']:
                row['source_url_or_path'] = str(path)

            if not row['source_type']:
                row['source_type'] = infer_source_type(record, source_type)
            else:
                row['source_type'] = row['source_type'].lower()

            if not row['created_at']:
                row['created_at'] = datetime.now(UTC).isoformat()

            row['property_role'] = infer_property_role(row, schema_cfg)

            if not row['source_id']:
                row['source_id'] = stable_id(
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
