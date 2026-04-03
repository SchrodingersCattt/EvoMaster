"""Tests for EditTool -- str_replace editing with Read-Before-Modify protocol."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.tools.tool_registry import Tool
from matmaster.types.tool_runner_state import ToolRunnerState


@pytest.fixture()
def mock_session() -> MagicMock:
    """Mock session with read_file returning a 3-line file."""
    session = MagicMock()
    session.read_file.return_value = "aaa\nbbb\nccc"
    return session


class TestEditToolBasic:
    """EditTool properties and protocol."""

    def test_name(self) -> None:
        tool = EditTool()
        assert tool.name == "edit_file"

    def test_tool_protocol(self) -> None:
        tool = EditTool()
        assert isinstance(tool, Tool)


class TestEditToolExecution:
    """EditTool str_replace behavior."""

    async def test_str_replace_unique(self, mock_session: MagicMock) -> None:
        """Single match replaces and returns snippet with new text."""
        tool = EditTool(session=mock_session)
        result = await tool.execute(
            {
                "file_path": "/workspace/test.py",
                "old_str": "bbb",
                "new_str": "xxx",
            }
        )
        assert "has been edited" in result
        assert "xxx" in result
        mock_session.write_file.assert_called_once()
        # Verify the written content
        written = mock_session.write_file.call_args[0][1]
        assert written == "aaa\nxxx\nccc"

    async def test_str_replace_no_match(self, mock_session: MagicMock) -> None:
        """No match returns error about old_str not found."""
        tool = EditTool(session=mock_session)
        result = await tool.execute(
            {
                "file_path": "/workspace/test.py",
                "old_str": "zzz",
                "new_str": "yyy",
            }
        )
        assert "did not appear verbatim" in result
        mock_session.write_file.assert_not_called()

    async def test_str_replace_multi_match(self, mock_session: MagicMock) -> None:
        """Multiple matches returns error with line numbers."""
        mock_session.read_file.return_value = "hello world\nfoo bar\nhello world\n"
        tool = EditTool(session=mock_session)
        result = await tool.execute(
            {
                "file_path": "/workspace/test.py",
                "old_str": "hello world",
                "new_str": "goodbye",
            }
        )
        assert "Multiple occurrences" in result
        # Line numbers should be present
        assert "1" in result
        assert "3" in result
        mock_session.write_file.assert_not_called()

    async def test_str_replace_same_strings(self, mock_session: MagicMock) -> None:
        """old_str == new_str returns error about no-op."""
        tool = EditTool(session=mock_session)
        result = await tool.execute(
            {
                "file_path": "/workspace/test.py",
                "old_str": "bbb",
                "new_str": "bbb",
            }
        )
        assert "must be different" in result
        mock_session.write_file.assert_not_called()

    async def test_str_replace_strip_fallback(self, mock_session: MagicMock) -> None:
        """old_str with extra whitespace falls back to stripped version."""
        tool = EditTool(session=mock_session)
        result = await tool.execute(
            {
                "file_path": "/workspace/test.py",
                "old_str": "  bbb  ",
                "new_str": "xxx",
            }
        )
        assert "has been edited" in result
        written = mock_session.write_file.call_args[0][1]
        assert "xxx" in written

    async def test_execute_does_not_enforce_read_before_modify(
        self, mock_session: MagicMock
    ) -> None:
        """Execution stays pure; read-before-modify is enforced in validate_input."""
        tool = EditTool(session=mock_session)
        result = await tool.execute(
            {
                "file_path": "/workspace/test.py",
                "old_str": "bbb",
                "new_str": "xxx",
            }
        )
        assert "has been edited" in result
        mock_session.write_file.assert_called_once()

    async def test_execute_without_runner_state_argument(self, mock_session: MagicMock) -> None:
        """Execution path stays independent from runner_state enforcement."""
        tool = EditTool(session=mock_session)
        result = await tool.execute(
            {
                "file_path": "/workspace/test.py",
                "old_str": "bbb",
                "new_str": "xxx",
            }
        )
        assert "has been edited" in result
        mock_session.write_file.assert_called_once()

    async def test_no_session_raises(self) -> None:
        tool = EditTool()
        result = await tool.execute(
            {
                "file_path": "/workspace/test.py",
                "old_str": "bbb",
                "new_str": "xxx",
            }
        )
        assert "Error:" in result
        assert "session" in result.lower()

    async def test_snippet_includes_context_lines(self, mock_session: MagicMock) -> None:
        """Snippet output includes surrounding context lines."""
        mock_session.read_file.return_value = (
            "line1\nline2\nline3\nline4\ntarget\nline6\nline7\nline8\nline9\nline10"
        )
        tool = EditTool(session=mock_session)
        result = await tool.execute(
            {
                "file_path": "/workspace/test.py",
                "old_str": "target",
                "new_str": "replaced",
            }
        )
        assert "replaced" in result
        # Snippet should include context lines around the replacement
        assert "cat -n" in result


class TestEditToolValidateInput:
    async def test_edit_blocks_unread_file_via_runner_state(
        self, mock_session: MagicMock
    ) -> None:
        tool = EditTool(session=mock_session, workdir="/workspace")
        state = ToolRunnerState()

        decision = await tool.validate_input(
            {"file_path": "/workspace/test.py", "old_str": "bbb", "new_str": "xxx"},
            runner_state=state,
        )

        assert decision is not None
        assert decision.decision == "deny"
        assert "must be read" in decision.reason.lower()

    async def test_edit_allows_read_file_via_runner_state(
        self, mock_session: MagicMock
    ) -> None:
        tool = EditTool(session=mock_session, workdir="/workspace")
        state = ToolRunnerState()
        state.set("read_files", {"/workspace/test.py"})

        decision = await tool.validate_input(
            {"file_path": "/workspace/test.py", "old_str": "bbb", "new_str": "xxx"},
            runner_state=state,
        )

        assert decision is None
