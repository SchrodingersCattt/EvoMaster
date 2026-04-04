"""Tests for BuiltinTool ABC base class."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_registry import Tool
from matmaster.types.cancellation import CancellationController
from matmaster.types.topology import ToolPlane


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

    async def test_execute_returns_result_on_success(self) -> None:
        tool = ConcreteBuiltinTool()
        result = await tool.execute({"arg1": "hello"})
        assert result == "executed with {'arg1': 'hello'}"

    async def test_execute_catches_exception_returns_error_string(self) -> None:
        tool = FailingBuiltinTool()
        result = await tool.execute({})
        assert result.startswith("Error:")
        assert "something went wrong" in result

    async def test_execute_error_string_contains_exception_message(self) -> None:
        tool = FailingBuiltinTool()
        result = await tool.execute({})
        assert "something went wrong" in result


def test_builtin_default_metadata() -> None:
    tool = ConcreteBuiltinTool()

    assert tool.resource_claims == ()
    assert tool.capabilities == frozenset()
    assert tool.effect_level == "local_mutation"
    assert tool.fast_path_eligible is False
    assert tool.max_result_chars == 0
    assert tool.plane == ToolPlane.CONTROL_PLANE
    assert tool.state_mode == "stateless"
    assert tool.stop_mode == "cancellable"
    assert tool.exposed_to_model is True


def test_builtin_describe_returns_description() -> None:
    tool = ConcreteBuiltinTool()

    assert tool.describe(None) == tool.description


def test_builtin_prompt_returns_none() -> None:
    tool = ConcreteBuiltinTool()

    assert tool.prompt() is None


@pytest.mark.asyncio
async def test_builtin_execute_with_context_delegates() -> None:
    tool = ConcreteBuiltinTool()

    result = await tool.execute_with_context({"x": 1}, None)

    assert "executed with" in str(result)


def test_cancel_token_for_exec_prefers_tool_attribute() -> None:
    session = type("SessionStub", (), {"_cancel_token": object()})()
    tool = ConcreteBuiltinTool(session=session)
    ctrl = CancellationController()
    tool._cancel_token = ctrl.token

    assert tool._cancel_token_for_exec() is ctrl.token


def test_cancel_token_for_exec_falls_back_to_session() -> None:
    ctrl = CancellationController()
    session = type("SessionStub", (), {"_cancel_token": ctrl.token})()
    tool = ConcreteBuiltinTool(session=session)

    assert tool._cancel_token_for_exec() is ctrl.token


@pytest.mark.asyncio
async def test_builtin_validate_input_accepts_runner_state() -> None:
    from matmaster.types.tool_runner_state import ToolRunnerState

    tool = ConcreteBuiltinTool()
    result = await tool.validate_input({"x": 1}, runner_state=ToolRunnerState())

    assert result is None
