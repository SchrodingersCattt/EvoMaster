"""Smoke tests for ``scripts/finalize_cc_baseline_ingest.py`` (no HTTP / OSS)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FINALIZE = REPO_ROOT / "scripts" / "finalize_cc_baseline_ingest.py"


def test_finalize_cc_baseline_no_ingest(tmp_path) -> None:
    run_dir = (tmp_path / "run").resolve()
    tid = "Q_test_direct_r0"
    ws = run_dir / "workspaces" / tid
    ws.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"eval_tooling": {"schema": "test"}}) + "\n",
        encoding="utf-8",
    )
    (ws / "_eval_task_meta.json").write_text(
        json.dumps(
            {
                "schema": "matmaster_eval_task_meta_v1",
                "task_id": tid,
                "question_id": "Q_test",
                "capability": "x",
                "domain": "y",
                "mode": "direct",
                "repeat_idx": 0,
                "prompt": "hello",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    summary = {
        "model": "test-model",
        "profile_key": "cc",
        "status": "completed",
        "reason": "natural",
        "final_content": "done",
        "num_turns": 3,
        "usage": {"total_tokens": 10},
        "duration_ms": 1000,
    }
    (ws / "_devshell_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(FINALIZE),
            "--run-dir",
            str(run_dir),
            "--no-eval-ingest",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    raw = run_dir / "raw_runs.jsonl"
    assert raw.is_file()
    line = raw.read_text(encoding="utf-8").strip().splitlines()[0]
    row = json.loads(line)
    assert row["task_id"] == tid
    assert row["devshell_exit_code"] == 0
    assert row["eval_ingest_message"] == "skipped_no_eval_ingest"
