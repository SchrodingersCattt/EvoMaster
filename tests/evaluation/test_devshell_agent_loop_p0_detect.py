"""Tests for P0 regression directory detection in ``DevshellAgentLoop``."""

from __future__ import annotations

import io
from pathlib import Path

from evaluation.devshell_agent.config_state import (
    AgentLoopSharedState,
    DevshellAgentCliDefaults,
)
from evaluation.devshell_agent.loop import DevshellAgentLoop


def _minimal_state(eval_output_dirs: list[Path]) -> AgentLoopSharedState:
    defaults = DevshellAgentCliDefaults(
        jobs=1,
        limit=None,
        questions=None,
        slices=None,
        model=None,
        exp=None,
        eval_ingest_pending_only=False,
        no_export_review=False,
        task_timeout_sec=60.0,
        eval_config=None,
        extra_args=[],
    )
    return AgentLoopSharedState(
        repo_root=Path("/tmp"),
        session_dir=Path("/tmp"),
        outcomes=[],
        defaults=defaults,
        eval_output_dirs=eval_output_dirs,
    )


def test_p0_detect_two_phase_success_no_false_positive(tmp_path: Path) -> None:
    """Sibling ``p0_gate`` + ``remaining`` must not look like regression."""
    base = tmp_path / "iter_01"
    p0 = base / "p0_gate"
    rest = base / "remaining"
    p0.mkdir(parents=True)
    rest.mkdir()

    log = io.StringIO()
    state = _minimal_state([p0, rest])
    assert DevshellAgentLoop._detect_p0_regression_from_eval_dirs(state, log) is False
    assert "P0 gate directory found without remaining" not in log.getvalue()


def test_p0_detect_regression_only_p0_gate(tmp_path: Path) -> None:
    """P0 failed: only ``p0_gate`` exists under tag — should flag regression."""
    base = tmp_path / "iter_01"
    p0 = base / "p0_gate"
    p0.mkdir(parents=True)

    log = io.StringIO()
    state = _minimal_state([p0])
    assert DevshellAgentLoop._detect_p0_regression_from_eval_dirs(state, log) is True
    assert "P0 gate directory found without remaining" in log.getvalue()


def test_p0_detect_single_phase_no_p0_subdirs(tmp_path: Path) -> None:
    """Single-phase output dir has no ``p0_gate`` child — no regression signal."""
    out = tmp_path / "iter_01"
    out.mkdir()

    log = io.StringIO()
    state = _minimal_state([out])
    assert DevshellAgentLoop._detect_p0_regression_from_eval_dirs(state, log) is False


def test_p0_detect_order_independent(tmp_path: Path) -> None:
    """Either sibling order in ``eval_output_dirs`` must not false-positive."""
    base = tmp_path / "iter_01"
    p0 = base / "p0_gate"
    rest = base / "remaining"
    p0.mkdir(parents=True)
    rest.mkdir()
    for paths in ([p0, rest], [rest, p0]):
        log = io.StringIO()
        state = _minimal_state(paths)
        assert (
            DevshellAgentLoop._detect_p0_regression_from_eval_dirs(state, log) is False
        )
