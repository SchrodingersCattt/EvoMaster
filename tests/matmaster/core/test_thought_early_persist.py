"""Focused tests for early persistence of complete reasoning snapshots."""

from __future__ import annotations

from typing import Any

import pytest

from matmaster.core.agent_llm_stream import stream_llm_items
from matmaster.types.events import ResponseEvent, RunResultEvent, ThoughtEvent
from matmaster.types.messages import LLMResponse, StreamChunk

from .agent_kernel_test_helpers import (
    ProviderProtocolAttrs,
    StreamingProvider,
    make_kernel_runtime,
)
from .agent_kernel_test_helpers import make_kernel_turn as turn


class ReasoningThenContentProvider(ProviderProtocolAttrs):
    """Provider that streams reasoning chunks then content chunks."""

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
        yield StreamChunk(reasoning_content="thinking part 1")
        yield StreamChunk(reasoning_content=" part 2")
        yield StreamChunk(content="visible part 1")
        yield StreamChunk(content=" part 2")
        yield StreamChunk(
            finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 5}
        )


@pytest.mark.asyncio
async def test_reasoning_to_toolcall_emits_complete_before_final() -> None:
    """Reasoning-to-tool-call transition emits complete before final response."""
    from matmaster.core.kernel_items import _KernelItem

    provider = StreamingProvider(
        [
            StreamChunk(reasoning_content="plan the call"),
            StreamChunk(
                tool_call_deltas=[
                    {"index": 0, "id": "c1", "name": "bash", "arguments": "{}"}
                ]
            ),
            StreamChunk(finish_reason="stop", usage={"prompt_tokens": 8}),
        ]
    )
    kernel_runtime = make_kernel_runtime(provider=provider)

    items: list[_KernelItem] = []
    async for item in stream_llm_items(
        kernel_runtime.resources, [{"role": "user", "content": "test"}], None
    ):
        items.append(item)

    complete_i = next(
        i
        for i, item in enumerate(items)
        if item.event
        and isinstance(item.event, ThoughtEvent)
        and item.event.stream_state == "complete"
    )
    final_i = next(i for i, item in enumerate(items) if item.llm_response is not None)
    assert complete_i < final_i
    assert items[complete_i].event.content == "plan the call"


@pytest.mark.asyncio
async def test_interleaved_reasoning_emits_single_complete() -> None:
    """Reasoning-content-reasoning streams emit only one complete thought."""
    from matmaster.core.kernel_items import _KernelItem

    provider = StreamingProvider(
        [
            StreamChunk(reasoning_content="first reasoning"),
            StreamChunk(content="visible answer"),
            StreamChunk(reasoning_content="second reasoning"),
            StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5}),
        ]
    )
    kernel_runtime = make_kernel_runtime(provider=provider)

    items: list[_KernelItem] = []
    async for item in stream_llm_items(
        kernel_runtime.resources, [{"role": "user", "content": "test"}], None
    ):
        items.append(item)

    thought_completes = [
        item
        for item in items
        if item.event
        and isinstance(item.event, ThoughtEvent)
        and item.event.stream_state == "complete"
    ]
    assert len(thought_completes) == 1
    assert thought_completes[0].event.content == "first reasoning"


@pytest.mark.asyncio
async def test_finally_does_not_emit_complete_on_midstream_error() -> None:
    """Midstream errors must not persist partial reasoning as complete."""
    from matmaster.core.kernel_items import _KernelItem
    from matmaster.types.errors import LLMError

    class ReasoningThenErrorProvider(ProviderProtocolAttrs):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def chat(self, messages, tools=None):
            return LLMResponse(content="not used", finish_reason="stop")

        async def chat_stream(self, messages, tools=None, *, timeout=None):
            yield StreamChunk(reasoning_content="half reasoning")
            raise LLMError("mid-stream failure", retryable=True)

    provider = ReasoningThenErrorProvider()
    kernel_runtime = make_kernel_runtime(provider=provider)

    items: list[_KernelItem] = []
    with pytest.raises(LLMError):
        async for item in stream_llm_items(
            kernel_runtime.resources, [{"role": "user", "content": "test"}], None
        ):
            items.append(item)

    thought_completes = [
        item
        for item in items
        if item.event
        and isinstance(item.event, ThoughtEvent)
        and item.event.stream_state == "complete"
    ]
    assert thought_completes == []


@pytest.mark.asyncio
async def test_thought_complete_emitted_during_content_stream_before_response() -> None:
    """Complete thought is emitted during content streaming, before response complete."""
    from matmaster.core.agent import AgentKernel

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=ReasoningThenContentProvider()),
        turn("test task"),
    ):
        events.append(event)

    thought_complete_i = next(
        i
        for i, event in enumerate(events)
        if isinstance(event, ThoughtEvent) and event.stream_state == "complete"
    )
    response_complete_i = next(
        i
        for i, event in enumerate(events)
        if isinstance(event, ResponseEvent) and event.stream_state == "complete"
    )
    last_response_streaming_i = max(
        i
        for i, event in enumerate(events)
        if isinstance(event, ResponseEvent) and event.stream_state == "streaming"
    )

    assert thought_complete_i < response_complete_i
    assert thought_complete_i < last_response_streaming_i


@pytest.mark.asyncio
async def test_cancel_during_reasoning_does_not_persist_complete() -> None:
    """Cancelled runs must not persist complete thoughts."""
    from matmaster.core.agent import AgentKernel
    from matmaster.types.cancellation import CancellationController

    kernel_runtime = make_kernel_runtime(provider=ReasoningThenContentProvider())
    ctrl = CancellationController()
    ctrl.cancel()

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        kernel_runtime, turn("test task"), cancel_token=ctrl.token
    ):
        events.append(event)

    assert isinstance(events[-1], RunResultEvent)
    assert events[-1].status == "cancelled"
    thought_completes = [
        event
        for event in events
        if isinstance(event, ThoughtEvent) and event.stream_state == "complete"
    ]
    assert thought_completes == []


@pytest.mark.asyncio
async def test_llm_error_retry_persists_first_attempt_reasoning() -> None:
    """Current retry behavior keeps the first pre-content reasoning snapshot."""
    from matmaster.core.agent import AgentKernel
    from matmaster.types.errors import LLMError

    class ContentMidStreamErrorProvider(ProviderProtocolAttrs):
        stream_timeout = 10.0
        max_retries = 2
        retry_delay = 0.0

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
                yield StreamChunk(reasoning_content="first attempt reasoning")
                yield StreamChunk(content="partial ")
                raise LLMError("connection dropped mid-content", retryable=True)

            yield StreamChunk(reasoning_content="second attempt reasoning")
            yield StreamChunk(content="final answer")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 7})

    provider = ContentMidStreamErrorProvider()
    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=provider), turn("test task")
    ):
        events.append(event)

    assert provider.call_count == 2
    completes = [
        event
        for event in events
        if isinstance(event, ThoughtEvent) and event.stream_state == "complete"
    ]
    assert [event.content for event in completes] == ["first attempt reasoning"]
