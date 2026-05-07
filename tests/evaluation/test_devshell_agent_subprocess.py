"""Tests for DevShell agent loop subprocess argv building (no claude-agent-sdk)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from evaluation.devshell_agent.subprocess_runner import (
    DevshellEvalSubprocess,
    RunDevshellEvalParams,
    run_score_devshell_tasks,
)


def test_build_run_devshell_eval_argv_minimal() -> None:
    repo = Path("/repo")
    script = repo / "evaluation/scripts/devshell/run_devshell_eval.py"
    out = repo / "results/run1"
    params = RunDevshellEvalParams(
        output_dir=out,
        jobs=2,
        limit=3,
        questions=None,
        slices=None,
        model="claude-sonnet-4-6",
        exp=None,
        eval_ingest_pending_only=True,
        no_export_review=False,
        task_timeout_sec=600.0,
        eval_config=repo / "evaluation/config.yaml",
        extra_args=[],
    )
    invoker = DevshellEvalSubprocess(repo)
    argv = invoker.build_argv(script, params)
    assert str(script) in map(str, argv)
    assert "--jobs" in argv and "2" in argv
    assert "--limit" in argv and "3" in argv
    assert "--model" in argv and "claude-sonnet-4-6" in argv
    assert "--eval-ingest-pending-only" in argv
    assert "--output-dir" in argv
    assert str(out) in argv
    assert "--no-clean-results" in argv
    assert "--eval-config" in argv
    assert "--eval-ingest-run-id" not in argv
    assert "--k" not in argv


def test_build_run_devshell_eval_argv_k() -> None:
    repo = Path("/repo")
    script = repo / "evaluation/scripts/devshell/run_devshell_eval.py"
    out = repo / "results/run1"
    params = RunDevshellEvalParams(
        output_dir=out,
        jobs=1,
        limit=None,
        questions=None,
        slices=None,
        model=None,
        exp=None,
        eval_ingest_pending_only=True,
        no_export_review=False,
        task_timeout_sec=0.0,
        eval_config=None,
        extra_args=[],
        k=3,
    )
    invoker = DevshellEvalSubprocess(repo)
    argv = invoker.build_argv(script, params)
    i = argv.index("--k")
    assert argv[i + 1] == "3"


def test_build_run_devshell_eval_argv_eval_ingest_run_id() -> None:
    repo = Path("/repo")
    script = repo / "evaluation/scripts/devshell/run_devshell_eval.py"
    out = repo / "results/run1"
    params = RunDevshellEvalParams(
        output_dir=out,
        jobs=1,
        limit=None,
        questions=None,
        slices=None,
        model=None,
        exp=None,
        eval_ingest_pending_only=True,
        no_export_review=False,
        task_timeout_sec=0.0,
        eval_config=None,
        extra_args=[],
        eval_ingest_run_id="11111111-1111-1111-1111-111111111111",
    )
    invoker = DevshellEvalSubprocess(repo)
    argv = invoker.build_argv(script, params)
    i = argv.index("--eval-ingest-run-id")
    assert argv[i + 1] == "11111111-1111-1111-1111-111111111111"


def test_run_score_devshell_tasks_uses_in_process_agent_scorer() -> None:
    """Orchestrator scores in-process with score_devshell_tasks_for_agent_loop."""
    repo = Path("/repo")
    run_dir = Path("/repo/results/run1")
    captured: dict[str, Any] = {}

    def fake_agent_loop(**kwargs: Any) -> int:
        captured.clear()
        captured.update(kwargs)
        return 0

    with patch(
        "evaluation.scripts.devshell.score_devshell_tasks.score_devshell_tasks_for_agent_loop",
        fake_agent_loop,
    ):
        run_score_devshell_tasks(
            repo_root=repo,
            run_dir=run_dir,
            eval_config=None,
            eval_ingest_timeout=60.0,
            score_jobs=2,
            parallel_checklist_workers=4,
            submit=False,
        )

    assert captured["run_dir"] == run_dir
    assert captured["submit"] is False
    assert captured["score_jobs"] == 2
    assert captured["parallel_checklist_workers"] == 4


def test_build_run_devshell_eval_argv_fallback_model() -> None:
    repo = Path("/repo")
    script = repo / "evaluation/scripts/devshell/run_devshell_eval.py"
    out = repo / "results/run1"
    params = RunDevshellEvalParams(
        output_dir=out,
        jobs=1,
        limit=None,
        questions=None,
        slices=None,
        model="bedrock-claude-opus",
        exp=None,
        eval_ingest_pending_only=True,
        no_export_review=False,
        task_timeout_sec=600.0,
        eval_config=None,
        extra_args=[],
        fallback_model="claude-opus-4-6",
    )
    invoker = DevshellEvalSubprocess(repo)
    argv = invoker.build_argv(script, params)
    i = argv.index("--fallback-model")
    assert argv[i + 1] == "claude-opus-4-6"
