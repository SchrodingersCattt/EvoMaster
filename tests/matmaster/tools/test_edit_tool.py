"""Tests for EditTool -- str_replace editing with Read-Before-Modify protocol."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.tools.builtin.read_tracker import ReadTracker
from matmaster.tools.tool_registry import Tool


@pytest.fixture()
def mock_session() -> MagicMock:
    """Mock session with read_file returning a 3-line file."""
    session = MagicMock()
    session.read_file.return_value = "aaa\nbbb\nccc"
    return session


@pytest.fixture()
def tracker_marked() -> ReadTracker:
    """ReadTracker pre-marked for /workspace/test.py."""
    tracker = ReadTracker()
    tracker.mark_read("/workspace/test.py")
    return tracker


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

    def test_str_replace_unique(
        self, mock_session: MagicMock, tracker_marked: ReadTracker
    ) -> None:
        """Single match replaces and returns snippet with new text."""
        tool = EditTool(session=mock_session, tracker=tracker_marked)
        result = tool.execute({
            "file_path": "/workspace/test.py",
            "old_str": "bbb",
            "new_str": "xxx",
        })
        assert "has been edited" in result
        assert "xxx" in result
        mock_session.write_file.assert_called_once()
        # Verify the written content
        written = mock_session.write_file.call_args[0][1]
        assert written == "aaa\nxxx\nccc"

    def test_str_replace_no_match(
        self, mock_session: MagicMock, tracker_marked: ReadTracker
    ) -> None:
        """No match returns error about old_str not found."""
        tool = EditTool(session=mock_session, tracker=tracker_marked)
        result = tool.execute({
            "file_path": "/workspace/test.py",
            "old_str": "zzz",
            "new_str": "yyy",
        })
        assert "did not appear verbatim" in result
        mock_session.write_file.assert_not_called()

    def test_str_replace_multi_match(
        self, mock_session: MagicMock, tracker_marked: ReadTracker
    ) -> None:
        """Multiple matches returns error with line numbers."""
        mock_session.read_file.return_value = "hello world\nfoo bar\nhello world\n"
        tool = EditTool(session=mock_session, tracker=tracker_marked)
        result = tool.execute({
            "file_path": "/workspace/test.py",
            "old_str": "hello world",
            "new_str": "goodbye",
        })
        assert "Multiple occurrences" in result
        # Line numbers should be present
        assert "1" in result
        assert "3" in result
        mock_session.write_file.assert_not_called()

    def test_str_replace_same_strings(
        self, mock_session: MagicMock, tracker_marked: ReadTracker
    ) -> None:
        """old_str == new_str returns error about no-op."""
        tool = EditTool(session=mock_session, tracker=tracker_marked)
        result = tool.execute({
            "file_path": "/workspace/test.py",
            "old_str": "bbb",
            "new_str": "bbb",
        })
        assert "must be different" in result
        mock_session.write_file.assert_not_called()

    def test_str_replace_strip_fallback(
        self, mock_session: MagicMock, tracker_marked: ReadTracker
    ) -> None:
        """old_str with extra whitespace falls back to stripped version."""
        tool = EditTool(session=mock_session, tracker=tracker_marked)
        result = tool.execute({
            "file_path": "/workspace/test.py",
            "old_str": "  bbb  ",
            "new_str": "xxx",
        })
        assert "has been edited" in result
        written = mock_session.write_file.call_args[0][1]
        assert "xxx" in written

    def test_read_before_modify(self, mock_session: MagicMock) -> None:
        """Without prior read -- returns Read-Before-Modify error."""
        tracker = ReadTracker()  # Not pre-marked
        tool = EditTool(session=mock_session, tracker=tracker)
        result = tool.execute({
            "file_path": "/workspace/test.py",
            "old_str": "bbb",
            "new_str": "xxx",
        })
        assert "must be read before modify" in result
        mock_session.write_file.assert_not_called()

    def test_no_tracker(self, mock_session: MagicMock) -> None:
        """No tracker -- edits without Read-Before-Modify check."""
        tool = EditTool(session=mock_session, tracker=None)
        result = tool.execute({
            "file_path": "/workspace/test.py",
            "old_str": "bbb",
            "new_str": "xxx",
        })
        assert "has been edited" in result
        mock_session.write_file.assert_called_once()

    def test_no_session_raises(self) -> None:
        tool = EditTool()
        result = tool.execute({
            "file_path": "/workspace/test.py",
            "old_str": "bbb",
            "new_str": "xxx",
        })
        assert "Error:" in result
        assert "session" in result.lower()

    def test_error_message_exact_format(self, mock_session: MagicMock) -> None:
        """Verify exact D-03 error string for Read-Before-Modify violation."""
        tracker = ReadTracker()
        tool = EditTool(session=mock_session, tracker=tracker)
        result = tool.execute({
            "file_path": "/workspace/test.py",
            "old_str": "bbb",
            "new_str": "xxx",
        })
        assert result == "Error: file '/workspace/test.py' must be read before modify"

    def test_snippet_includes_context_lines(
        self, mock_session: MagicMock, tracker_marked: ReadTracker
    ) -> None:
        """Snippet output includes surrounding context lines."""
        mock_session.read_file.return_value = (
            "line1\nline2\nline3\nline4\ntarget\nline6\nline7\nline8\nline9\nline10"
        )
        tool = EditTool(session=mock_session, tracker=tracker_marked)
        result = tool.execute({
            "file_path": "/workspace/test.py",
            "old_str": "target",
            "new_str": "replaced",
        })
        assert "replaced" in result
        # Snippet should include context lines around the replacement
        assert "cat -n" in result
