"""Shared test fixtures for matmaster.kernel tests.

Provides mock objects and builders for kernel test suites.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from matmaster.kernel.types import LLMResponse, StreamChunk, ToolCallData


@pytest.fixture
def mock_tool_call() -> ToolCallData:
    """Default ToolCallData fixture."""
    return ToolCallData(id="tc-1", name="test_tool", arguments={"key": "value"})


def make_tool_call(
    name: str = "test_tool",
    args: dict[str, Any] | None = None,
    call_id: str = "tc-1",
) -> ToolCallData:
    """Factory function for creating ToolCallData instances."""
    return ToolCallData(
        id=call_id,
        name=name,
        arguments=args if args is not None else {},
    )


class MockLLMProvider:
    """Mock LLM provider satisfying the LLMProvider Protocol.

    chat() returns LLMResponse with no tool_calls.
    chat_stream() yields a single StreamChunk with content="hello".
    """

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="mock response", finish_reason="stop")

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")


def build_mock_spec(
    *,
    llm_provider: Any | None = None,
    guards: list[Any] | None = None,
    hooks: list[Any] | None = None,
    max_turns: int = 10,
    system_prompt: str = "You are a test agent",
) -> dict[str, Any]:
    """Build a dict with AgentRuntimeSpec-like fields for testing.

    Returns a plain dict (not AgentRuntimeSpec) to avoid circular imports.
    Plan 02 will use actual AgentRuntimeSpec once kernel is complete.
    """
    return {
        "llm_provider": llm_provider or MockLLMProvider(),
        "guards": guards or [],
        "hooks": hooks or [],
        "max_turns": max_turns,
        "system_prompt": system_prompt,
    }
