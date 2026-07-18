"""Smoke tests for ``evaluation/scripts/devshell/run_devshell_eval.py`` (dry-run only; no LLM)."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "evaluation" / "scripts" / "devshell" / "run_devshell_eval.py"


def test_bohrium_prod_env_override_is_scoped(tmp_path) -> None:
    mod = importlib.import_module("evaluation.scripts.devshell.run_devshell_eval")
    (tmp_path / ".env.prod").write_text(
        "\n".join(
            (
                "BOHRIUM_ACCESS_KEY=prod-ak",
                "BOHRIUM_PROJECT_ID=123",
                "BOHRIUM_USER_ID=456",
                "MYSQL_PASSWORD=must-not-be-loaded",
            )
        ),
        encoding="utf-8",
    )
    env = {
        "SERVICE_ENV": "test",
        "BOHRIUM_ACCESS_KEY": "test-ak",
        "BOHRIUM_ORG_ID": "test-org",
        "BOHRIUM_USE_SANDBOX": "1",
        "MYSQL_PASSWORD": "test-db-password",
    }

    mod._apply_bohrium_env_override(env, repo_root=tmp_path, environment="prod")

    assert env["SERVICE_ENV"] == "test"
    assert env["BOHRIUM_ACCESS_KEY"] == "prod-ak"
    assert env["BOHRIUM_PROJECT_ID"] == "123"
    assert env["BOHRIUM_USER_ID"] == "456"
    assert "BOHRIUM_ORG_ID" not in env
    assert env["BOHRIUM_BASE_URL"] == "https://openapi.dp.tech"
    assert env["BOHRIUM_USE_SANDBOX"] == "1"
    assert env["MYSQL_PASSWORD"] == "test-db-password"


def test_bohrium_env_override_requires_access_key(tmp_path) -> None:
    mod = importlib.import_module("evaluation.scripts.devshell.run_devshell_eval")
    (tmp_path / ".env.prod").write_text("BOHRIUM_PROJECT_ID=123\n", encoding="utf-8")

    with pytest.raises(ValueError, match="BOHRIUM_ACCESS_KEY"):
        mod._apply_bohrium_env_override({}, repo_root=tmp_path, environment="prod")


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
        *,
        cmd,
        cwd,
        env,
        summary_file,
        console_log_file,
        timeout_sec=None,
        tee_stderr=False,
        console_log_append=False,
        **kwargs: Any,
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
    assert "--exclude-builtin-tool" not in cmd0
    assert "--model" in cmd0
    assert cmd0[cmd0.index("--model") + 1] == "bedrock-claude-opus"
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["eval_tooling"]["exp_config_name"] == "direct"
    assert "matmaster_exp" not in man
    assert man.get("model") == "bedrock-claude-opus"
    assert man.get("fallback_model") == "global.anthropic.claude-opus-4-6-v1"


def test_devshell_eval_bohr_cli_excludes_bohrium(tmp_path, monkeypatch) -> None:
    mod = importlib.import_module("evaluation.scripts.devshell.run_devshell_eval")
    out = (tmp_path / "bohr_cli_tools").resolve()
    captured: list[list[str | Path]] = []
    captured_envs: list[dict[str, str]] = []
    real_bin_dir = tmp_path / "real_bin"
    real_bin_dir.mkdir()
    real_bohr = real_bin_dir / "bohr"
    real_bohr.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    real_bohr.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f'{real_bin_dir}{os.pathsep}{os.environ.get("PATH", "")}',
    )

    def fake_run_devshell_task(
        *,
        cmd,
        env,
        summary_file,
        **kwargs: Any,
    ):
        captured.append(list(cmd))
        captured_envs.append(env)
        summary = {
            "status": "completed",
            "reason": "natural",
            "final_content": "ok",
            "num_turns": 1,
            "usage": {"total_tokens": 1},
        }
        summary_file.write_text(
            json.dumps(summary, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return 0, 123, summary

    monkeypatch.setattr(mod, "_run_devshell_task", fake_run_devshell_task)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--slices",
            "@bohr-cli",
            "--k",
            "1",
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
    cmd0 = [str(x) for x in captured[0]]
    index = cmd0.index("--exclude-builtin-tool")
    assert cmd0[index + 1] == "Bohrium"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["eval_tooling"]["tools_builtin_excluded"] == ["Bohrium"]
    assert "Bohrium" not in manifest["eval_tooling"]["builtin_tool_names"]
    assert manifest["bohr_cli_receipt_schema"] == "bohr_cli_receipt_v1"
    assert captured_envs[0]["BOHR_EVAL_REAL_BIN"] == str(real_bohr)
    assert captured_envs[0]["BOHR_EVAL_RECEIPT_PATH"].endswith(
        "/bohr_cli_receipts.jsonl"
    )
    assert captured_envs[0]["PATH"].split(os.pathsep)[0] == str(out / "_eval_bin")
    row = json.loads((out / "raw_runs.jsonl").read_text(encoding="utf-8"))
    assert row["eval_tooling"] == manifest["eval_tooling"]


def test_devshell_eval_no_verbose_disables_forwarding(tmp_path, monkeypatch) -> None:
    mod = importlib.import_module("evaluation.scripts.devshell.run_devshell_eval")
    out = (tmp_path / "verbose_disabled").resolve()
    captured: list[list[str | Path]] = []

    def fake_run_devshell_task(
        *,
        cmd,
        cwd,
        env,
        summary_file,
        console_log_file,
        timeout_sec=None,
        tee_stderr=False,
        console_log_append=False,
        **kwargs: Any,
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
    assert cmd0[cmd0.index("--model") + 1] == "bedrock-claude-opus"
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["eval_tooling"]["exp_config_name"] == "direct"
    assert "matmaster_exp" not in man


def test_devshell_eval_exp_direct_forwards_flag(tmp_path, monkeypatch) -> None:
    mod = importlib.import_module("evaluation.scripts.devshell.run_devshell_eval")
    out = (tmp_path / "exp_direct").resolve()
    captured: list[list[str | Path]] = []

    def fake_run_devshell_task(
        *,
        cmd,
        cwd,
        env,
        summary_file,
        console_log_file,
        timeout_sec=None,
        tee_stderr=False,
        console_log_append=False,
        **kwargs: Any,
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
    assert man["eval_tooling"]["exp_config_name"] == "direct"
    assert "matmaster_exp" not in man


def test_devshell_eval_provider_fallback_retries_once(tmp_path, monkeypatch) -> None:
    mod = importlib.import_module("evaluation.scripts.devshell.run_devshell_eval")
    out = (tmp_path / "provider_fb").resolve()
    calls: list[str] = []

    def fake_run_devshell_task(
        *,
        cmd,
        cwd,
        env,
        summary_file,
        console_log_file,
        timeout_sec=None,
        tee_stderr=False,
        console_log_append=False,
        **kwargs: Any,
    ) -> tuple[int, int, dict[str, Any]]:
        if not console_log_append:
            calls.append("primary")
            console_log_file.write_text(
                "botocore.exceptions.ReadTimeoutError: Read timeout on endpoint URL: "
                '"https://bedrock-runtime.us-east-1.amazonaws.com/x/converse-stream"\n',
                encoding="utf-8",
            )
            summary_file.write_text('{"parse_error":true}\n', encoding="utf-8")
            return (1, 50, {"parse_error": True})
        calls.append("fallback")
        cmd_s = [str(x) for x in cmd]
        assert "--model" in cmd_s
        assert (
            cmd_s[cmd_s.index("--model") + 1] == "global.anthropic.claude-opus-4-6-v1"
        )
        summary_file.write_text(
            '{"status":"completed","reason":"natural","final_content":"ok",'
            '"num_turns":1,"usage":{"total_tokens":1}}\n',
            encoding="utf-8",
        )
        return (
            0,
            80,
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
            "--limit",
            "1",
            "--output-dir",
            str(out),
            "--no-clean-results",
            "--no-eval-ingest",
            "--no-export-review",
            "--model",
            "bedrock-claude-opus",
            "--fallback-model",
            "global.anthropic.claude-opus-4-6-v1",
        ],
    )

    rc = mod.main()
    assert rc == 0
    assert calls == ["primary", "fallback"]
    raw_lines = (
        (out / "raw_runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    )
    row = json.loads(raw_lines[-1])
    assert row.get("llm_provider_fallback_used") is True
    assert row.get("llm_model_route_used") == "global.anthropic.claude-opus-4-6-v1"
