"""Tests for DevShell agent loop subprocess argv building (no claude-agent-sdk)."""

from __future__ import annotations

from pathlib import Path

from evaluation.devshell_agent.subprocess_runner import (
    DevshellEvalSubprocess,
    RunDevshellEvalParams,
)


def test_build_run_devshell_eval_argv_minimal() -> None:
    repo = Path("/repo")
    script = repo / "evaluation/scripts/devshell/run_devshell_eval.py"
    out = repo / "results/run1"
    params = RunDevshellEvalParams(
        output_dir=out,
        modes=["direct"],
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
    assert "--modes" in argv and "direct" in argv
    assert "--jobs" in argv and "2" in argv
    assert "--limit" in argv and "3" in argv
    assert "--model" in argv and "claude-sonnet-4-6" in argv
    assert "--eval-ingest-pending-only" in argv
    assert "--output-dir" in argv
    assert str(out) in argv
    assert "--no-clean-results" in argv
    assert "--eval-config" in argv
    assert "--eval-ingest-run-id" not in argv


def test_build_run_devshell_eval_argv_eval_ingest_run_id() -> None:
    repo = Path("/repo")
    script = repo / "evaluation/scripts/devshell/run_devshell_eval.py"
    out = repo / "results/run1"
    params = RunDevshellEvalParams(
        output_dir=out,
        modes=["direct"],
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
