"""Tests for GrepTool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from matmaster.tools.builtin.grep_tool import GrepTool
from matmaster.tools.tool_registry import Tool


@pytest.fixture()
def mock_session() -> MagicMock:
    """Create a mock session with exec_bash returning grep output."""
    session = MagicMock()
    session.exec_bash.return_value = {
        "output": "/workspace/a.py:1:import os\n/workspace/b.py:3:import os\n",
        "exit_code": 0,
    }
    return session


class TestGrepToolBasic:
    """Basic GrepTool properties."""

    def test_name(self) -> None:
        tool = GrepTool()
        assert tool.name == "grep"

    def test_tool_protocol(self) -> None:
        tool = GrepTool()
        assert isinstance(tool, Tool)


class TestGrepToolExecution:
    """GrepTool execution with mock session."""

    async def test_grep_matches(self, mock_session: MagicMock) -> None:
        tool = GrepTool(session=mock_session, workdir=Path("/workspace"))
        result = await tool.execute({"pattern": "import os"})
        assert "/workspace/a.py:1:import os" in result
        assert "/workspace/b.py:3:import os" in result
        mock_session.exec_bash.assert_called_once()

    async def test_no_matches(self, mock_session: MagicMock) -> None:
        mock_session.exec_bash.return_value = {"output": "", "exit_code": 1}
        tool = GrepTool(session=mock_session, workdir=Path("/workspace"))
        result = await tool.execute({"pattern": "nonexistent_pattern"})
        assert "No matches for pattern" in result

    async def test_include_filter(self, mock_session: MagicMock) -> None:
        """include='*.py' -> command contains --include='*.py'."""
        tool = GrepTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "import os", "include": "*.py"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert '--include="*.py"' in command

    async def test_default_path(self, mock_session: MagicMock) -> None:
        """No path arg -> searches workdir root."""
        tool = GrepTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "import os"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert '"/workspace"' in command

    async def test_relative_path(self, mock_session: MagicMock) -> None:
        """path='src' resolves to workdir/src."""
        tool = GrepTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "import os", "path": "src"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert '"/workspace/src"' in command

    async def test_grep_command_has_head_truncation(
        self, mock_session: MagicMock
    ) -> None:
        """Verify output is truncated via head -200."""
        tool = GrepTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "import os"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert "head -200" in command

    async def test_grep_uses_rn_flags(self, mock_session: MagicMock) -> None:
        """Verify grep uses -rn for recursive search with line numbers."""
        tool = GrepTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "import os"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert "grep -rn" in command


class TestGrepToolPathSafety:
    """GrepTool workdir boundary enforcement."""

    async def test_path_safety_dotdot(self, mock_session: MagicMock) -> None:
        """path='../../etc' resolves to workdir (traversal blocked)."""
        tool = GrepTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "import os", "path": "../../etc"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert '"/workspace"' in command
        assert "/etc" not in command

    async def test_path_safety_absolute(self, mock_session: MagicMock) -> None:
        """path='/etc' resolves to workdir (traversal blocked)."""
        tool = GrepTool(session=mock_session, workdir=Path("/workspace"))
        await tool.execute({"pattern": "import os", "path": "/etc"})
        call_kwargs = mock_session.exec_bash.call_args
        command = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert '"/workspace"' in command
        assert "/etc" not in command

    async def test_no_session_raises(self) -> None:
        """No session -> error returned."""
        tool = GrepTool()
        result = await tool.execute({"pattern": "import os"})
        assert "Error:" in result
        assert "session" in result.lower()
