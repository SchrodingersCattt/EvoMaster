"""Tests for BuiltinTool ABC base class."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_registry import Tool


class ConcreteBuiltinTool(BuiltinTool):
    """Concrete subclass for testing the ABC."""

    name: ClassVar[str] = "test_concrete"
    description: ClassVar[str] = "A concrete test tool"
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"arg1": {"type": "string"}},
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        return f"executed with {arguments}"


class FailingBuiltinTool(BuiltinTool):
    """Concrete subclass that raises in _execute."""

    name: ClassVar[str] = "test_failing"
    description: ClassVar[str] = "A failing test tool"
    json_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def _execute(self, arguments: dict[str, Any]) -> str:
        raise ValueError("something went wrong")


class TestBuiltinToolProtocol:
    """BuiltinTool subclass must satisfy the Tool Protocol."""

    def test_isinstance_tool_protocol(self) -> None:
        tool = ConcreteBuiltinTool()
        assert isinstance(tool, Tool)

    def test_isinstance_tool_protocol_with_session(self) -> None:
        tool = ConcreteBuiltinTool(session=object())
        assert isinstance(tool, Tool)


class TestRequireSession:
    """_require_session guard method."""

    def test_raises_when_session_is_none(self) -> None:
        tool = ConcreteBuiltinTool()
        with pytest.raises(RuntimeError, match="test_concrete requires a session"):
            tool._require_session()

    def test_returns_session_when_present(self) -> None:
        sentinel = object()
        tool = ConcreteBuiltinTool(session=sentinel)
        assert tool._require_session() is sentinel


class TestExecuteTemplateMethod:
    """execute() template method delegates to _execute() and handles errors."""

    def test_execute_returns_result_on_success(self) -> None:
        tool = ConcreteBuiltinTool()
        result = tool.execute({"arg1": "hello"})
        assert result == "executed with {'arg1': 'hello'}"

    def test_execute_catches_exception_returns_error_string(self) -> None:
        tool = FailingBuiltinTool()
        result = tool.execute({})
        assert result.startswith("Error:")
        assert "something went wrong" in result

    def test_execute_error_string_contains_exception_message(self) -> None:
        tool = FailingBuiltinTool()
        result = tool.execute({})
        assert "something went wrong" in result
