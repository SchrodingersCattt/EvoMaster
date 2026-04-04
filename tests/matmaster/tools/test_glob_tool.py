"""Tests for GlobTool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from matmaster.tools.builtin.glob_tool import GlobTool
from matmaster.tools.tool_registry import Tool
from matmaster.types.cancellation import CancellationController


@pytest.fixture()
def mock_session() -> MagicMock:
    """Create a mock session with exec_bash returning file list."""
    session = MagicMock()
    session.exec_bash.return_value = {
        "output": "/workspace/a.py\n/workspace/b.py\n",
        "exit_code": 0,
    }
    return session


class TestGlobToolBasic:
    """Basic GlobTool properties."""

    def test_name(self) -> None:
        tool = GlobTool()
        assert tool.name == "glob"

    def test_tool_protocol(self) -> None:
        tool = GlobTool()
        assert isinstance(tool, Tool)


class TestGlobToolExecution:
    """GlobTool execution with mock session."""

    async def test_find_files(self, mock_session: MagicMock) -> None:
        tool = GlobTool(session=mock_session, workdir=Path("/workspace"))
        result = await tool.execute({"pattern": "*.py"})
        assert "/workspace/a.py" in result
        assert "/workspace/b.py" in result
        mock_session.exec_bash.assert_called_once()

    async def test_no_matches(self, mock_session: MagicMock) -> None:
        mock_session.exec_bash.return_value = {"output": "", "exit_code": 0}
        tool = GlobTool(session=mock_session, workdir=Path("/workspace"))
        result = await tool.execute({"pattern": "*.xyz"})
        assert "No files matching pattern" in result

    async def test_default_path(self, mock_session: MagicMock) -> None:
        """No path arg -> searches workdir root."""
        tool = GlobTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "*.py"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert '"/workspace"' in command

    async def test_relative_path(self, mock_session: MagicMock) -> None:
        """path='src' resolves to workdir/src."""
        tool = GlobTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "*.py", "path": "src"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert '"/workspace/src"' in command

    async def test_find_command_has_head_truncation(
        self, mock_session: MagicMock
    ) -> None:
        """Verify output is truncated via head -200."""
        tool = GlobTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "*.py"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert "head -200" in command

    async def test_passes_cancel_token_from_session(self, mock_session: MagicMock) -> None:
        ctrl = CancellationController()
        mock_session._cancel_token = ctrl.token
        tool = GlobTool(session=mock_session, workdir=Path("/workspace"))

        await tool.execute({"pattern": "*.py"})

        call_kwargs = mock_session.exec_bash.call_args.kwargs
        assert call_kwargs["cancel_token"] is ctrl.token


class TestGlobToolPathSafety:
    """GlobTool workdir boundary enforcement."""

    async def test_path_safety_dotdot(self, mock_session: MagicMock) -> None:
        """path='../../etc' resolves to workdir (traversal blocked)."""
        tool = GlobTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "*.py", "path": "../../etc"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert '"/workspace"' in command
        assert "/etc" not in command

    async def test_path_safety_absolute(self, mock_session: MagicMock) -> None:
        """path='/etc' resolves to workdir (traversal blocked)."""
        tool = GlobTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "*.py", "path": "/etc"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert '"/workspace"' in command
        assert "/etc" not in command

    async def test_no_session_raises(self) -> None:
        """No session -> error returned."""
        tool = GlobTool()
        result = await tool.execute({"pattern": "*.py"})
        assert "Error:" in result
        assert "session" in result.lower()
