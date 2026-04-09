"""Tests for evaluation.devshell_agent.git_iteration helpers."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.devshell_agent.git_iteration import append_iteration_head


def test_append_iteration_head_appends_jsonl_rows(tmp_path: Path) -> None:
    session = tmp_path / "sess"
    append_iteration_head(session_dir=session, iteration=1, head="aaa")
    append_iteration_head(session_dir=session, iteration=2, head="bbb")
    path = session / "git_iteration_heads.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"iteration": 1, "head_at_start": "aaa"}
    assert json.loads(lines[1]) == {"iteration": 2, "head_at_start": "bbb"}
