"""Smoke tests for ``scripts/run_devshell_eval.py`` (dry-run only; no LLM)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "run_devshell_eval.py"


def test_devshell_eval_dry_run_limit_one() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--limit", "1"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Planned tasks: 1" in proc.stderr
    assert "[dry-run]" in proc.stderr


def test_devshell_eval_empty_plan_limit_zero() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--limit", "0"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "No tasks in plan" in proc.stderr
