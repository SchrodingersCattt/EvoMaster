"""Smoke tests for ``evaluation/scripts/devshell/run_devshell_eval.py`` (dry-run only; no LLM)."""

from __future__ import annotations

import importlib
import json
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


def test_devshell_eval_verbose_is_on_by_default(tmp_path, monkeypatch) -> None:
    mod = importlib.import_module("evaluation.scripts.devshell.run_devshell_eval")
    out = (tmp_path / "verbose_default").resolve()
    captured: list[list[str | Path]] = []

    def fake_run_devshell_task(
        *, cmd, cwd, env, summary_file, console_log_file, timeout_sec=None
    ):
        captured.append(list(cmd))
        summary_file.write_text(
            '{"status":"completed","reason":"natural","final_content":"ok","num_turns":1,"usage":{"total_tokens":1}}\n',
            encoding="utf-8",
        )
        return (
            0,
            123,
            {
                "status": "completed",
                "reason": "natural",
                "final_content": "ok",
                "num_turns": 1,
                "usage": {"total_tokens": 1},
            },
        )

    monkeypatch.setattr(mod, "_run_devshell_task", fake_run_devshell_task)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--modes",
            "direct",
            "--limit",
            "1",
            "--output-dir",
            str(out),
            "--no-clean-results",
            "--no-eval-ingest",
            "--no-export-review",
        ],
    )

    rc = mod.main()

    assert rc == 0
    assert captured
    cmd0 = [str(x) for x in captured[0]]
    assert "--verbose" in cmd0
    assert "--exp" not in cmd0
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["matmaster_exp"] == "devshell"


def test_devshell_eval_no_verbose_disables_forwarding(tmp_path, monkeypatch) -> None:
    mod = importlib.import_module("evaluation.scripts.devshell.run_devshell_eval")
    out = (tmp_path / "verbose_disabled").resolve()
    captured: list[list[str | Path]] = []

    def fake_run_devshell_task(
        *, cmd, cwd, env, summary_file, console_log_file, timeout_sec=None
    ):
        captured.append(list(cmd))
        summary_file.write_text(
            '{"status":"completed","reason":"natural","final_content":"ok","num_turns":1,"usage":{"total_tokens":1}}\n',
            encoding="utf-8",
        )
        return (
            0,
            123,
            {
                "status": "completed",
                "reason": "natural",
                "final_content": "ok",
                "num_turns": 1,
                "usage": {"total_tokens": 1},
            },
        )

    monkeypatch.setattr(mod, "_run_devshell_task", fake_run_devshell_task)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--modes",
            "direct",
            "--limit",
            "1",
            "--output-dir",
            str(out),
            "--no-clean-results",
            "--no-eval-ingest",
            "--no-export-review",
            "--no-verbose",
        ],
    )

    rc = mod.main()

    assert rc == 0
    assert captured
    cmd0 = [str(x) for x in captured[0]]
    assert "--verbose" not in cmd0
    assert "--exp" not in cmd0
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["matmaster_exp"] == "devshell"


def test_devshell_eval_exp_direct_forwards_flag(tmp_path, monkeypatch) -> None:
    mod = importlib.import_module("evaluation.scripts.devshell.run_devshell_eval")
    out = (tmp_path / "exp_direct").resolve()
    captured: list[list[str | Path]] = []

    def fake_run_devshell_task(
        *, cmd, cwd, env, summary_file, console_log_file, timeout_sec=None
    ):
        captured.append(list(cmd))
        summary_file.write_text(
            '{"status":"completed","reason":"natural","final_content":"ok","num_turns":1,"usage":{"total_tokens":1}}\n',
            encoding="utf-8",
        )
        return (
            0,
            123,
            {
                "status": "completed",
                "reason": "natural",
                "final_content": "ok",
                "num_turns": 1,
                "usage": {"total_tokens": 1},
            },
        )

    monkeypatch.setattr(mod, "_run_devshell_task", fake_run_devshell_task)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--modes",
            "direct",
            "--limit",
            "1",
            "--output-dir",
            str(out),
            "--no-clean-results",
            "--no-eval-ingest",
            "--no-export-review",
            "--exp",
            "direct",
        ],
    )

    rc = mod.main()

    assert rc == 0
    assert captured
    cmd0 = [str(x) for x in captured[0]]
    assert "--exp" in cmd0
    assert cmd0[cmd0.index("--exp") + 1] == "direct"
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["matmaster_exp"] == "direct"
