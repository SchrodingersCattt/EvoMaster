#!/usr/bin/env python3
"""Submit a pending eval ingest file after the score is known.

``run_devshell_eval.py --eval-ingest-pending-only`` writes one JSON per task under
``pending_ingest/`` with ``ingest_url``, ``run_id``, ``git_commit``, and ``item`` **without**
``score``. After judging, run::

    uv run python scripts/eval_ingest_submit_pending.py \\
        --pending results/devshell_eval_xxx/pending_ingest/SC_struct_007_direct_r0.json \\
        --score 73

This POSTs ``{ run_id, git_commit?, items: [item with score] }`` to ``ingest_url``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="POST eval ingest from a pending_ingest/*.json (add score).",
    )
    parser.add_argument(
        "--pending",
        type=Path,
        required=True,
        help="Path to pending_ingest/<task_id>.json",
    )
    parser.add_argument(
        "--score",
        type=float,
        required=True,
        help="Numeric score to set on the item (e.g. 0–100).",
    )
    parser.add_argument(
        "--eval-ingest-timeout",
        type=float,
        default=120.0,
        help="HTTP timeout seconds (default: 120).",
    )
    args = parser.parse_args()

    path = args.pending.resolve()
    if not path.is_file():
        print(f"not found: {path}", file=sys.stderr)
        return 1

    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 1

    if not isinstance(envelope, dict):
        print("pending file must be a JSON object", file=sys.stderr)
        return 1

    ingest_url = (envelope.get("ingest_url") or "").strip()
    run_id = (envelope.get("run_id") or "").strip()
    item = envelope.get("item")
    if not ingest_url or not run_id or not isinstance(item, dict):
        print(
            "pending file missing ingest_url, run_id, or item object", file=sys.stderr
        )
        return 1

    qid = item.get("question_id")
    if not isinstance(qid, str) or not qid:
        print("item.question_id missing or invalid", file=sys.stderr)
        return 1

    item = dict(item)
    item["score"] = float(args.score)

    body: dict[str, Any] = {"run_id": run_id, "items": [item]}
    gc = envelope.get("git_commit")
    if isinstance(gc, str) and gc.strip():
        body["git_commit"] = gc.strip()

    sys.path.insert(0, str(REPO_ROOT))
    from matmaster.eval_ingest_client import post_eval_ingest

    ok, msg = post_eval_ingest(
        ingest_url,
        body,
        timeout=float(args.eval_ingest_timeout),
    )
    if ok:
        print(f"ingest ok: {msg}", file=sys.stderr)
        return 0
    print(f"ingest failed: {msg}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
