"""Tests for immediate ingest submit behavior in DevShell agent SDK tools."""

from __future__ import annotations

import asyncio
import importlib
import io
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

    async def __aenter__(self) -> _FakeClaudeClient:
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


def _sdk_tools_eval_run_module():
    return importlib.import_module("evaluation.devshell_agent.sdk_tools_eval_run")


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
            slices=None,
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
            slices=None,
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

    qb_mod = importlib.import_module("evaluation.devshell_agent.question_bank_ids")
    with (
        patch.object(
            toolkit._subprocess,
            "run_capture",
            autospec=True,
            return_value=(0, "stdout\n", "stderr\n"),
        ) as mock_run,
        patch.object(
            _sdk_tools_eval_run_module(),
            "run_score_devshell_tasks_submit",
            return_value=(0, "", ""),
        ) as mock_submit,
        patch.object(qb_mod, "collect_p0_question_ids", return_value=[]),
    ):
        result = asyncio.run(toolkit._run_devshell_eval({"iteration_tag": "iter_01"}))

    assert result["is_error"] is False
    assert '"macro_mean_0_100": 65' in result["content"][0]["text"]
    assert (
        "Checklist wording says too much about the task."
        not in result["content"][0]["text"]
    )
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

    qb_mod = importlib.import_module("evaluation.devshell_agent.question_bank_ids")
    with (
        patch.object(
            toolkit._subprocess,
            "run_capture",
            autospec=True,
            return_value=(0, "stdout\n", "stderr\n"),
        ),
        patch.object(
            _sdk_tools_eval_run_module(),
            "run_score_devshell_tasks_submit",
            return_value=(0, "", ""),
        ) as mock_submit,
        patch.object(qb_mod, "collect_p0_question_ids", return_value=[]),
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


def test_merge_p0_and_rest_into_base_run_dir_merges_artifacts(tmp_path: Path) -> None:
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit
    base = tmp_path / "iter_tag"
    p0 = base / "p0_gate"
    rest = base / "remaining"
    (p0 / "workspaces" / "t1").mkdir(parents=True)
    (p0 / "workspaces" / "t1" / "out.txt").write_text("p0", encoding="utf-8")
    (p0 / "logs" / "t1").mkdir(parents=True)
    (p0 / "pending_ingest").mkdir(parents=True)
    (p0 / "pending_ingest" / "t1.json").write_text(
        '{"item":{"question_id":"q1"}}', encoding="utf-8"
    )
    (p0 / "raw_runs.jsonl").write_text('{"task_id":"t1"}\n', encoding="utf-8")
    (rest / "workspaces" / "t2").mkdir(parents=True)
    (rest / "pending_ingest").mkdir(parents=True)
    (rest / "pending_ingest" / "t2.json").write_text(
        '{"item":{"question_id":"q2"}}', encoding="utf-8"
    )
    (rest / "raw_runs.jsonl").write_text('{"task_id":"t2"}\n', encoding="utf-8")

    toolkit_cls._merge_p0_and_rest_into_base_run_dir(base, p0, rest)

    merged = (base / "raw_runs.jsonl").read_text(encoding="utf-8")
    assert merged.count('"task_id"') == 2
    assert (base / "workspaces" / "t1" / "out.txt").read_text() == "p0"
    assert (base / "pending_ingest" / "t1.json").is_file()
    assert (base / "pending_ingest" / "t2.json").is_file()


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
                "candidate_layers": ["skill"],
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
            "candidate_layers": ["skill"],
            "execution_track": "code_edit",
            "failure_buckets": [],
            "capabilities_affected": [],
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
    assert "mcp__matmaster_eval__main_read_text" in allowed
    assert "mcp__matmaster_eval__main_glob_paths" in allowed
    assert "mcp__matmaster_eval__main_grep_text" in allowed


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
            "failure_buckets": [],
            "capabilities_affected": [],
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
            "failure_buckets": [],
            "capabilities_affected": [],
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


def test_optimization_user_message_guides_system_prompt_candidates_to_proposal(
    tmp_path: Path,
) -> None:
    loop = DevshellAgentLoop(_build_config(tmp_path))

    message = loop._optimization_user_message(
        it=1,
        delegation={
            "iteration_index": 1,
            "optimization_round": 1,
            "problem_summary": "Need cross-task execution contract cleanup.",
            "symptom": "Same delivery-policy issue across tasks.",
            "suggested_focus": ["matmaster/exps"],
            "candidate_layers": ["system_prompt"],
            "allowed_evidence_paths": [],
            "notes": "proposal only",
        },
    )

    assert "candidate_layers" in message
    assert "`system_prompt`" in message
    assert (
        "默认不要修改 `matmaster/skills/`、`matmaster/tools/`、`src/` 等产品代码"
        in message
    )
    assert (
        "优先读取现有 `matmaster/exps/_base.toml` / `matmaster/exps/direct.toml`"
        in message
    )
    assert "`proposed_matmaster_exps_changes.md`" in message
    assert "Target file" in message
    assert "Existing rule(s) to replace or merge" in message
    assert "Prompt budget impact" in message


def test_optimization_user_message_guides_skill_and_tool_candidates(
    tmp_path: Path,
) -> None:
    loop = DevshellAgentLoop(_build_config(tmp_path))

    message = loop._optimization_user_message(
        it=1,
        delegation={
            "iteration_index": 1,
            "optimization_round": 2,
            "problem_summary": "Need sharper reusable guidance.",
            "symptom": "Agent chooses wrong layer repeatedly.",
            "suggested_focus": ["matmaster/skills", "matmaster/tools"],
            "candidate_layers": ["skill", "tool"],
            "allowed_evidence_paths": [],
            "notes": "layered fix",
        },
    )

    assert "`skill`" in message
    assert "`tool`" in message
    assert (
        "优先检查 `matmaster/skills/`，并遵守 `SKILL.md` / `references` / `scripts` 分层约束"
        in message
    )
    assert "优先检查 `matmaster/tools/` 与相关 tool descriptions" in message


def test_proposal_only_optimization_skips_auto_commit_and_records_track(
    tmp_path: Path,
) -> None:
    loop = DevshellAgentLoop(_build_config(tmp_path))
    state = _build_state(tmp_path)
    state.optimization_reports.append(
        {
            "iteration_index": 1,
            "optimization_round": 1,
            "summary": "Wrote proposal only.",
            "files_touched": [],
            "commit_shas": [],
            "needs_more_work": True,
            "followup_suggestion": "Review proposal.",
        }
    )
    delegation = {
        "iteration_index": 1,
        "optimization_round": 1,
        "problem_summary": "Need cross-task contract cleanup.",
        "symptom": "Prompt layer issue.",
        "suggested_focus": ["matmaster/exps"],
        "candidate_layers": ["system_prompt"],
        "execution_track": "proposal_only",
        "allowed_evidence_paths": [],
        "notes": "proposal only",
    }

    with patch(
        "evaluation.devshell_agent.optimization_auto_commit.commit_optimization_changes"
    ) as mock_commit:
        loop._apply_optimization_auto_commit(
            it=1,
            delegation=delegation,
            state=state,
            loop_log=io.StringIO(),
        )

    mock_commit.assert_not_called()
    track_path = tmp_path / "session" / "optimization_proposal_tracks.jsonl"
    assert track_path.is_file()
    rows = [
        json.loads(line)
        for line in track_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[-1]["execution_track"] == "proposal_only"
    assert rows[-1]["candidate_layers"] == ["system_prompt"]


def test_optimization_agent_uses_restricted_mcp_fs_tools_only() -> None:
    toolkit_cls = _sdk_tools_module().MatmasterEvalMcpToolkit

    tool_names = toolkit_cls.optimization_agent_tool_names()

    assert "Read" not in tool_names
    assert "Edit" not in tool_names
    assert "Write" not in tool_names
    assert "Bash" not in tool_names
    assert "mcp__matmaster_eval__optimization_read_text" in tool_names
    assert "mcp__matmaster_eval__optimization_replace_text" in tool_names
    assert "mcp__matmaster_eval__git_revert_commits_after_base" in tool_names


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
            "evaluation/question_bank/structure_construction/sc_agnostic.yaml",
            role="optimization",
            write=False,
        )
    except ValueError as exc:
        assert "optimization" in str(exc)
    else:
        raise AssertionError("expected optimization path guard to reject evaluation")


def test_optimization_cannot_write_matmaster_exps(tmp_path: Path) -> None:
    (tmp_path / "matmaster" / "exps").mkdir(parents=True)
    (tmp_path / "matmaster" / "exps" / "direct.toml").write_text('name = "direct"\n')
    (tmp_path / "matmaster" / "exps" / "explore.toml").write_text('name = "explore"\n')
    state = _build_state(tmp_path)
    toolkit = _sdk_tools_module().MatmasterEvalMcpToolkit(state)
    for rel in ("matmaster/exps/direct.toml", "matmaster/exps/explore.toml"):
        try:
            toolkit._resolve_agent_path(rel, role="optimization", write=True)
        except ValueError as exc:
            assert "matmaster/exps" in str(exc)
            assert "proposed_matmaster_exps_changes" in str(exc)
        else:
            raise AssertionError(f"expected block on {rel} write")


def test_optimization_writes_proposal_only_under_session(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit = _sdk_tools_module().MatmasterEvalMcpToolkit(state)
    proposal = state.session_dir / "proposed_matmaster_exps_changes.md"
    resolved = toolkit._resolve_agent_path(
        str(proposal),
        role="optimization",
        write=True,
    )
    assert resolved == proposal.resolve()

    try:
        toolkit._resolve_agent_path(
            str(state.session_dir / "notes.md"),
            role="optimization",
            write=True,
        )
    except ValueError as exc:
        assert "proposed_matmaster_exps_changes" in str(exc)
    else:
        raise AssertionError("expected block on arbitrary session file write")


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


def test_checklist_cannot_write_question_bank_yaml(tmp_path: Path) -> None:
    (tmp_path / "evaluation" / "question_bank" / "b").mkdir(parents=True)
    (tmp_path / "evaluation" / "question_bank" / "b" / "q.yaml").write_text(
        "id: x\n", encoding="utf-8"
    )
    state = _build_state(tmp_path)
    toolkit = _sdk_tools_module().MatmasterEvalMcpToolkit(state)
    try:
        toolkit._resolve_agent_path(
            "evaluation/question_bank/b/q.yaml",
            role="checklist",
            write=True,
        )
    except ValueError as exc:
        assert "proposed_question_bank_changes" in str(exc)
    else:
        raise AssertionError("expected block on direct question_bank write")


def test_checklist_writes_proposal_only_under_session(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit = _sdk_tools_module().MatmasterEvalMcpToolkit(state)
    proposal = state.session_dir / "proposed_question_bank_changes.md"
    resolved = toolkit._resolve_agent_path(
        str(proposal),
        role="checklist",
        write=True,
    )
    assert resolved == proposal.resolve()

    try:
        toolkit._resolve_agent_path(
            str(state.session_dir / "notes.md"),
            role="checklist",
            write=True,
        )
    except ValueError as exc:
        assert "proposed_question_bank_changes" in str(exc)
    else:
        raise AssertionError("expected block on arbitrary session file write")


def test_main_path_guard_allows_history_session_reads(tmp_path: Path) -> None:
    hist = (
        tmp_path
        / "evaluation"
        / "devshell_agent_history"
        / "session"
        / "iterations"
        / "iter_01.json"
    )
    hist.parent.mkdir(parents=True)
    hist.write_text("{}\n", encoding="utf-8")
    state = _build_state(tmp_path)
    toolkit = _sdk_tools_module().MatmasterEvalMcpToolkit(state)
    resolved = toolkit._resolve_agent_path(
        "evaluation/devshell_agent_history/session/iterations/iter_01.json",
        role="main",
        write=False,
    )
    assert resolved == hist.resolve()


def test_main_path_guard_blocks_other_evaluation_reads(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    toolkit = _sdk_tools_module().MatmasterEvalMcpToolkit(state)
    try:
        toolkit._resolve_agent_path(
            "evaluation/question_bank/structure_construction/sc_agnostic.yaml",
            role="main",
            write=False,
        )
    except ValueError as exc:
        assert "main" in str(exc)
    else:
        raise AssertionError("expected main path guard to reject evaluation")


def test_main_path_guard_allows_other_session_under_history_tree(
    tmp_path: Path,
) -> None:
    other = (
        tmp_path
        / "evaluation"
        / "devshell_agent_history"
        / "other_session"
        / "session_summary.json"
    )
    other.parent.mkdir(parents=True)
    other.write_text("{}\n", encoding="utf-8")
    state = _build_state(tmp_path)
    toolkit = _sdk_tools_module().MatmasterEvalMcpToolkit(state)
    resolved = toolkit._resolve_agent_path(
        "evaluation/devshell_agent_history/other_session/session_summary.json",
        role="main",
        write=False,
    )
    assert resolved == other.resolve()


def test_main_path_guard_allows_index_jsonl(tmp_path: Path) -> None:
    index_path = tmp_path / "evaluation" / "devshell_agent_history" / "index.jsonl"
    index_path.parent.mkdir(parents=True)
    index_path.write_text("{}\n", encoding="utf-8")
    state = _build_state(tmp_path)
    toolkit = _sdk_tools_module().MatmasterEvalMcpToolkit(state)
    resolved = toolkit._resolve_agent_path(
        "evaluation/devshell_agent_history/index.jsonl",
        role="main",
        write=False,
    )
    assert resolved == index_path.resolve()
