#!/usr/bin/env python3
"""Print question_ids with no baseline score for a channel via tools-server score-summary.

Uses ``GET /api/v1/evaluation/questions/score-summary`` (matmaster-tools-server
``evaluation_questions_score_summary``), not per-question ``.../overview``.

Environment (same as ingest):

* ``MATMASTER_TOOLS_SERVER``
* ``MATMASTER_TOOLS_EVALUATION_BEARER``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evaluation.eval_ingest_client import fetch_missing_baseline_question_ids


def _read_id_whitelist(path: Path) -> frozenset[str]:
    text = path.read_text(encoding="utf-8")
    ids: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for part in line.split(","):
            p = part.strip()
            if p:
                ids.add(p)
    return frozenset(ids)


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "List active catalog question_ids where baseline score for --channel is null "
            "(from tools-server score-summary)."
        )
    )
    p.add_argument(
        "--channel",
        choices=["claude_code", "cursor", "codex"],
        default="claude_code",
        help="Baseline channel column to check (default: claude_code).",
    )
    p.add_argument(
        "--intersect-file",
        type=Path,
        default=None,
        help=(
            "If set and non-empty after parsing: only emit ids present in this file "
            "(newline or comma-separated; e.g. output of ci/baseline_eval_preset.py list-ids)."
        ),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="If > 0, emit at most this many ids (stable order from server).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="HTTP timeout seconds (default: 120).",
    )
    args = p.parse_args()

    ok, msg, ids = fetch_missing_baseline_question_ids(
        channel=args.channel,
        timeout=float(args.timeout),
    )
    if not ok:
        print(msg, file=sys.stderr)
        return 1

    if args.intersect_file is not None:
        if not args.intersect_file.is_file():
            print(f"intersect-file not found: {args.intersect_file}", file=sys.stderr)
            return 1
        whitelist = _read_id_whitelist(args.intersect_file)
        if whitelist:
            ids = [q for q in ids if q in whitelist]

    if args.limit > 0:
        ids = ids[: int(args.limit)]

    for q in ids:
        print(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
