"""Tests for evaluation.devshell_agent.path_policy."""

from __future__ import annotations

from pathlib import Path

from evaluation.devshell_agent.path_policy import (
    is_blocked_matmaster_exps_path,
    is_path_committable_for_optimization,
    is_under,
)


def test_is_under(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    assert is_under(sub, root) is True
    assert is_under(root, root) is True
    assert is_under(tmp_path, root) is False


def test_committable_excludes_evaluation_and_results(tmp_path: Path) -> None:
    rr = tmp_path
    (rr / "matmaster" / "exps").mkdir(parents=True)
    (rr / "evaluation" / "question_bank").mkdir(parents=True)
    (rr / "results" / "sess").mkdir(parents=True)

    assert (
        is_path_committable_for_optimization(rr, "matmaster/exps/direct.toml") is False
    )
    assert (
        is_path_committable_for_optimization(rr, "matmaster/exps/_base.toml") is False
    )
    assert (
        is_path_committable_for_optimization(rr, "matmaster/exps/explore.toml") is False
    )
    assert (
        is_path_committable_for_optimization(rr, "evaluation/question_bank/x.yaml")
        is False
    )
    assert is_path_committable_for_optimization(rr, "results/sess/out.txt") is False


def test_blocked_matmaster_exps_path(tmp_path: Path) -> None:
    rr = tmp_path
    (rr / "matmaster" / "exps").mkdir(parents=True)
    direct = rr / "matmaster" / "exps" / "direct.toml"
    direct.touch()
    assert is_blocked_matmaster_exps_path(rr, direct) is True
    explore = rr / "matmaster" / "exps" / "explore.toml"
    explore.touch()
    assert is_blocked_matmaster_exps_path(rr, explore) is True
    assert (
        is_blocked_matmaster_exps_path(rr, rr / "matmaster" / "skills" / "x.md")
        is False
    )
