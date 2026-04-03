"""Root conftest -- async mock factories for Protocol testing.

Provides MockAsyncLLMProvider, MockAsyncTool, MockAsyncHook that satisfy
the async Protocol definitions. Used across all test suites for Phase 12+.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from matmaster.core.hooks import HookAction
from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import LLMResponse, Message, StreamChunk, ToolCallData
from matmaster.types.topology import ToolPlane


class MockAsyncLLMProvider:
    """Async mock satisfying LLMProvider Protocol for testing."""

    def __init__(
        self,
        *,
        chat_response: LLMResponse | None = None,
        stream_chunks: list[StreamChunk] | None = None,
    ) -> None:
        self._chat_response = chat_response or LLMResponse(
            content="mock response", finish_reason="stop"
        )
        self._stream_chunks = stream_chunks or [
            StreamChunk(content="hello", finish_reason="stop")
        ]

    async def __aenter__(self) -> MockAsyncLLMProvider:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return self._chat_response

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        for chunk in self._stream_chunks:
            yield chunk


class MockAsyncTool:
    """Async mock satisfying Tool Protocol for testing."""

    def __init__(
        self,
        name: str = "test_tool",
        result: str = "ok",
        description: str = "A test tool",
    ) -> None:
        self._name = name
        self._result = result
        self._description = description
        self.resource_claims = ()
        self.capabilities = frozenset()
        self.effect_level = "local_mutation"
        self.fast_path_eligible = False
        self.max_result_chars = 0
        self.plane = ToolPlane.CONTROL_PLANE
        self.state_mode = "stateless"
        self.stop_mode = "cancellable"
        self.exposed_to_model = True

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def describe(self, ctx: Any) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> str | None:
        return None

    async def execute(self, arguments: dict[str, Any]) -> str:
        return self._result


class MockAsyncHook:
    """Async mock satisfying Hook Protocol for testing."""

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        pass

    async def pre_llm_call(self, messages: list[Message], turn: int) -> None:
        pass

    async def should_continue(self, messages: list[Message], turn: int) -> bool:
        return True

    async def on_stream_chunk(self, chunk: StreamChunk) -> None:
        pass

    async def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None:
        pass


# -- Fixtures --


@pytest.fixture
def async_llm_provider() -> MockAsyncLLMProvider:
    return MockAsyncLLMProvider()


@pytest.fixture
def async_tool() -> MockAsyncTool:
    return MockAsyncTool()


@pytest.fixture
def async_hook() -> MockAsyncHook:
    return MockAsyncHook()
