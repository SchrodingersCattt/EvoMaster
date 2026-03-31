"""Tests for ListDirTool."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from matmaster.tools.builtin.listdir_tool import ListDirTool
from matmaster.tools.tool_registry import Tool


@pytest.fixture()
def mock_session() -> MagicMock:
    """Create a mock session with exec_bash returning directory listing."""
    session = MagicMock()
    session.exec_bash.return_value = {
        "output": "total 8\ndrwxr-xr-x 2 user user 4096 Jan 1 00:00 subdir\n-rw-r--r-- 1 user user 100 Jan 1 00:00 file.txt",
        "exit_code": 0,
        "working_dir": "/workspace",
    }
    return session


class TestListDirToolBasic:
    """Basic ListDirTool properties."""

    def test_name_is_list_dir(self) -> None:
        tool = ListDirTool()
        assert tool.name == "list_dir"

    def test_satisfies_tool_protocol(self) -> None:
        tool = ListDirTool()
        assert isinstance(tool, Tool)


class TestListDirToolExecution:
    """ListDirTool execution."""

    async def test_normal_path_returns_listing(self, mock_session: MagicMock) -> None:
        tool = ListDirTool(session=mock_session)
        result = await tool.execute({"path": "/some/dir"})
        assert "file.txt" in result
        mock_session.exec_bash.assert_called_once()

    async def test_error_exit_code_returns_error_message(
        self, mock_session: MagicMock
    ) -> None:
        mock_session.exec_bash.return_value = {
            "output": "ls: cannot access '/nonexistent': No such file or directory",
            "exit_code": 2,
            "working_dir": "/workspace",
        }
        tool = ListDirTool(session=mock_session)
        result = await tool.execute({"path": "/nonexistent"})
        assert "Error" in result

    async def test_default_path_is_dot(self, mock_session: MagicMock) -> None:
        tool = ListDirTool(session=mock_session)
        await tool.execute({})
        call_kwargs = mock_session.exec_bash.call_args
        command_sent = call_kwargs.kwargs.get(
            "command", call_kwargs[1].get("command", "")
        )
        assert '"."' in command_sent or "'.'" in command_sent

    async def test_session_not_injected_returns_error(self) -> None:
        tool = ListDirTool()
        result = await tool.execute({"path": "/some/dir"})
        assert "Error:" in result
