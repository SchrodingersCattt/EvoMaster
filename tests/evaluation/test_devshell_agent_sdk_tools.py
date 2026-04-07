"""Tests for immediate ingest submit behavior in DevShell agent SDK tools."""

from __future__ import annotations

import asyncio
import io
import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

from evaluation.devshell_agent.config_state import (
    AgentLoopSharedState,
    DevshellAgentCliDefaults,
)
from evaluation.devshell_agent.loop import (
    AgentLoopConfig,
    DevshellAgentLoop,
    checklist_max_turns_for_shared_state,
)


def _tool(*_args: object, **_kwargs: object):
    def _decorator(func: object) -> object:
        return func

    return _decorator


class _DummyClaudeAgentOptions:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeClaudeClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self) -> "_FakeClaudeClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def query(self, _message: str) -> None:
        return None

    async def receive_response(self):
        if False:
            yield None


sys.modules.setdefault(
    "claude_agent_sdk",
    types.SimpleNamespace(
        create_sdk_mcp_server=lambda **kwargs: kwargs,
        ClaudeAgentOptions=_DummyClaudeAgentOptions,
        ClaudeSDKClient=_FakeClaudeClient,
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


def _build_config(tmp_path: Path) -> AgentLoopConfig:
    return AgentLoopConfig(
        repo_root=tmp_path,
        session_dir=tmp_path / "session",
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
        max_iterations=2,
        target_mean_score=80,
        permission_mode="acceptEdits",
        max_sdk_turns=100,
    )


def test_run_devshell_eval_submits_immediately_after_run(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit
    toolkit = toolkit_cls(state)
    run_dir = tmp_path / "session" / "eval_runs" / "iter_01"
    pending_dir = run_dir / "pending_ingest"
    pending_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "raw_runs.jsonl").write_text("{}\n", encoding="utf-8")
    (pending_dir / "SC_struct_001_direct_r0.json").write_text(
        json.dumps(
            {
                "item": {
                    "score": 65,
                    "score_reason": "Checklist wording says too much about the task.",
                }
            }
        )
        + "\n",
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
    assert '"macro_mean_0_100": 65' in result["content"][0]["text"]
    assert "Checklist wording says too much about the task." not in result["content"][0]["text"]
    assert mock_run.call_count == 1
    mock_submit.assert_called_once_with(
        repo_root=tmp_path,
        run_dir=run_dir,
        eval_config=None,
        eval_ingest_timeout=42.0,
        score_jobs=2,
        parallel_checklist_workers=4,
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


def test_checklist_revision_sdk_max_turns_floor(tmp_path: Path) -> None:
    state = _build_state(tmp_path)

    assert checklist_max_turns_for_shared_state(state) == 64


def test_delegate_optimization_records_round_and_payload(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit
    toolkit = toolkit_cls(state)

    result = asyncio.run(
        toolkit._delegate_optimization(
            {
                "iteration_index": 1,
                "problem_summary": "Need stronger reusable workflow guidance.",
                "symptom": "Low score due to missing deliverable structure.",
                "suggested_focus": ["matmaster/skills"],
                "allowed_evidence_paths": ["matmaster/skills/result-analysis/SKILL.md"],
                "notes": "Do not expose raw rubric text.",
            }
        )
    )

    assert result["is_error"] is False
    assert state.optimization_delegations_pending == [
        {
            "iteration_index": 1,
            "optimization_round": 1,
            "problem_summary": "Need stronger reusable workflow guidance.",
            "symptom": "Low score due to missing deliverable structure.",
            "suggested_focus": ["matmaster/skills"],
            "allowed_evidence_paths": ["matmaster/skills/result-analysis/SKILL.md"],
            "notes": "Do not expose raw rubric text.",
        }
    ]


def test_report_optimization_result_persists_jsonl(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit
    toolkit = toolkit_cls(state)

    asyncio.run(
        toolkit._report_optimization_result(
            {
                "iteration_index": 1,
                "optimization_round": 2,
                "summary": "Updated reusable skill instructions.",
                "files_touched": ["matmaster/skills/demo/SKILL.md"],
                "commit_shas": ["abc1234"],
                "needs_more_work": False,
                "followup_suggestion": "Re-run eval.",
            }
        )
    )

    assert state.optimization_reports == [
        {
            "iteration_index": 1,
            "optimization_round": 2,
            "summary": "Updated reusable skill instructions.",
            "files_touched": ["matmaster/skills/demo/SKILL.md"],
            "commit_shas": ["abc1234"],
            "needs_more_work": False,
            "followup_suggestion": "Re-run eval.",
        }
    ]
    log_path = tmp_path / "session" / "optimization_reports.jsonl"
    assert log_path.is_file()


def test_main_agent_allowed_tools_exclude_edit_write_and_bash() -> None:
    allowed = DevshellAgentLoop.main_agent_allowed_tools()

    assert "Read" not in allowed
    assert "Glob" not in allowed
    assert "Grep" not in allowed
    assert "Edit" not in allowed
    assert "Write" not in allowed
    assert "Bash" not in allowed
    assert "mcp__matmaster_eval__delegate_optimization" in allowed


def test_optimization_followup_needed_only_when_queue_has_current_iteration(
    tmp_path: Path,
) -> None:
    state = _build_state(tmp_path)
    state.optimization_delegations_pending.append(
        {
            "iteration_index": 2,
            "optimization_round": 1,
            "problem_summary": "demo",
            "symptom": "demo",
            "suggested_focus": ["matmaster/skills"],
            "allowed_evidence_paths": ["matmaster/skills/demo/SKILL.md"],
            "notes": "demo",
        }
    )

    loop = DevshellAgentLoop(_build_config(tmp_path))

    assert loop._optimization_escalations_for_iteration(1, state) == []
    assert len(loop._optimization_escalations_for_iteration(2, state)) == 1


def test_run_optimization_followup_returns_warning_when_report_missing(
    tmp_path: Path,
) -> None:
    state = _build_state(tmp_path)
    state.optimization_delegations_pending.append(
        {
            "iteration_index": 1,
            "optimization_round": 1,
            "problem_summary": "demo",
            "symptom": "demo",
            "suggested_focus": ["matmaster/skills"],
            "allowed_evidence_paths": ["matmaster/skills/demo/SKILL.md"],
            "notes": "demo",
        }
    )
    loop = DevshellAgentLoop(_build_config(tmp_path))

    rc = asyncio.run(
        loop._run_optimization_followups_if_needed(
            it=1,
            state=state,
            mcp_server={},
            loop_log=io.StringIO(),
        )
    )

    assert rc == 1


def test_default_history_dir_is_outside_results(tmp_path: Path) -> None:
    cfg = _build_config(tmp_path)
    loop = DevshellAgentLoop(cfg)

    history_dir = loop._history_root()

    assert history_dir == tmp_path / "evaluation" / "devshell_agent_history"
    assert "results" not in str(history_dir)


def test_optimization_agent_uses_restricted_mcp_fs_tools_only() -> None:
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit

    tool_names = toolkit_cls.optimization_agent_tool_names()

    assert "Read" not in tool_names
    assert "Edit" not in tool_names
    assert "Write" not in tool_names
    assert "Bash" not in tool_names
    assert "mcp__matmaster_eval__optimization_read_text" in tool_names
    assert "mcp__matmaster_eval__optimization_replace_text" in tool_names


def test_checklist_agent_uses_restricted_mcp_fs_tools_only() -> None:
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit

    tool_names = toolkit_cls.checklist_agent_tool_names()

    assert "Read" not in tool_names
    assert "Edit" not in tool_names
    assert "Write" not in tool_names
    assert "Bash" not in tool_names
    assert "mcp__matmaster_eval__checklist_read_text" in tool_names
    assert "mcp__matmaster_eval__checklist_replace_text" in tool_names


def test_optimization_path_guard_blocks_evaluation_reads(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit
    toolkit = toolkit_cls(state)

    try:
        toolkit._resolve_agent_path(
            "evaluation/question_bank/structure_construction/sc_struct.yaml",
            role="optimization",
            write=False,
        )
    except ValueError as exc:
        assert "optimization" in str(exc)
    else:
        raise AssertionError("expected optimization path guard to reject evaluation")


def test_checklist_path_guard_blocks_product_writes(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit
    toolkit = toolkit_cls(state)

    try:
        toolkit._resolve_agent_path(
            "matmaster/skills/result-analysis/SKILL.md",
            role="checklist",
            write=True,
        )
    except ValueError as exc:
        assert "checklist" in str(exc)
    else:
        raise AssertionError("expected checklist path guard to reject product write")
