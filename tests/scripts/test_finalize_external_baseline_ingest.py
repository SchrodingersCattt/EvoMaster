"""Smoke tests for ``evaluation/scripts/baseline/finalize_external_baseline_ingest.py`` (no HTTP / OSS)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FINALIZE = (
    REPO_ROOT
    / "evaluation"
    / "scripts"
    / "baseline"
    / "finalize_external_baseline_ingest.py"
)


def test_finalize_external_baseline_no_ingest(tmp_path) -> None:
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
    assert row["baseline_duration_source"] == "no_cc_baseline_clock"
    assert row["duration_ms"] is None


def test_finalize_external_baseline_uses_clock_file(tmp_path) -> None:
    run_dir = (tmp_path / "run").resolve()
    tid = "Q_clock_direct_r0"
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
                "question_id": "Q_clock",
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
    start_ms = int(time.time() * 1000) - 12_000
    (ws / "_cc_baseline_task_start.json").write_text(
        json.dumps(
            {
                "started_at_unix_ms": start_ms,
                "schema": "matmaster_cc_baseline_task_start_v1",
            }
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
    line = (
        (run_dir / "raw_runs.jsonl").read_text(encoding="utf-8").strip().splitlines()[0]
    )
    row = json.loads(line)
    assert row["baseline_duration_source"] == "cc_baseline_clock"
    assert row["duration_ms"] != 999
    assert row["duration_ms"] >= 10_000
    assert row["duration_ms"] <= 60_000
