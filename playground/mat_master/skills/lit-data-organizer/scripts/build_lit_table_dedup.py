"""Deduplication and conflict annotation for lit evidence rows."""

import hashlib
import json

from build_lit_table_normalize import stable_id


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

        conflict_id = f"conflict-{stable_id([*group_key])}"
        for i in indices:
            rows[i]['conflict_group_id'] = conflict_id
            if not rows[i].get('conflict_note', '').strip():
                rows[i]['conflict_note'] = (
                    'Conflicting measurements detected for the same material-property '
                    'group across sources or methods.'
                )
        conflict_count += 1
    return conflict_count
