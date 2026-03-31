"""Tests for evaluation/scripts/devshell/export_devshell_review_bundle.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_export_review_bundle_smoke(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    row = {
        "task_id": "Q1_direct_r0",
        "question_id": "Q1",
        "capability": "batch_processing",
        "domain": "struct",
        "mode": "direct",
        "repeat_idx": 0,
        "devshell_exit_code": 0,
        "devshell_summary_path": str(
            run_dir / "workspaces" / "Q1_direct_r0" / "_devshell_summary.json"
        ),
        "devshell_summary": {
            "status": "completed",
            "reason": "natural",
            "final_content": "done",
        },
    }
    (run_dir / "raw_runs.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"plan_count": 1, "question_bank_dir": "noop"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    ws = run_dir / "workspaces" / "Q1_direct_r0"
    ws.mkdir(parents=True)
    (ws / "_devshell_summary.json").write_text(
        '{"reason":"natural"}\n', encoding="utf-8"
    )
    (ws / "out.txt").write_text("x", encoding="utf-8")

    repo = Path(__file__).resolve().parents[3]
    script = (
        repo
        / "evaluation"
        / "scripts"
        / "devshell"
        / "export_devshell_review_bundle.py"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--run-dir",
            str(run_dir),
            "--out",
            str(run_dir / "claude_review.md"),
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    md = (run_dir / "claude_review.md").read_text(encoding="utf-8")
    assert "Q1_direct_r0" in md
    assert "final_content" in md or "done" in md
    assert "out.txt" in md
