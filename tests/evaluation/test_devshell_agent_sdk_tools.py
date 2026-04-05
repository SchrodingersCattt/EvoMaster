"""Tests for immediate ingest submit behavior in DevShell agent SDK tools."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import patch

from evaluation.devshell_agent.config_state import (
    AgentLoopSharedState,
    DevshellAgentCliDefaults,
)


def _tool(*_args: object, **_kwargs: object):
    def _decorator(func: object) -> object:
        return func

    return _decorator


sys.modules.setdefault(
    "claude_agent_sdk",
    types.SimpleNamespace(
        create_sdk_mcp_server=lambda **kwargs: kwargs,
        tool=_tool,
    ),
)


def _sdk_tools_module():
    return importlib.import_module("evaluation.devshell_agent.sdk_tools")


def _build_state(tmp_path: Path) -> AgentLoopSharedState:
    state = AgentLoopSharedState(
        repo_root=tmp_path,
        session_dir=tmp_path / "session",
        outcomes=[],
        defaults=DevshellAgentCliDefaults(
            modes=["direct"],
            jobs=2,
            limit=1,
            questions=None,
            capabilities=None,
            model="claude-sonnet-4-6",
            exp=None,
            eval_ingest_pending_only=True,
            no_export_review=False,
            task_timeout_sec=600.0,
            eval_config=None,
            extra_args=[],
        ),
    )
    state.eval_ingest_submit_each_iteration = True
    state.eval_ingest_submit_timeout = 42.0
    return state


def test_run_devshell_eval_submits_immediately_after_run(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit
    toolkit = toolkit_cls(state)
    run_dir = tmp_path / "session" / "eval_runs" / "iter_01"
    pending_dir = run_dir / "pending_ingest"
    pending_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "raw_runs.jsonl").write_text("{}\n", encoding="utf-8")
    (pending_dir / "SC_struct_001_direct_r0.json").write_text(
        "{}\n",
        encoding="utf-8",
    )

    with (
        patch.object(
            toolkit._subprocess,
            "run_capture",
            autospec=True,
            return_value=(0, "stdout\n", "stderr\n"),
        ) as mock_run,
        patch.object(
            _sdk_tools_module(),
            "run_score_devshell_tasks_submit",
            return_value=(0, "", ""),
        ) as mock_submit,
    ):
        result = asyncio.run(toolkit._run_devshell_eval({"iteration_tag": "iter_01"}))

    assert result["is_error"] is False
    assert mock_run.call_count == 1
    mock_submit.assert_called_once_with(
        repo_root=tmp_path,
        run_dir=run_dir,
        eval_config=None,
        eval_ingest_timeout=42.0,
    )


def test_run_devshell_eval_skips_immediate_submit_when_pending_only_disabled(
    tmp_path: Path,
) -> None:
    state = _build_state(tmp_path)
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit
    toolkit = toolkit_cls(state)

    with (
        patch.object(
            toolkit._subprocess,
            "run_capture",
            autospec=True,
            return_value=(0, "stdout\n", "stderr\n"),
        ),
        patch.object(
            _sdk_tools_module(),
            "run_score_devshell_tasks_submit",
            return_value=(0, "", ""),
        ) as mock_submit,
    ):
        result = asyncio.run(
            toolkit._run_devshell_eval(
                {
                    "iteration_tag": "iter_01",
                    "eval_ingest_pending_only": False,
                }
            )
        )

    assert result["is_error"] is False
    mock_submit.assert_not_called()
