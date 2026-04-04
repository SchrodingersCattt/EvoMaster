"""tests/matmaster/tools/builtin/test_edit_tool.py"""

import asyncio
from unittest.mock import MagicMock

from matmaster.tools.builtin.edit_tool import EditTool
from matmaster.types.tool_runner_state import ToolRunnerState


def make_session(content="hello world"):
    s = MagicMock()
    s.read_file.return_value = content
    s.write_file.return_value = None
    return s


class TestEditToolMetadata:
    def test_name(self):
        assert EditTool.name == "Edit"

    def test_schema_has_replace_all(self):
        assert "replace_all" in EditTool.json_schema["properties"]


class TestEditValidation:
    def test_empty_old_string(self):
        tool = EditTool(session=make_session())
        result = asyncio.run(
            tool.validate_input(
                {"file_path": "/f", "old_string": "", "new_string": "x"}, None
            )
        )
        assert result is not None
        assert result.decision == "deny"

    def test_same_strings(self):
        tool = EditTool(session=make_session())
        result = asyncio.run(
            tool.validate_input(
                {"file_path": "/f", "old_string": "x", "new_string": "x"}, None
            )
        )
        assert result is not None
        assert result.decision == "deny"

    def test_read_before_modify(self):
        tool = EditTool(session=make_session())
        state = ToolRunnerState()
        result = asyncio.run(
            tool.validate_input(
                {"file_path": "/workspace/f.py", "old_string": "a", "new_string": "b"},
                state,
            )
        )
        assert result is not None
        assert result.decision == "deny"
        assert "read" in result.reason.lower()

    def test_read_before_modify_passes(self):
        tool = EditTool(session=make_session())
        state = ToolRunnerState()
        state.set("read_files", {"/workspace/f.py"})
        result = asyncio.run(
            tool.validate_input(
                {"file_path": "/workspace/f.py", "old_string": "a", "new_string": "b"},
                state,
            )
        )
        assert result is None


class TestEditExecution:
    def test_single_match_replace(self):
        tool = EditTool(session=make_session("hello world"))
        result = asyncio.run(
            tool.execute(
                {
                    "file_path": "/f",
                    "old_string": "hello",
                    "new_string": "goodbye",
                }
            )
        )
        assert isinstance(result, str)
        assert "edited" in result.lower() or "goodbye" in result

    def test_no_match_error(self):
        tool = EditTool(session=make_session("hello world"))
        result = asyncio.run(
            tool.execute(
                {
                    "file_path": "/f",
                    "old_string": "notfound",
                    "new_string": "x",
                }
            )
        )
        assert "not" in result.lower() or "error" in result.lower()

    def test_multiple_matches_error(self):
        tool = EditTool(session=make_session("aaa bbb aaa"))
        result = asyncio.run(
            tool.execute(
                {
                    "file_path": "/f",
                    "old_string": "aaa",
                    "new_string": "x",
                }
            )
        )
        assert "multiple" in result.lower() or "unique" in result.lower()

    def test_replace_all(self):
        session = make_session("aaa bbb aaa")
        tool = EditTool(session=session)
        asyncio.run(
            tool.execute(
                {
                    "file_path": "/f",
                    "old_string": "aaa",
                    "new_string": "x",
                    "replace_all": True,
                }
            )
        )
        session.write_file.assert_called_once()
        written = session.write_file.call_args[0][1]
        assert written == "x bbb x"
