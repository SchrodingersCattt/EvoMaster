"""Tests for devshell eval Feishu notification helpers."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.devshell_agent.feishu_round_notify import (
    _build_markdown_body,
    _load_pending_rows,
)


def test_build_markdown_sorts_scores_descending(tmp_path: Path) -> None:
    rows = [
        {"question_id": "low", "score": 40, "score_reason": "", "task_id": "a"},
        {"question_id": "high", "score": 100, "score_reason": "", "task_id": "b"},
        {"question_id": "mid", "score": 70, "score_reason": "", "task_id": "c"},
    ]
    _, md = _build_markdown_body(
        tag="t",
        run_dir=tmp_path,
        rows=rows,
        submit_ok=True,
        stderr_tail="",
    )
    hi = md.find("`high`")
    mid = md.find("`mid`")
    lo = md.find("`low`")
    assert hi != -1 and mid != -1 and lo != -1
    assert hi < mid < lo


def test_build_markdown_unscored_rows_last(tmp_path: Path) -> None:
    rows = [
        {"question_id": "pending", "score": None, "score_reason": "", "task_id": "p"},
        {"question_id": "ok", "score": 50, "score_reason": "", "task_id": "o"},
    ]
    _, md = _build_markdown_body(
        tag="t",
        run_dir=tmp_path,
        rows=rows,
        submit_ok=True,
        stderr_tail="",
    )
    assert md.find("`ok`") < md.find("`pending`")


def test_build_markdown_success(tmp_path: Path) -> None:
    rows = [
        {
            "question_id": "Q1",
            "score": 100,
            "score_reason": "bad",
            "task_id": "t",
        },
    ]
    t, md = _build_markdown_body(
        tag="iter_01",
        run_dir=tmp_path,
        rows=rows,
        submit_ok=True,
        stderr_tail="",
    )
    assert t == "green"
    assert "**通过**" in md or "：**通过**" in md
    assert "1/1 题全项通过" in md
    assert "全项通过情况" in md
    assert "判分说明（节选）" not in md
    assert "bad" not in md


def test_load_pending_rows(tmp_path: Path) -> None:
    p = tmp_path / "t1.json"
    p.write_text(
        json.dumps(
            {
                "task_id": "t1",
                "item": {
                    "question_id": "Q1",
                    "score": 77,
                    "score_reason": "r",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = _load_pending_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["score"] == 77
    assert rows[0]["question_id"] == "Q1"
