"""Tests for WriteTool -- file writing with Read-Before-Modify protocol."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from matmaster.tools.builtin.read_tracker import ReadTracker
from matmaster.tools.builtin.write_tool import WriteTool
from matmaster.tools.tool_registry import Tool


@pytest.fixture()
def mock_session() -> MagicMock:
    """Mock session with configurable path_exists."""
    session = MagicMock()
    session.path_exists.return_value = False
    return session


class TestWriteToolBasic:
    """WriteTool properties and protocol."""

    def test_name(self) -> None:
        tool = WriteTool()
        assert tool.name == "write_file"

    def test_tool_protocol(self) -> None:
        tool = WriteTool()
        assert isinstance(tool, Tool)


class TestWriteToolExecution:
    """WriteTool execution with Read-Before-Modify protocol."""

    async def test_write_new_file(self, mock_session: MagicMock) -> None:
        """New file (path_exists=False) writes without read check."""
        mock_session.path_exists.return_value = False
        tracker = ReadTracker()
        tool = WriteTool(session=mock_session, tracker=tracker)
        result = await tool.execute(
            {"file_path": "/workspace/new.py", "content": "hello"}
        )
        assert "File written successfully" in result
        mock_session.write_file.assert_called_once_with("/workspace/new.py", "hello")

    async def test_write_existing_after_read(self, mock_session: MagicMock) -> None:
        """Existing file after read -- writes successfully."""
        mock_session.path_exists.return_value = True
        tracker = ReadTracker()
        tracker.mark_read("/workspace/exist.py")
        tool = WriteTool(session=mock_session, tracker=tracker)
        result = await tool.execute(
            {"file_path": "/workspace/exist.py", "content": "updated"}
        )
        assert "File written successfully" in result
        mock_session.write_file.assert_called_once()

    async def test_write_existing_without_read(self, mock_session: MagicMock) -> None:
        """Existing file without prior read -- error."""
        mock_session.path_exists.return_value = True
        tracker = ReadTracker()
        tool = WriteTool(session=mock_session, tracker=tracker)
        result = await tool.execute(
            {"file_path": "/workspace/exist.py", "content": "bad"}
        )
        assert "must be read before modify" in result
        mock_session.write_file.assert_not_called()

    async def test_write_no_tracker(self, mock_session: MagicMock) -> None:
        """No tracker -- writes without enforcement."""
        mock_session.path_exists.return_value = True
        tool = WriteTool(session=mock_session, tracker=None)
        result = await tool.execute(
            {"file_path": "/workspace/exist.py", "content": "anything"}
        )
        assert "File written successfully" in result
        mock_session.write_file.assert_called_once()

    async def test_no_session_raises(self) -> None:
        tool = WriteTool()
        result = await tool.execute({"file_path": "/workspace/x.py", "content": "data"})
        assert "Error:" in result
        assert "session" in result.lower()

    async def test_error_message_exact_format(self, mock_session: MagicMock) -> None:
        """Verify exact D-03 error string format."""
        mock_session.path_exists.return_value = True
        tracker = ReadTracker()
        tool = WriteTool(session=mock_session, tracker=tracker)
        result = await tool.execute({"file_path": "/workspace/x.py", "content": "data"})
        assert result == "Error: file '/workspace/x.py' must be read before modify"

    async def test_write_success_message_format(self, mock_session: MagicMock) -> None:
        """Verify success message contains file path."""
        mock_session.path_exists.return_value = False
        tool = WriteTool(session=mock_session, tracker=ReadTracker())
        result = await tool.execute(
            {"file_path": "/workspace/new.txt", "content": "data"}
        )
        assert result == "File written successfully to: /workspace/new.txt"
