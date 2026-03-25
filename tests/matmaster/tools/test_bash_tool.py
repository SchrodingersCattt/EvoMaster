"""Tests for BashTool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

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

    def test_normal_command_returns_output_and_exit_code(
        self, mock_session: MagicMock
    ) -> None:
        tool = BashTool(session=mock_session)
        result = tool.execute({"command": "echo hello"})
        assert "hello world" in result
        assert "exit code 0" in result
        mock_session.exec_bash.assert_called_once()

    def test_dangerous_command_blocked(self, mock_session: MagicMock) -> None:
        tool = BashTool(session=mock_session)
        result = tool.execute({"command": "env"})
        assert "Blocked:" in result
        mock_session.exec_bash.assert_not_called()

    def test_is_input_true_no_proxy_prefix(self, mock_session: MagicMock) -> None:
        tool = BashTool(session=mock_session)
        result = tool.execute({"command": "some input", "is_input": "true"})
        # The command sent to session should NOT have proxy clear prefix
        call_kwargs = mock_session.exec_bash.call_args
        command_sent = call_kwargs.kwargs.get("command", call_kwargs[1].get("command", ""))
        assert "http_proxy" not in command_sent

    def test_session_not_injected_returns_error(self) -> None:
        tool = BashTool()
        result = tool.execute({"command": "echo hello"})
        assert "Error:" in result
        assert "session" in result.lower()

    def test_working_dir_in_output(self, mock_session: MagicMock) -> None:
        tool = BashTool(session=mock_session)
        result = tool.execute({"command": "pwd"})
        assert "/tmp" in result
