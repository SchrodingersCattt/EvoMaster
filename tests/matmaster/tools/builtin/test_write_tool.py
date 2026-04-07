"""tests/matmaster/tools/builtin/test_write_tool.py"""

import asyncio
from pathlib import PurePosixPath
from unittest.mock import MagicMock

from matmaster.tools.builtin.read_tool import ReadTool
from matmaster.tools.builtin.write_tool import WriteTool
from matmaster.types.session import SessionFileStat
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext


def make_session(path_exists=False, raw: bytes = b"", mtime: float = 1.0):
    session = MagicMock()
    session.path_exists.return_value = path_exists
    session.download.return_value = raw
    session.stat_file.return_value = SessionFileStat(size=len(raw), mtime=mtime)
    session.write_file.return_value = None
    return session


class TestWriteToolMetadata:
    def test_name(self):
        assert WriteTool.name == "Write"

    def test_schema_allows_encoding(self):
        assert "encoding" in WriteTool.json_schema["properties"]


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
        session.write_file.assert_called_once_with("/workspace/f.py", "hello", "utf-8")
        assert "successfully" in result.lower()

    def test_write_uses_fresh_probe_when_snapshot_missing(self):
        raw = b"\xff\xfeh\x00i\x00\n\x00"
        session = make_session(path_exists=True, raw=raw)
        tool = WriteTool(session=session, workdir=PurePosixPath("/workspace"))
        state = ToolRunnerState()
        state.set("read_files", {"/workspace/f.txt"})
        ctx = ToolExecutionContext(runner_state=state)

        decision = asyncio.run(
            tool.validate_input(
                {"file_path": "/workspace/f.txt", "content": "hello"}, state
            )
        )
        assert decision is None

        result = asyncio.run(
            tool.execute_with_context(
                {"file_path": "/workspace/f.txt", "content": "hello"},
                ctx,
            )
        )

        session.write_file.assert_called_once_with(
            "/workspace/f.txt", "hello", "utf-16"
        )
        assert result.meta["encoding_source"] == "fresh_probe"

    def test_read_then_write_reuses_snapshot_encoding(self):
        raw = b"\xff\xfeh\x00i\x00\n\x00"
        session = MagicMock()
        session.is_file.return_value = True
        session.download.return_value = raw
        session.stat_file.return_value = SessionFileStat(size=len(raw), mtime=1.0)
        session.path_exists.return_value = True
        session.write_file = MagicMock()

        read_tool = ReadTool(session=session, workdir="/workspace")
        write_tool = WriteTool(session=session, workdir=PurePosixPath("/workspace"))
        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)

        asyncio.run(
            read_tool.execute_with_context({"file_path": "/workspace/f.txt"}, ctx)
        )
        asyncio.run(
            write_tool.execute_with_context(
                {"file_path": "/workspace/f.txt", "content": "ok"},
                ctx,
            )
        )

        session.write_file.assert_called_with("/workspace/f.txt", "ok", "utf-16")
