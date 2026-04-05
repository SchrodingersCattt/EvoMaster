"""tests/matmaster/tools/builtin/test_base.py"""

import asyncio

import pytest

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.topology import ToolPlane


class ConcreteTool(BuiltinTool):
    name = "TestTool"
    description = "A test tool"
    json_schema = {"type": "object", "properties": {}, "required": []}

    def _execute(self, arguments):
        return "ok"


class ErrorTool(BuiltinTool):
    name = "ErrorTool"
    description = "Raises"
    json_schema = {"type": "object", "properties": {}}

    def _execute(self, arguments):
        raise RuntimeError("boom")


class TestBuiltinToolProtocol:
    def test_name(self):
        tool = ConcreteTool()
        assert tool.name == "TestTool"

    def test_description(self):
        tool = ConcreteTool()
        assert tool.description == "A test tool"

    def test_describe_returns_description(self):
        tool = ConcreteTool()
        assert tool.describe() == "A test tool"

    def test_prompt_returns_none(self):
        tool = ConcreteTool()
        assert tool.prompt() is None

    def test_default_plane(self):
        tool = ConcreteTool()
        assert tool.plane == ToolPlane.CONTROL_PLANE

    def test_default_effect_level(self):
        tool = ConcreteTool()
        assert tool.effect_level == "local_mutation"

    def test_default_capabilities(self):
        tool = ConcreteTool()
        assert tool.capabilities == frozenset()

    def test_default_exposed_to_model(self):
        tool = ConcreteTool()
        assert tool.exposed_to_model is True


class TestBuiltinToolExecution:
    def test_execute_returns_result(self):
        tool = ConcreteTool()
        result = asyncio.run(tool.execute({}))
        assert result == "ok"

    def test_execute_catches_exception(self):
        tool = ErrorTool()
        result = asyncio.run(tool.execute({}))
        assert isinstance(result, str)
        assert "Error:" in result

    def test_execute_with_context_default(self):
        tool = ConcreteTool()
        result = asyncio.run(tool.execute_with_context({}, None))
        assert result == "ok"


class TestRequireSession:
    def test_no_session_raises(self):
        tool = ConcreteTool()
        with pytest.raises(RuntimeError, match="requires a session"):
            tool._require_session()

    def test_with_session(self):
        tool = ConcreteTool(session="fake")
        assert tool._require_session() == "fake"


class TestToolResultReturn:
    def test_execute_propagates_tool_result(self):
        class ToolResultTool(BuiltinTool):
            name = "ToolResultTool"
            description = "Returns ToolResult"
            json_schema = {"type": "object", "properties": {}}

            def _execute(self, arguments):
                return ToolResult(content="structured output")

        tool = ToolResultTool()
        result = asyncio.run(tool.execute({}))
        assert isinstance(result, ToolResult)
        assert result.content == "structured output"


class TestValidateInput:
    def test_default_returns_none(self):
        tool = ConcreteTool()
        result = asyncio.run(tool.validate_input({}, None))
        assert result is None
