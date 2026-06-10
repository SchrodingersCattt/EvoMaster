from __future__ import annotations

import pytest

from matmaster.core.agent_tool_dispatch import (
    InvalidToolUsageDelta,
    dispatch_tool_calls,
    extract_tool_usage_delta,
)
from matmaster.core.kernel_items import _KernelState
from matmaster.tools.tool_result import ToolResult
from matmaster.types.events import ToolResultEvent
from matmaster.types.messages import ImageContentPart, SystemMessage, ToolCallData


def test_extract_tool_usage_delta_ignores_non_agent_tools() -> None:
    result = ToolResult(
        status="success",
        content="ok",
        payload={"subagent_usage": {"prompt_tokens": 10}},
    )

    assert extract_tool_usage_delta("Read", result) == {}


def test_extract_tool_usage_delta_missing_subagent_usage_returns_empty() -> None:
    result = ToolResult(status="success", content="ok", payload={})

    assert extract_tool_usage_delta("Agent", result) == {}


@pytest.mark.parametrize(
    "usage",
    [
        "not-a-dict",
        {"prompt_tokens": "10"},
        {"prompt_tokens": True},
        {"prompt_tokens": -1},
    ],
)
def test_extract_tool_usage_delta_rejects_malformed_agent_usage(usage) -> None:
    result = ToolResult(
        status="success",
        content="ok",
        payload={"subagent_usage": usage},
    )

    with pytest.raises(InvalidToolUsageDelta):
        extract_tool_usage_delta("Agent", result)


def test_extract_tool_usage_delta_allows_cancelled_agent_without_usage() -> None:
    result = ToolResult(status="cancelled", content="Run cancelled.", payload={})

    assert extract_tool_usage_delta("Agent", result) == {}


class StaticRunner:
    def __init__(self, results):
        self.results = results

    async def execute_batch(self, tool_calls, ctx, *, on_result=None):
        del ctx, on_result
        return list(zip(tool_calls, self.results))


@pytest.mark.asyncio
async def test_dispatch_tool_calls_accumulates_agent_usage_before_event() -> None:
    state = _KernelState(
        messages=[SystemMessage(content="sys")],
        turn=1,
        turn_usage={"prompt_tokens": 5},
        total_usage={"prompt_tokens": 5},
    )
    tool_call = ToolCallData(
        id="call-agent",
        name="Agent",
        arguments={"prompt": "child task"},
    )
    runner = StaticRunner(
        [
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
            )
        ]
    )

    items = [
        item
        async for item in dispatch_tool_calls(
            tool_calls=[tool_call],
            tool_runner=runner,
            max_turns=10,
            state=state,
            cancel_token=None,
        )
    ]

    event = items[0].event
    assert isinstance(event, ToolResultEvent)
    assert "turn_usage" not in ToolResultEvent.model_fields
    assert state.total_usage == {
        "prompt_tokens": 15,
        "completion_tokens": 2,
        "total_tokens": 12,
    }


@pytest.mark.asyncio
async def test_dispatch_tool_calls_accumulates_each_agent_usage() -> None:
    state = _KernelState(
        messages=[SystemMessage(content="sys")],
        turn=1,
        turn_usage={"prompt_tokens": 5},
        total_usage={"prompt_tokens": 5},
    )
    tool_calls = [
        ToolCallData(id="call-1", name="Agent", arguments={}),
        ToolCallData(id="call-2", name="Agent", arguments={}),
    ]
    runner = StaticRunner(
        [
            ToolResult(
                status="success",
                content="one",
                payload={"subagent_usage": {"prompt_tokens": 10}},
            ),
            ToolResult(
                status="success",
                content="two",
                payload={"subagent_usage": {"prompt_tokens": 20}},
            ),
        ]
    )

    events = [
        item.event
        async for item in dispatch_tool_calls(
            tool_calls=tool_calls,
            tool_runner=runner,
            max_turns=10,
            state=state,
            cancel_token=None,
        )
        if isinstance(item.event, ToolResultEvent)
    ]

    assert len(events) == 2
    assert state.total_usage == {"prompt_tokens": 35}


@pytest.mark.asyncio
async def test_dispatch_propagates_tool_result_images() -> None:
    image = ImageContentPart(
        url="data:image/png;base64,aGVsbG8=", mime_type="image/png"
    )
    state = _KernelState(messages=[SystemMessage(content="sys")], turn=1)
    tool_call = ToolCallData(id="tc1", name="Read", arguments={"file_path": "/a.png"})
    runner = StaticRunner(
        [ToolResult(status="success", content="Read image: /a.png", images=[image])]
    )

    items = [
        item
        async for item in dispatch_tool_calls(
            tool_calls=[tool_call],
            tool_runner=runner,
            max_turns=10,
            state=state,
            cancel_token=None,
        )
    ]

    tool_msg = state.messages[-1]
    assert tool_msg.images == [image]
    event = items[0].event
    assert isinstance(event, ToolResultEvent)
    assert event.images == [image]
