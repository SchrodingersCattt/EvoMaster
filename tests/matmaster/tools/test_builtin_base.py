"""Tests for BuiltinTool ABC base class.

Post Plan-01: BuiltinTool._execute() is async def abstractmethod.
Concrete subclasses implement async def _execute().
BuiltinTool.execute() is still sync def (calls _execute without await).
Phase 14 will unify execute() to async def + await _execute().
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_registry import Tool


class ConcreteBuiltinTool(BuiltinTool):
    """Concrete subclass with async _execute for testing the ABC."""

    name: ClassVar[str] = "test_concrete"
    description: ClassVar[str] = "A concrete test tool"
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"arg1": {"type": "string"}},
    }

    async def _execute(self, arguments: dict[str, Any]) -> str:
        return f"executed with {arguments}"


class FailingBuiltinTool(BuiltinTool):
    """Concrete subclass that raises in async _execute."""

    name: ClassVar[str] = "test_failing"
    description: ClassVar[str] = "A failing test tool"
    json_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    async def _execute(self, arguments: dict[str, Any]) -> str:
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


class TestAsyncExecute:
    """Test _execute directly with await (Phase 12 async ABC)."""

    async def test_execute_returns_result_on_success(self) -> None:
        tool = ConcreteBuiltinTool()
        result = await tool._execute({"arg1": "hello"})
        assert result == "executed with {'arg1': 'hello'}"

    async def test_execute_raises_on_failure(self) -> None:
        tool = FailingBuiltinTool()
        with pytest.raises(ValueError, match="something went wrong"):
            await tool._execute({})


class TestExecuteTemplateMethod:
    """execute() template method -- still sync in Phase 12.

    Since _execute is now async def but execute() calls it without await,
    execute() returns a coroutine object (not the string result).
    This is a known temporary inconsistency; Phase 14 will make
    execute() async and add await _execute().
    """

    def test_execute_sync_returns_coroutine_not_string(self) -> None:
        """Sync execute() calling async _execute() without await returns coroutine.

        This documents the transitional behavior in Phase 12.
        Phase 14 will fix this by making execute() async.
        """
        import asyncio

        tool = ConcreteBuiltinTool()
        result = tool.execute({"arg1": "hello"})
        # Since _execute is async, calling it without await returns a coroutine
        assert asyncio.iscoroutine(result)
        # Clean up the unawaited coroutine
        result.close()
