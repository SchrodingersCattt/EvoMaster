"""Guardrails for source file line-count limits."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_devshell_loop_py_stays_under_line_limit() -> None:
    path = REPO_ROOT / "evaluation" / "devshell_agent" / "loop.py"
    line_count = len(path.read_text(encoding="utf-8").splitlines())

    assert line_count <= 1000, f"{path} has {line_count} lines"
