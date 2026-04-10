"""Tests for evaluation.devshell_agent.git_iteration helpers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from evaluation.devshell_agent.git_iteration import (
    append_iteration_head,
    run_git_revert_commits_after_base,
)


def _git(repo: Path, *args: str) -> None:
    p = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    assert p.returncode == 0


def test_run_git_revert_commits_after_base_linear(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@e.st")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "a")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-m", "b")

    ok, msg, shas = run_git_revert_commits_after_base(repo_root=repo, base_sha=base)
    assert ok is True
    assert "1 commit" in msg or "reverted 1" in msg.lower()
    assert len(shas) == 1
    assert not (repo / "b.txt").exists()


def test_append_iteration_head_appends_jsonl_rows(tmp_path: Path) -> None:
    session = tmp_path / "sess"
    append_iteration_head(session_dir=session, iteration=1, head="aaa")
    append_iteration_head(session_dir=session, iteration=2, head="bbb")
    path = session / "git_iteration_heads.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"iteration": 1, "head_at_start": "aaa"}
    assert json.loads(lines[1]) == {"iteration": 2, "head_at_start": "bbb"}
