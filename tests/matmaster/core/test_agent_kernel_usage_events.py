"""Token usage event behavior for AgentKernel."""

from __future__ import annotations

from typing import Any

import pytest

from matmaster.types.events import ResponseEvent, RunResultEvent

from .agent_kernel_test_helpers import _make_spec
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
    spec = _make_spec(provider=provider)
    items: list[_KernelItem] = []
    async for item in stream_llm_items(
        spec, [{"role": "user", "content": "test"}], None
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
        _make_spec(provider=ContentOnlyProvider()), "test task"
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
        _make_spec(provider=provider), "test task"
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

    spec = _make_spec(provider=ContentOnlyProvider()).model_copy(
        update={"meta": {"spawn_id": "child-1"}}
    )
    events: list[Any] = []
    async for event in AgentKernel().run_stream(spec, "child task"):
        events.append(event)

    assert not [
        e
        for e in events
        if isinstance(e, ResponseEvent) and e.stream_state == "complete"
    ]


@pytest.mark.asyncio
async def test_completed_run_result_usage_matches_distinct_response_turn_usage() -> (
    None
):
    from matmaster.core.agent import AgentKernel

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        _make_spec(provider=ContentOnlyProvider()), "test task"
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
