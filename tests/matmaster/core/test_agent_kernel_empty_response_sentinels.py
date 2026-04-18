"""Tests for AgentKernel handling of empty-value assistant sentinels."""

from __future__ import annotations

from typing import Any

import pytest

from matmaster.types.events import AssistantStateEvent, ResponseEvent, RunResultEvent
from matmaster.types.messages import LLMResponse, StreamChunk

from .agent_kernel_test_helpers import _make_spec, _make_tool_registry


class EmptyStopProvider:
    """Provider that ends cleanly with configurable assistant content."""

    stream_timeout = 10.0
    max_retries = 1
    retry_delay = 0.0

    def __init__(self, content: str | None = None):
        self.content = content
        self.call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self.call_count += 1
        if self.content is not None:
            yield StreamChunk(content=self.content)
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10})


class SentinelToolPreambleProvider:
    """Provider that emits an empty-value sentinel before switching to tools."""

    def __init__(self) -> None:
        self.call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self.call_count += 1
        if self.call_count == 1:
            yield StreamChunk(content="none")
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-none",
                        "name": "test_tool",
                        "arguments": '{"cmd": "pwd"}',
                    }
                ],
                finish_reason="tool_calls",
            )
        else:
            yield StreamChunk(content="done")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10})


@pytest.mark.asyncio
@pytest.mark.parametrize("sentinel", ["none", "None", " null "])
async def test_empty_sentinel_stop_finishes_as_invalid_finish(
    sentinel: str,
) -> None:
    from matmaster.core.agent import AgentKernel

    provider = EmptyStopProvider(content=sentinel)
    spec = _make_spec(provider=provider)
    kernel = AgentKernel()

    events: list[Any] = []
    async for event in kernel.run_stream(spec, "test task"):
        events.append(event)

    response_texts = [
        event.content
        for event in events
        if isinstance(event, ResponseEvent) and event.content
    ]

    assert isinstance(events[-1], RunResultEvent)
    assert events[-1].status == "failed"
    assert events[-1].reason == "invalid_finish"
    assert events[-1].final_content is None
    assert sentinel.strip() not in response_texts


@pytest.mark.asyncio
async def test_text_containing_none_still_finishes_naturally() -> None:
    from matmaster.core.agent import AgentKernel

    content = "None 是 Python 的空值对象"
    provider = EmptyStopProvider(content=content)
    spec = _make_spec(provider=provider)
    kernel = AgentKernel()

    events: list[Any] = []
    async for event in kernel.run_stream(spec, "test task"):
        events.append(event)

    assert isinstance(events[-1], RunResultEvent)
    assert events[-1].status == "completed"
    assert events[-1].reason == "natural"
    assert events[-1].final_content == content


@pytest.mark.asyncio
async def test_empty_sentinel_tool_call_preamble_does_not_block_tools() -> None:
    from matmaster.core.agent import AgentKernel

    provider = SentinelToolPreambleProvider()
    registry, tools = _make_tool_registry(tool_names=["test_tool"])
    spec = _make_spec(provider=provider, tool_registry=registry)
    kernel = AgentKernel()

    events: list[Any] = []
    async for event in kernel.run_stream(spec, "test task"):
        events.append(event)

    assistant_state_events = [
        event for event in events if isinstance(event, AssistantStateEvent)
    ]
    response_texts = [
        event.content
        for event in events
        if isinstance(event, ResponseEvent) and event.content
    ]

    assert tools[0].calls == [("test_tool", {"cmd": "pwd"})]
    assert len(assistant_state_events) >= 1
    state = assistant_state_events[0].state
    assert state.get("tool_calls") is not None
    assert state.get("content") is None
    assert "none" not in response_texts
    assert isinstance(events[-1], RunResultEvent)
    assert events[-1].status == "completed"
    assert events[-1].reason == "natural"
