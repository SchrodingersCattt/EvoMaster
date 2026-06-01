"""Token usage event behavior for AgentKernel."""

from __future__ import annotations

from typing import Any

import pytest

from matmaster.types.events import ResponseEvent, RunResultEvent
from matmaster.types.run_metadata import RunIdentity

from .agent_kernel_test_helpers import make_kernel_runtime, make_kernel_turn
from .test_agent_kernel_stream import (
    ContentOnlyProvider,
    EmptyThenContentProvider,
    ReasoningThenContentProvider,
)


@pytest.mark.asyncio
async def test_response_segment_end_at_stream_end() -> None:
    from matmaster.core.agent_llm_stream import stream_llm_items
    from matmaster.core.kernel_items import _KernelItem

    provider = ReasoningThenContentProvider()
    kernel_runtime = make_kernel_runtime(provider=provider)
    items: list[_KernelItem] = []
    async for item in stream_llm_items(
        kernel_runtime.resources, [{"role": "user", "content": "test"}], None
    ):
        items.append(item)

    completes = [
        i
        for i in items
        if i.event
        and isinstance(i.event, ResponseEvent)
        and i.event.stream_state == "complete"
    ]
    segment_ends = [
        i
        for i in items
        if i.event
        and isinstance(i.event, ResponseEvent)
        and i.event.stream_state == "segment_end"
    ]
    assert completes == []
    assert "visible part 1" in segment_ends[0].event.content


@pytest.mark.asyncio
async def test_run_stream_emits_usage_bearing_response_complete() -> None:
    from matmaster.core.agent import AgentKernel

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=ContentOnlyProvider()),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    completes = [
        e
        for e in events
        if isinstance(e, ResponseEvent) and e.stream_state == "complete"
    ]
    assert len(completes) == 1
    assert completes[0].content == "hello world"
    assert completes[0].turn_index == 0
    assert completes[0].turn_usage == {"prompt_tokens": 5}
    assert completes[0].total_usage == {"prompt_tokens": 5}


@pytest.mark.asyncio
async def test_retry_discarded_attempt_does_not_emit_usage_response_complete() -> None:
    from matmaster.core.agent import AgentKernel

    provider = EmptyThenContentProvider()
    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=provider),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    completes = [
        e
        for e in events
        if isinstance(e, ResponseEvent) and e.stream_state == "complete"
    ]
    assert provider.call_count == 2
    assert [e.content for e in completes] == ["recovered"]


@pytest.mark.asyncio
async def test_child_runtime_does_not_emit_usage_response_complete() -> None:
    from matmaster.core.agent import AgentKernel

    kernel_runtime = make_kernel_runtime(
        provider=ContentOnlyProvider(),
        run_identity=RunIdentity(spawn_id="child-1"),
    )
    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        kernel_runtime, make_kernel_turn("child task")
    ):
        events.append(event)

    assert not [
        e
        for e in events
        if isinstance(e, ResponseEvent) and e.stream_state == "complete"
    ]


@pytest.mark.asyncio
async def test_root_only_run_result_usage_matches_distinct_response_turn_usage() -> (
    None
):
    from matmaster.core.agent import AgentKernel

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=ContentOnlyProvider()),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    usage: dict[str, int] = {}
    seen: set[int] = set()
    for event in events:
        if not isinstance(event, ResponseEvent):
            continue
        if event.stream_state != "complete" or event.turn_index is None:
            continue
        if event.turn_index in seen:
            continue
        seen.add(event.turn_index)
        for key, value in event.turn_usage.items():
            usage[key] = usage.get(key, 0) + value

    run_result = next(e for e in events if isinstance(e, RunResultEvent))
    assert usage == run_result.usage


@pytest.mark.asyncio
async def test_agent_tool_usage_delta_reaches_parent_run_result() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.tools.tool_result import ToolResult
    from matmaster.types.events import ToolResultEvent
    from matmaster.types.messages import StreamChunk, ToolCallData

    from .agent_kernel_test_helpers import ToolCallingProvider, make_kernel_runtime

    class AgentUsageRunner:
        async def execute_batch(self, tool_calls, ctx, *, on_result=None):
            del ctx, on_result
            return [
                (
                    tool_calls[0],
                    ToolResult(
                        status="success",
                        content="child answer",
                        payload={
                            "subagent_usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                                "total_tokens": 12,
                            }
                        },
                    ),
                )
            ]

    class UsageProvider(ToolCallingProvider):
        async def chat_stream(self, messages, tools=None, *, timeout=None):
            self._call_count += 1
            if self._call_count == 1:
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": "call-agent",
                            "name": "Agent",
                            "arguments": '{"prompt": "child"}',
                        }
                    ],
                )
                yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})
                return
            yield StreamChunk(content="done", finish_reason="stop")
            yield StreamChunk(
                usage={
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                }
            )

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(
            provider=UsageProvider(
                [ToolCallData(id="unused", name="Agent", arguments={})],
                max_tool_turns=1,
            ),
            tool_runner=AgentUsageRunner(),
        ),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    tool_event = next(e for e in events if isinstance(e, ToolResultEvent))
    run_result = next(e for e in events if isinstance(e, RunResultEvent))
    assert tool_event.turn_usage == {"prompt_tokens": 5}
    assert tool_event.total_usage == {
        "prompt_tokens": 15,
        "completion_tokens": 2,
        "total_tokens": 12,
    }
    assert run_result.usage == {
        "prompt_tokens": 22,
        "completion_tokens": 5,
        "total_tokens": 22,
    }
    assert run_result.usage_vendor_by_turn == [{}, {}]


@pytest.mark.asyncio
async def test_malformed_agent_subagent_usage_aborts_run_via_error_path() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.tools.tool_result import ToolResult
    from matmaster.types.messages import StreamChunk, ToolCallData

    from .agent_kernel_test_helpers import ToolCallingProvider, make_kernel_runtime

    class MalformedUsageRunner:
        async def execute_batch(self, tool_calls, ctx, *, on_result=None):
            del ctx, on_result
            return [
                (
                    tool_calls[0],
                    ToolResult(
                        status="success",
                        content="child answer",
                        payload={"subagent_usage": {"prompt_tokens": -1}},
                    ),
                )
            ]

    class UsageProvider(ToolCallingProvider):
        async def chat_stream(self, messages, tools=None, *, timeout=None):
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "call-agent",
                        "name": "Agent",
                        "arguments": '{"prompt": "child"}',
                    }
                ],
            )
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(
            provider=UsageProvider(
                [ToolCallData(id="unused", name="Agent", arguments={})],
                max_tool_turns=1,
            ),
            tool_runner=MalformedUsageRunner(),
        ),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    run_result = next(e for e in events if isinstance(e, RunResultEvent))
    assert run_result.status == "failed"
    assert run_result.reason == "internal_error"
    assert run_result.usage == {"prompt_tokens": 5}
