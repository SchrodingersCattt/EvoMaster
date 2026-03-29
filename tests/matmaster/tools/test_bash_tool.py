"""Tests for BashTool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from evomaster.agent.session.local import LocalSession
from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.tool_registry import Tool


@pytest.fixture()
def mock_session() -> MagicMock:
    """Create a mock session with exec_bash returning success."""
    session = MagicMock()
    session.exec_bash.return_value = {
        "output": "hello world",
        "exit_code": 0,
        "working_dir": "/tmp",
    }
    return session


class TestBashToolBasic:
    """Basic BashTool properties."""

    def test_name_is_execute_bash(self) -> None:
        tool = BashTool()
        assert tool.name == "execute_bash"

    def test_satisfies_tool_protocol(self) -> None:
        tool = BashTool()
        assert isinstance(tool, Tool)


class TestBashToolExecution:
    """BashTool command execution."""

    async def test_normal_command_returns_output_and_exit_code(
        self, mock_session: MagicMock
    ) -> None:
        tool = BashTool(session=mock_session)
        result = await tool.execute({"command": "echo hello"})
        assert "hello world" in result
        assert "exit code 0" in result
        mock_session.exec_bash.assert_called_once()

    async def test_dangerous_command_blocked(self, mock_session: MagicMock) -> None:
        tool = BashTool(session=mock_session)
        result = await tool.execute({"command": "env"})
        assert "Blocked:" in result
        mock_session.exec_bash.assert_not_called()

    async def test_is_input_true_no_proxy_prefix(self, mock_session: MagicMock) -> None:
        tool = BashTool(session=mock_session)
        result = await tool.execute({"command": "some input", "is_input": "true"})
        # The command sent to session should NOT have proxy clear prefix
        call_kwargs = mock_session.exec_bash.call_args
        command_sent = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert "http_proxy" not in command_sent

    async def test_session_not_injected_returns_error(self) -> None:
        tool = BashTool()
        result = await tool.execute({"command": "echo hello"})
        assert "Error:" in result
        assert "session" in result.lower()

    async def test_working_dir_in_output(self, mock_session: MagicMock) -> None:
        tool = BashTool(session=mock_session)
        result = await tool.execute({"command": "pwd"})
        assert "/tmp" in result


class TestBashToolAsyncSubprocess:
    """BashTool async subprocess path for matmaster LocalSession."""

    def _make_tool_with_local_session(self) -> BashTool:
        """Create BashTool with a real evomaster LocalSession instance."""
        session = LocalSession()
        return BashTool(session=session, workdir=Path("/tmp"))

    async def test_normal_command(self) -> None:
        """Async path: create_subprocess_exec called, output contains result."""
        tool = self._make_tool_with_local_session()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello world", b""))
        mock_proc.returncode = 0

        with patch(
            "matmaster.tools.builtin.bash_tool.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_create:
            result = await tool.execute({"command": "echo hello"})

        assert "hello world" in result
        assert "exit code 0" in result
        mock_create.assert_called_once()

    async def test_timeout(self) -> None:
        """Async path: timeout kills process and returns exit code 124."""
        tool = self._make_tool_with_local_session()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch(
            "matmaster.tools.builtin.bash_tool.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            result = await tool.execute({"command": "sleep 999", "timeout": 1})

        assert "timeout" in result.lower()
        assert "exit code 124" in result
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_awaited_once()

    async def test_dangerous_blocked(self) -> None:
        """Async path: dangerous commands blocked before subprocess creation."""
        tool = self._make_tool_with_local_session()

        with patch(
            "matmaster.tools.builtin.bash_tool.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_create:
            result = await tool.execute({"command": "env"})

        assert "Blocked:" in result
        mock_create.assert_not_called()

    async def test_is_input(self) -> None:
        """Async path: is_input returns not-supported message."""
        tool = self._make_tool_with_local_session()

        with patch(
            "matmaster.tools.builtin.bash_tool.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_create:
            result = await tool.execute({"command": "x", "is_input": "true"})

        assert "Interactive input is not supported" in result
        mock_create.assert_not_called()

    async def test_session_dependent_fallback(self) -> None:
        """Non-LocalSession: falls back to session.exec_bash via base class to_thread."""
        mock_session = MagicMock()
        mock_session.exec_bash.return_value = {
            "output": "fallback result",
            "exit_code": 0,
            "working_dir": "/tmp",
        }
        tool = BashTool(session=mock_session, workdir=Path("/tmp"))

        result = await tool.execute({"command": "echo test"})

        assert "fallback result" in result
        mock_session.exec_bash.assert_called_once()
