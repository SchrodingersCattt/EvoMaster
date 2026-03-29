"""Tests for matmaster.sessions.local.LocalSession."""
from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.sessions.local import LocalSession


class TestLocalSessionExecBash:
    """exec_bash via subprocess."""

    def test_simple_command(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        result = session.exec_bash("echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert "hello" in result["output"]
        assert result["working_dir"] == str(tmp_path)

    def test_nonzero_exit(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        result = session.exec_bash("exit 42")
        assert result["exit_code"] == 42

    def test_stderr_captured(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        result = session.exec_bash("echo err >&2")
        assert "err" in result["stderr"]

    def test_timeout(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        result = session.exec_bash("sleep 10", timeout=1)
        assert result["exit_code"] != 0
        assert "timeout" in result["stderr"].lower() or "timeout" in result["output"].lower()

    def test_is_input_returns_error(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        result = session.exec_bash("echo hi", is_input=True)
        assert result["exit_code"] == 1
        assert "not supported" in result["stderr"].lower()

    def test_cwd_is_workspace(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        result = session.exec_bash("pwd")
        assert result["stdout"].strip() == str(tmp_path)


class TestLocalSessionFileOps:
    """File read/write/exists operations."""

    def test_write_and_read(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        session.write_file(str(tmp_path / "test.txt"), "hello content")
        content = session.read_file(str(tmp_path / "test.txt"))
        assert content == "hello content"

    def test_read_nonexistent_raises(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        with pytest.raises(FileNotFoundError):
            session.read_file(str(tmp_path / "missing.txt"))

    def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        target = str(tmp_path / "sub" / "dir" / "file.txt")
        session.write_file(target, "nested")
        assert session.read_file(target) == "nested"

    def test_path_exists(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        (tmp_path / "exists.txt").write_text("x")
        assert session.path_exists(str(tmp_path / "exists.txt")) is True
        assert session.path_exists(str(tmp_path / "nope.txt")) is False

    def test_is_file(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        (tmp_path / "file.txt").write_text("x")
        (tmp_path / "dir").mkdir()
        assert session.is_file(str(tmp_path / "file.txt")) is True
        assert session.is_file(str(tmp_path / "dir")) is False
        assert session.is_file(str(tmp_path / "missing")) is False


class TestLocalSessionLifecycle:
    """open/close are no-ops."""

    def test_open_close_no_error(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        session.open()
        session.close()

    def test_works_without_open(self, tmp_path: Path) -> None:
        session = LocalSession(workspace_path=tmp_path)
        result = session.exec_bash("echo works")
        assert result["exit_code"] == 0
