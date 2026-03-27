"""Tests for BuiltinTool ABC base class.

BuiltinTool.execute() is async def + asyncio.to_thread.
_execute() is sync def -- subclasses implement sync _execute() only.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_registry import Tool


class ConcreteBuiltinTool(BuiltinTool):
    """Concrete subclass with sync _execute for testing the ABC."""

    name: ClassVar[str] = "test_concrete"
    description: ClassVar[str] = "A concrete test tool"
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"arg1": {"type": "string"}},
    }

    def _execute(self, arguments: dict[str, Any]) -> str:
        return f"executed with {arguments}"


class FailingBuiltinTool(BuiltinTool):
    """Concrete subclass that raises in sync _execute."""

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


class TestDirectExecute:
    """Test _execute() directly (sync def)."""

    def test_execute_returns_result_on_success(self) -> None:
        tool = ConcreteBuiltinTool()
        result = tool._execute({"arg1": "hello"})
        assert result == "executed with {'arg1': 'hello'}"

    def test_execute_raises_on_failure(self) -> None:
        tool = FailingBuiltinTool()
        with pytest.raises(ValueError, match="something went wrong"):
            tool._execute({})


class TestExecuteTemplateMethod:
    """execute() template method -- async def with asyncio.to_thread."""

    async def test_execute_async_returns_string_result(self) -> None:
        """Async execute() delegates to sync _execute() via to_thread."""
        tool = ConcreteBuiltinTool()
        result = await tool.execute({"arg1": "hello"})
        assert result == "executed with {'arg1': 'hello'}"

    async def test_execute_async_catches_exception(self) -> None:
        """execute() catches _execute() exceptions and returns error string."""
        tool = FailingBuiltinTool()
        result = await tool.execute({})
        assert isinstance(result, str)
        assert "something went wrong" in result
