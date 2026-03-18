"""Export canonical rows to CSV or JSONL."""

import csv
import json
from pathlib import Path

from build_lit_table_schema import CANONICAL_FIELDS


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
