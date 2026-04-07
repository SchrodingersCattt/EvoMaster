"""Tests for devshell eval Feishu notification helpers."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.devshell_agent.feishu_round_notify import (
    _build_markdown_body,
    _load_pending_rows,
    _macro_mean,
)


def test_macro_mean() -> None:
    assert _macro_mean([80, 100, None]) == 90.0
    assert _macro_mean([]) is None


def test_build_markdown_success(tmp_path: Path) -> None:
    rows = [
        {
            "question_id": "Q1",
            "score": 80,
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
    assert "80" in md
    assert "宏平均" in md


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
