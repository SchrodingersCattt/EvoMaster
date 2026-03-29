#!/usr/bin/env python3
"""将本仓库 v5 ``question_bank`` 中的题目同步到 matmaster-tools-server 题库目录表。

对应 tools-server 接口（见 sibling 仓库 ``eval_question_catalog_api``）::

    POST {MATMASTER_TOOLS_SERVER}/api/v1/evaluation/question-catalog/sync
    Body: { \"items\": [ {\"question_id\": \"...\", \"question_text\": \"...\"}, ... ] }

``question_text`` 取自题库条目的 ``human_prompt_seed``（与 ingest 的题干字段一致）。

同步语义：服务端先将目录表内全部题目标为非活跃，再将 payload 中的题目 upsert 为活跃；
与 ``eval_results`` 仅通过 ``question_id`` 对齐。

环境：与 ingest / 配额相同，使用 ``MATMASTER_TOOLS_SERVER``（见 ``utils.env``），未设置时按
``SERVICE_ENV`` 推导默认 Bohrium 域名。

示例::

    uv run python evaluation/scripts/sync_question_catalog_to_tools_server.py
    uv run python evaluation/scripts/sync_question_catalog_to_tools_server.py --dry-run
    uv run python evaluation/scripts/sync_question_catalog_to_tools_server.py \\
        --question-bank-dir evaluation/question_bank
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

if TYPE_CHECKING:
    from evaluation.core.schemas import QuestionItem


def _stable_unique_catalog_items(
    flat: list[QuestionItem],
) -> tuple[list[dict[str, str]], str | None]:
    """Dedupe by ``question_id`` (first wins); build sync items with trimmed prompt text."""
    from evaluation.eval_ingest_client import (
        EVAL_ITEM_QUESTION_TEXT_MAX_LEN,
        clip_ingest_text_field,
    )

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for q in flat:
        qid = str(q.id).strip()
        if not qid or qid in seen:
            continue
        seen.add(qid)
        qtext = clip_ingest_text_field(
            q.human_prompt_seed, max_len=EVAL_ITEM_QUESTION_TEXT_MAX_LEN
        )
        if not qtext:
            return [], f"empty human_prompt_seed after trim for question_id={qid!r}"
        out.append({"question_id": qid, "question_text": qtext})
    return out, None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync local evaluation question_bank (id + human_prompt_seed) to tools-server catalog.",
    )
    parser.add_argument(
        "--question-bank-dir",
        type=str,
        default="evaluation/question_bank",
        help="Directory with v5 YAML banks (default: evaluation/question_bank).",
    )
    parser.add_argument(
        "--sync-url",
        type=str,
        default=None,
        help="Override full POST URL (default: MATMASTER_TOOLS_SERVER + question-catalog/sync).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="HTTP timeout seconds (default: 120).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load banks and print counts only; do not POST.",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))

    from evaluation.core.runner import (
        _flatten_banks,
        _resolve_to_project_root,
        load_question_banks,
    )
    from evaluation.eval_ingest_client import (
        QUESTION_CATALOG_SYNC_URL,
        post_question_catalog_sync,
    )

    bank_dir = Path(_resolve_to_project_root(args.question_bank_dir))
    if not bank_dir.is_dir():
        print(f"question bank directory not found: {bank_dir}", file=sys.stderr)
        return 1

    try:
        banks = load_question_banks(bank_dir)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    flat = _flatten_banks(banks)
    catalog_items, build_err = _stable_unique_catalog_items(flat)
    if build_err:
        print(build_err, file=sys.stderr)
        return 1

    print(
        f"loaded {len(flat)} question row(s) from {len(banks)} bank file(s)",
        file=sys.stderr,
    )
    print(f"unique catalog item count: {len(catalog_items)}", file=sys.stderr)

    if args.dry_run:
        for row in catalog_items[:20]:
            print(row["question_id"])
        if len(catalog_items) > 20:
            print(f"... and {len(catalog_items) - 20} more", file=sys.stderr)
        return 0

    url = (args.sync_url or "").strip() or (QUESTION_CATALOG_SYNC_URL or "")
    if not url:
        print(
            "no sync URL: set MATMASTER_TOOLS_SERVER or pass --sync-url",
            file=sys.stderr,
        )
        return 1

    ok, msg = post_question_catalog_sync(
        url, catalog_items, timeout=float(args.timeout)
    )
    if ok:
        print(msg, file=sys.stderr)
        return 0
    print(f"sync failed: {msg}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
