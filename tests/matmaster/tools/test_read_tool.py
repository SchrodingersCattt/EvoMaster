"""Tests for ReadTool -- file reading with line numbers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.builtin.read_tracker import ReadTracker
from matmaster.tools.tool_registry import Tool


@pytest.fixture()
def mock_session() -> MagicMock:
    """Mock session with is_file=True and 5-line content."""
    session = MagicMock()
    session.is_file.return_value = True
    session.read_file.return_value = "line1\nline2\nline3\nline4\nline5"
    return session


class TestReadToolBasic:
    """ReadTool properties and protocol."""

    def test_name(self) -> None:
        tool = ReadTool()
        assert tool.name == "read_file"

    def test_tool_protocol(self) -> None:
        tool = ReadTool()
        assert isinstance(tool, Tool)


class TestReadToolExecution:
    """ReadTool execution behavior."""

    def test_read_file_full(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/workspace/a.py"})
        assert "cat -n" in result
        # Verify line number format: 6-char width + tab
        assert "     1\tline1" in result
        assert "     5\tline5" in result

    def test_read_file_line_range(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/workspace/a.py", "line_range": [2, 3]})
        assert "     2\tline2" in result
        assert "     3\tline3" in result
        assert "line1" not in result
        assert "line4" not in result

    def test_read_file_line_range_open_end(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/workspace/a.py", "line_range": [2, -1]})
        assert "     2\tline2" in result
        assert "     5\tline5" in result
        assert "line1" not in result

    def test_file_not_found(self, mock_session: MagicMock) -> None:
        mock_session.is_file.return_value = False
        tool = ReadTool(session=mock_session)
        result = tool.execute({"file_path": "/nonexist"})
        assert "Error:" in result
        assert "/nonexist" in result
        assert "is not a file" in result

    def test_tracker_mark(self, mock_session: MagicMock) -> None:
        tracker = ReadTracker()
        tool = ReadTool(session=mock_session, tracker=tracker)
        tool.execute({"file_path": "/workspace/a.py"})
        assert tracker.has_been_read("/workspace/a.py") is True

    def test_no_tracker(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session, tracker=None)
        result = tool.execute({"file_path": "/workspace/a.py"})
        # Works without tracker
        assert "cat -n" in result

    def test_no_session_raises(self) -> None:
        tool = ReadTool()
        result = tool.execute({"file_path": "/workspace/a.py"})
        assert "Error:" in result
        assert "session" in result.lower()
