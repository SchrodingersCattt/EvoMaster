"""Tests for evaluation.devshell_agent.git_iteration helpers."""

from __future__ import annotations

from pathlib import Path

from evaluation.devshell_agent.git_iteration import (
    append_iteration_head,
    head_at_iteration_start,
)


def test_head_at_iteration_start_latest_per_iteration(tmp_path: Path) -> None:
    session = tmp_path / "sess"
    append_iteration_head(session_dir=session, iteration=2, head="aaa")
    append_iteration_head(session_dir=session, iteration=2, head="bbb")
    assert head_at_iteration_start(session, 2) == "bbb"
    assert head_at_iteration_start(session, 1) is None


def test_head_at_iteration_start_malformed_line_skipped(tmp_path: Path) -> None:
    session = tmp_path / "sess"
    session.mkdir()
    path = session / "git_iteration_heads.jsonl"
    path.write_text(
        '{"iteration": 1, "head_at_start": "ok"}\nnot-json\n',
        encoding="utf-8",
    )
    assert head_at_iteration_start(session, 1) == "ok"
