"""tests/matmaster/tools/builtin/test_write_tool.py"""

import asyncio
from pathlib import PurePosixPath
from unittest.mock import MagicMock

from matmaster.tools.builtin.write_tool import WriteTool
from matmaster.types.tool_runner_state import ToolRunnerState


def make_session(path_exists=False):
    s = MagicMock()
    s.path_exists.return_value = path_exists
    s.write_file.return_value = None
    return s


class TestWriteToolMetadata:
    def test_name(self):
        assert WriteTool.name == "Write"


class TestWriteValidation:
    def test_empty_path(self):
        tool = WriteTool(session=make_session(), workdir=PurePosixPath("/workspace"))
        result = asyncio.run(
            tool.validate_input({"file_path": "", "content": "x"}, None)
        )
        assert result is not None
        assert result.decision == "deny"

    def test_outside_workspace(self):
        tool = WriteTool(session=make_session(), workdir=PurePosixPath("/workspace"))
        result = asyncio.run(
            tool.validate_input({"file_path": "/etc/passwd", "content": "x"}, None)
        )
        assert result is not None
        assert result.decision == "deny"

    def test_existing_file_without_read(self):
        tool = WriteTool(
            session=make_session(path_exists=True), workdir=PurePosixPath("/workspace")
        )
        state = ToolRunnerState()
        result = asyncio.run(
            tool.validate_input({"file_path": "/workspace/f.py", "content": "x"}, state)
        )
        assert result is not None
        assert result.decision == "deny"

    def test_existing_file_with_read(self):
        tool = WriteTool(
            session=make_session(path_exists=True), workdir=PurePosixPath("/workspace")
        )
        state = ToolRunnerState()
        state.set("read_files", {"/workspace/f.py"})
        result = asyncio.run(
            tool.validate_input({"file_path": "/workspace/f.py", "content": "x"}, state)
        )
        assert result is None

    def test_new_file_no_read_needed(self):
        tool = WriteTool(
            session=make_session(path_exists=False), workdir=PurePosixPath("/workspace")
        )
        state = ToolRunnerState()
        result = asyncio.run(
            tool.validate_input(
                {"file_path": "/workspace/new.py", "content": "x"}, state
            )
        )
        assert result is None


class TestWriteExecution:
    def test_write_succeeds(self):
        session = make_session()
        tool = WriteTool(session=session, workdir=PurePosixPath("/workspace"))
        result = asyncio.run(
            tool.execute({"file_path": "/workspace/f.py", "content": "hello"})
        )
        session.write_file.assert_called_once_with("/workspace/f.py", "hello")
        assert "successfully" in result.lower()
