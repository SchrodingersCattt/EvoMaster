"""Tests for ReadTool -- file reading with line numbers and overlimit protection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from matmaster.tools.builtin.read_tool import (
    MAX_READ_CHARS,
    MAX_READ_LINES,
    PREVIEW_LINES,
    ReadTool,
)
from matmaster.tools.tool_registry import Tool
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_session() -> MagicMock:
    """Mock session with is_file=True and 5-line content."""
    session = MagicMock()
    session.is_file.return_value = True
    session.read_file.return_value = "line1\nline2\nline3\nline4\nline5"
    return session


def _make_content(n_lines: int) -> str:
    """Generate n lines of content: 'line1\\nline2\\n...lineN'."""
    return "\n".join(f"line{i}" for i in range(1, n_lines + 1))


# ---------------------------------------------------------------------------
# Basic protocol
# ---------------------------------------------------------------------------


class TestReadToolBasic:
    """ReadTool properties and protocol."""

    def test_name(self) -> None:
        tool = ReadTool()
        assert tool.name == "read_file"

    def test_tool_protocol(self) -> None:
        tool = ReadTool()
        assert isinstance(tool, Tool)

    def test_description_has_routing_declaration(self) -> None:
        desc = ReadTool.description
        assert "ALWAYS" in desc
        assert "NEVER" in desc

    def test_schema_has_offset_and_limit(self) -> None:
        props = ReadTool.json_schema["properties"]
        assert "offset" in props
        assert "limit" in props
        assert "line_range" not in props


# ---------------------------------------------------------------------------
# Full-read mode (no offset/limit)
# ---------------------------------------------------------------------------


class TestFullRead:
    """Full-read mode: no offset/limit provided."""

    async def test_read_full_within_limit(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = await tool.execute({"file_path": "/workspace/a.py"})
        assert "cat -n" in result
        assert "     1\tline1" in result
        assert "     5\tline5" in result

    async def test_read_exceeds_limit_returns_error_and_preview(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        session.read_file.return_value = _make_content(MAX_READ_LINES + 500)
        tool = ReadTool(session=session)
        result = await tool.execute({"file_path": "/workspace/big.py"})

        assert "Error:" in result
        assert str(MAX_READ_LINES + 500) in result
        # Preview contains first PREVIEW_LINES lines
        assert "     1\tline1" in result
        assert f"    {PREVIEW_LINES}\tline{PREVIEW_LINES}" in result
        # Does NOT contain lines beyond preview
        assert f"line{PREVIEW_LINES + 1}" not in result

    async def test_runner_state_marked_on_within_limit(
        self, mock_session: MagicMock
    ) -> None:
        tool = ReadTool(session=mock_session)
        state = ToolRunnerState()
        exec_ctx = ToolExecutionContext(runner_state=state)
        await tool.execute_with_context({"file_path": "/workspace/a.py"}, exec_ctx)
        assert "/workspace/a.py" in state.get("read_files", set())

    async def test_runner_state_not_marked_on_overlimit(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        session.read_file.return_value = _make_content(MAX_READ_LINES + 100)
        tool = ReadTool(session=session)
        state = ToolRunnerState()
        exec_ctx = ToolExecutionContext(runner_state=state)
        await tool.execute_with_context({"file_path": "/workspace/big.py"}, exec_ctx)
        assert "/workspace/big.py" not in state.get("read_files", set())

    async def test_read_empty_file(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        session.read_file.return_value = ""
        tool = ReadTool(session=session)
        result = await tool.execute({"file_path": "/workspace/empty.py"})
        assert "Error" not in result
        assert "cat -n" in result

    async def test_file_with_trailing_newline(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        # 5 lines with trailing newline — splitlines() should give 5, not 6
        session.read_file.return_value = "a\nb\nc\nd\ne\n"
        tool = ReadTool(session=session)
        result = await tool.execute({"file_path": "/workspace/f.py"})
        assert "     5\te" in result
        # No 6th line
        assert "     6\t" not in result


# ---------------------------------------------------------------------------
# Ranged-read mode (offset and/or limit)
# ---------------------------------------------------------------------------


class TestRangedRead:
    """Ranged-read mode: offset and/or limit provided."""

    async def test_read_with_offset_and_limit(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = await tool.execute(
            {"file_path": "/workspace/a.py", "offset": 2, "limit": 2}
        )
        assert "     2\tline2" in result
        assert "     3\tline3" in result
        assert "line1" not in result
        assert "line4" not in result

    async def test_read_with_offset_only(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = await tool.execute({"file_path": "/workspace/a.py", "offset": 3})
        assert "     3\tline3" in result
        assert "     5\tline5" in result
        assert "line1" not in result
        assert "line2" not in result

    async def test_read_with_only_limit(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = await tool.execute({"file_path": "/workspace/a.py", "limit": 3})
        assert "     1\tline1" in result
        assert "     3\tline3" in result
        assert "line4" not in result

    async def test_read_with_limit_exceeds_max(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        session.read_file.return_value = _make_content(MAX_READ_LINES + 500)
        tool = ReadTool(session=session)
        result = await tool.execute(
            {
                "file_path": "/workspace/big.py",
                "offset": 1,
                "limit": MAX_READ_LINES + 500,
            }
        )
        # Should return content (not error) but with truncation notice
        assert "cat -n" in result
        assert "     1\tline1" in result
        assert f"  {MAX_READ_LINES}\tline{MAX_READ_LINES}" in result
        assert "[Note:" in result
        assert "capped" in result.lower() or "showing" in result.lower()

    async def test_offset_only_truncated_with_notice(self) -> None:
        total = MAX_READ_LINES + 1000
        session = MagicMock()
        session.is_file.return_value = True
        session.read_file.return_value = _make_content(total)
        tool = ReadTool(session=session)
        result = await tool.execute({"file_path": "/workspace/big.py", "offset": 1})
        assert "cat -n" in result
        assert "[Note:" in result

    async def test_runner_state_marked_on_ranged_read(
        self, mock_session: MagicMock
    ) -> None:
        tool = ReadTool(session=mock_session)
        state = ToolRunnerState()
        exec_ctx = ToolExecutionContext(runner_state=state)
        await tool.execute_with_context(
            {"file_path": "/workspace/a.py", "offset": 1, "limit": 3},
            exec_ctx,
        )
        assert "/workspace/a.py" in state.get("read_files", set())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    """Parameter validation."""

    async def test_offset_out_of_range(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        state = ToolRunnerState()
        exec_ctx = ToolExecutionContext(runner_state=state)
        result = await tool.execute_with_context(
            {"file_path": "/workspace/a.py", "offset": 100},
            exec_ctx,
        )
        content = result.content if hasattr(result, "content") else result
        assert "Error" in content
        assert "/workspace/a.py" not in state.get("read_files", set())

    async def test_offset_zero_rejected(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = await tool.execute({"file_path": "/workspace/a.py", "offset": 0})
        assert "Error" in result

    async def test_limit_negative_rejected(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session)
        result = await tool.execute({"file_path": "/workspace/a.py", "limit": -1})
        assert "Error" in result

    async def test_file_not_found(self, mock_session: MagicMock) -> None:
        mock_session.is_file.return_value = False
        tool = ReadTool(session=mock_session)
        result = await tool.execute({"file_path": "/nonexist"})
        assert "Error" in result
        assert "is not a file" in result

    async def test_no_session_raises(self) -> None:
        tool = ReadTool()
        result = await tool.execute({"file_path": "/workspace/a.py"})
        assert "Error" in result
        assert "session" in result.lower()

    async def test_no_tracker(self, mock_session: MagicMock) -> None:
        tool = ReadTool(session=mock_session, tracker=None)
        result = await tool.execute({"file_path": "/workspace/a.py"})
        assert "cat -n" in result


# ---------------------------------------------------------------------------
# Character limit fallback
# ---------------------------------------------------------------------------


class TestCharLimit:
    """MAX_READ_CHARS fallback guard."""

    async def test_char_limit_truncation(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        # 10 lines but each line is huge (exceeds MAX_READ_CHARS total)
        huge_line = "x" * (MAX_READ_CHARS // 5)
        session.read_file.return_value = "\n".join([huge_line] * 10)
        tool = ReadTool(session=session)
        result = await tool.execute({"file_path": "/workspace/huge.json"})
        # Output should be truncated and contain notice
        assert "[Output truncated" in result
        assert len(result) <= MAX_READ_CHARS + 500  # some margin for the notice text

    async def test_char_truncated_ranged_read_not_marked(self) -> None:
        session = MagicMock()
        session.is_file.return_value = True
        huge_line = "x" * (MAX_READ_CHARS // 5)
        session.read_file.return_value = "\n".join([huge_line] * 10)
        tool = ReadTool(session=session)
        state = ToolRunnerState()
        exec_ctx = ToolExecutionContext(runner_state=state)
        result = await tool.execute_with_context(
            {"file_path": "/workspace/huge.json", "offset": 1, "limit": 10},
            exec_ctx,
        )
        content = result.content if hasattr(result, "content") else result
        assert "[Output truncated" in content
        assert "/workspace/huge.json" not in state.get("read_files", set())
