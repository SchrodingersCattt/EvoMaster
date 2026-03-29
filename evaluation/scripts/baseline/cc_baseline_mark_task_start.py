#!/usr/bin/env python3
"""Mark wall-clock start for a Claude Code baseline task workspace.

Run once when you **begin** working on the task (e.g. right after opening the folder
in Claude Code). Later, ``finalize_cc_baseline_ingest.py`` prefers::

    duration_ms ≈ mtime(_devshell_summary.json) - started_at_unix_ms

so ingest ``duration_ms`` matches DevShell-style objective timing (no self-report).

Without this file, ``finalize_cc_baseline_ingest.py`` leaves ``duration_ms`` unset.

Example::

    uv run python evaluation/scripts/baseline/cc_baseline_mark_task_start.py \\
      --workspace \"$RUN_DIR/workspaces/SC_struct_007_direct_r0\"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Write _cc_baseline_task_start.json (objective start time for finalize duration)."
        )
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="Task directory (contains _devshell_prompt.txt, _eval_task_meta.json)",
    )
    args = parser.parse_args()
    ws = args.workspace.resolve()
    if not ws.is_dir():
        print(f"not a directory: {ws}", file=sys.stderr)
        return 1

    out = ws / "_cc_baseline_task_start.json"
    ms = time.time_ns() // 1_000_000
    payload = {
        "started_at_unix_ms": ms,
        "schema": "matmaster_cc_baseline_task_start_v1",
    }
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
