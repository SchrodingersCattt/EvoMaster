"""Token usage event behavior for AgentKernel."""

from __future__ import annotations

from typing import Any

import pytest

from matmaster.types.events import (
    ResponseEvent,
    RunResultEvent,
    ThoughtEvent,
    ToolCallEvent,
)
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
    assert tool_event.tool_name == "Agent"
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


@pytest.mark.asyncio
async def test_run_stream_emits_usage_bearing_thought_complete() -> None:
    from matmaster.core.agent import AgentKernel

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=ReasoningThenContentProvider()),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    assert len(completes) == 1
    assert completes[0].content == "thinking part 1 part 2"
    assert completes[0].reasoning_content == "thinking part 1 part 2"
    assert completes[0].turn_index == 0
    assert completes[0].turn_usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert completes[0].total_usage == {"prompt_tokens": 10, "completion_tokens": 5}

    segment_ends = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "segment_end"
    ]
    assert segment_ends
    for ev in segment_ends:
        assert ev.turn_usage == {}
        assert ev.total_usage == {}


@pytest.mark.asyncio
async def test_tool_call_events_carry_parent_turn_usage() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.tools.tool_result import ToolResult
    from matmaster.types.messages import StreamChunk, ToolCallData

    from .agent_kernel_test_helpers import ToolCallingProvider

    class EchoRunner:
        async def execute_batch(self, tool_calls, ctx, *, on_result=None):
            del ctx, on_result
            return [
                (tc, ToolResult(status="success", content="ok"))
                for tc in tool_calls
            ]

    class TwoToolCallProvider(ToolCallingProvider):
        async def chat_stream(self, messages, tools=None, *, timeout=None):
            self._call_count += 1
            if self._call_count == 1:
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": "call-1",
                            "name": "bash",
                            "arguments": '{"cmd": "ls"}',
                        },
                        {
                            "index": 1,
                            "id": "call-2",
                            "name": "bash",
                            "arguments": '{"cmd": "pwd"}',
                        },
                    ],
                )
                yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})
                return
            yield StreamChunk(content="done", finish_reason="stop")
            yield StreamChunk(usage={"prompt_tokens": 7})

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(
            provider=TwoToolCallProvider(
                [ToolCallData(id="unused", name="bash", arguments={})],
                max_tool_turns=1,
            ),
            tool_runner=EchoRunner(),
        ),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    tool_call_events = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(tool_call_events) == 2
    for tc_event in tool_call_events:
        assert tc_event.turn_index == 0
        assert tc_event.turn_usage == {"prompt_tokens": 5}
        assert tc_event.total_usage == {"prompt_tokens": 5}


@pytest.mark.asyncio
async def test_reasoning_then_tool_call_thought_and_tool_call_share_turn() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.tools.tool_result import ToolResult
    from matmaster.types.messages import StreamChunk, ToolCallData

    from .agent_kernel_test_helpers import ToolCallingProvider

    class EchoRunner:
        async def execute_batch(self, tool_calls, ctx, *, on_result=None):
            del ctx, on_result
            return [
                (tc, ToolResult(status="success", content="ok"))
                for tc in tool_calls
            ]

    class ReasoningToolProvider(ToolCallingProvider):
        async def chat_stream(self, messages, tools=None, *, timeout=None):
            self._call_count += 1
            if self._call_count == 1:
                yield StreamChunk(reasoning_content="plan the call")
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": "call-r1",
                            "name": "bash",
                            "arguments": '{"cmd": "ls"}',
                        }
                    ],
                )
                yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 8})
                return
            yield StreamChunk(content="done", finish_reason="stop")
            yield StreamChunk(usage={"prompt_tokens": 6})

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(
            provider=ReasoningToolProvider(
                [ToolCallData(id="unused", name="bash", arguments={})],
                max_tool_turns=1,
            ),
            tool_runner=EchoRunner(),
        ),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    thought_completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    tool_call_events = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(thought_completes) == 1
    assert thought_completes[0].content == "plan the call"
    assert thought_completes[0].turn_usage == {"prompt_tokens": 8}
    assert len(tool_call_events) == 1
    assert tool_call_events[0].turn_index == thought_completes[0].turn_index
    assert tool_call_events[0].turn_usage == {"prompt_tokens": 8}


@pytest.mark.asyncio
async def test_retry_discarded_attempt_does_not_emit_usage_thought_complete() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.types.messages import LLMResponse, StreamChunk

    from .agent_kernel_test_helpers import ProviderProtocolAttrs

    class ReasoningRetryProvider(ProviderProtocolAttrs):
        """First attempt reasoning-only (incomplete, retried); second accepted."""

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
                yield StreamChunk(reasoning_content="discarded reasoning")
                yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 3})
            else:
                yield StreamChunk(reasoning_content="kept reasoning")
                yield StreamChunk(content="answer")
                yield StreamChunk(
                    finish_reason="stop", usage={"prompt_tokens": 10}
                )

    provider = ReasoningRetryProvider()
    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=provider),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    assert provider.call_count == 2
    completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    assert [e.content for e in completes] == ["kept reasoning"]
    assert completes[0].turn_usage == {"prompt_tokens": 10}
    for ev in events:
        if isinstance(ev, ThoughtEvent) and ev.stream_state != "complete":
            assert ev.turn_usage == {}


@pytest.mark.asyncio
async def test_invalid_finish_reasoning_only_still_emits_thought_complete() -> None:
    """Spec invalid finish: reasoning-only accepted response still emits audit."""
    from matmaster.core.agent import AgentKernel
    from matmaster.types.messages import LLMResponse, StreamChunk

    from .agent_kernel_test_helpers import ProviderProtocolAttrs

    class ReasoningOnlyProvider(ProviderProtocolAttrs):
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
            yield StreamChunk(reasoning_content=f"attempt {self.call_count}")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 4})

    provider = ReasoningOnlyProvider()
    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=provider),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    assert provider.call_count == 2
    completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    assert [e.content for e in completes] == ["attempt 2"]
    assert completes[0].turn_usage == {"prompt_tokens": 4}
    run_result = next(e for e in events if isinstance(e, RunResultEvent))
    assert run_result.status == "failed"
    assert run_result.reason == "invalid_finish"
    assert run_result.usage == {"prompt_tokens": 4}
    assert not [e for e in events if isinstance(e, ToolCallEvent)]
