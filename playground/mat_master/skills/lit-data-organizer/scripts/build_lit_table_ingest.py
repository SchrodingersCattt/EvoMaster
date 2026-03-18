"""Ingest input files with per-file fingerprint cache and normalized rows."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from build_lit_table_io import (
    cache_dir_for,
    fingerprint_file,
    load_rows,
    save_rows,
)
from build_lit_table_normalize import normalize_records


def ingest_and_cache_normalized_rows(
    *,
    input_paths: list[Path],
    source_type: str,
    schema_cfg: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    resume: bool,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Normalize inputs with per-file cache keyed by file fingerprint."""
    cache_dir = cache_dir_for(state_path)
    processed = state.get('processed_inputs', {})
    if not isinstance(processed, dict):
        processed = {}

    all_rows: list[dict[str, str]] = []
    raw_total = 0
    reused = 0
    rebuilt = 0

    for path in input_paths:
        fp = fingerprint_file(path)
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
            rows = load_rows(cache_file)
            all_rows.extend(rows)
            raw_total += int(processed.get(key, {}).get('raw_records', 0) or 0)
            reused += 1
        else:
            rows, raw_count = normalize_records(
                input_paths=[path],
                source_type=source_type,
                schema_cfg=schema_cfg,
            )
            save_rows(cache_file, rows)
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
