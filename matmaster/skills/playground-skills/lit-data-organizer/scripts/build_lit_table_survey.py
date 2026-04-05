"""Survey contract detection and enrich-batch response parsing."""

import json
import re
from pathlib import Path
from typing import Any

from build_lit_table_io import load_json
from build_lit_table_normalize import stringify


def is_survey_input_from_metadata(input_paths: list[Path]) -> bool:
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


def load_survey_context(
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


def parse_enrich_batch_response(
    text: str, batch_start_idx: int
) -> list[dict[str, Any]] | None:
    """Parse LLM JSON array response. Returns list of dicts with idx, keep, material_name, etc., or None."""
    if not text or not text.strip():
        return None
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
                'material_name': stringify(item.get('material_name')),
                'property_name': stringify(item.get('property_name')),
                'property_value': stringify(item.get('property_value')),
                'property_unit': stringify(item.get('property_unit')),
                'enrich_note': stringify(item.get('enrich_note')),
            }
        )
    return out if out else None
