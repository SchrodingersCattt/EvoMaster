"""tests/matmaster/tools/builtin/test_read_tool.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext


def make_session(content="line1\nline2\nline3", is_file=True):
    s = MagicMock()
    s.is_file.return_value = is_file
    s.read_file.return_value = content
    return s


class TestReadToolMetadata:
    def test_name(self):
        assert ReadTool.name == "Read"

    def test_effect_level(self):
        assert ReadTool.effect_level == "none"

    def test_fast_path(self):
        assert ReadTool.fast_path_eligible is True


class TestReadToolExecution:
    def test_file_not_found(self):
        tool = ReadTool(session=make_session(is_file=False))
        result = asyncio.run(tool.execute({"file_path": "/workspace/nope"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    def test_full_read_small_file(self):
        content = "\n".join(f"line {i}" for i in range(10))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({"file_path": "/workspace/f.py"}))
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert "1\t" in result.content  # cat -n format

    def test_full_read_marks_read(self):
        content = "hello"
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({"file_path": "/workspace/f.py"}))
        assert isinstance(result, ToolResult)
        assert result.meta.get("mark_read") is True

    def test_overlimit_returns_error(self):
        content = "\n".join(f"line {i}" for i in range(2500))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(tool.execute({"file_path": "/workspace/big.py"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "offset" in result.content.lower()

    def test_ranged_read(self):
        content = "\n".join(f"line {i}" for i in range(100))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(
            tool.execute(
                {
                    "file_path": "/workspace/f.py",
                    "offset": 10,
                    "limit": 5,
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert result.meta.get("mark_read") is True

    def test_ranged_read_marks_read(self):
        content = "\n".join(f"line {i}" for i in range(100))
        tool = ReadTool(session=make_session(content=content))
        result = asyncio.run(
            tool.execute(
                {
                    "file_path": "/workspace/f.py",
                    "offset": 0,
                    "limit": 5,
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert result.meta.get("mark_read") is True


class TestReadToolRunnerState:
    def test_execute_with_context_updates_runner_state(self):
        content = "hello"
        tool = ReadTool(session=make_session(content=content))
        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)
        asyncio.run(tool.execute_with_context({"file_path": "/workspace/f.py"}, ctx))
        assert "/workspace/f.py" in state.get("read_files", set())
