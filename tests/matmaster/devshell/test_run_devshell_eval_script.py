"""Smoke tests for ``evaluation/scripts/devshell/run_devshell_eval.py`` (dry-run only; no LLM)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "evaluation" / "scripts" / "devshell" / "run_devshell_eval.py"


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


def test_prepare_cc_baseline_rejects_dry_run() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--dry-run",
            "--prepare-cc-baseline",
            "--limit",
            "1",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "cannot be used together" in proc.stderr


def test_prepare_cc_baseline_writes_task_meta(tmp_path) -> None:
    out = (tmp_path / "cc_baseline_smoke").resolve()
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--prepare-cc-baseline",
            "--modes",
            "direct",
            "--limit",
            "1",
            "--no-eval-ingest",
            "--output-dir",
            str(out),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    ws_dirs = list((out / "workspaces").iterdir())
    assert len(ws_dirs) == 1
    assert (ws_dirs[0] / "_eval_task_meta.json").is_file()
    assert (ws_dirs[0] / "_devshell_prompt.txt").is_file()
    assert (out / "CC_BASELINE.md").is_file()
