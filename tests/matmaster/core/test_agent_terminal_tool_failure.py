from __future__ import annotations

import pytest

from matmaster.core.agent import AgentKernel
from matmaster.tools.tool_result import ToolResult
from matmaster.types.events import ResponseEvent, RunResultEvent
from matmaster.types.messages import ToolCallData

from .agent_kernel_test_helpers import (
    ToolCallingProvider,
    make_kernel_runtime,
    make_kernel_turn,
)


class _RunTerminalToolRunner:
    async def execute_batch(self, tool_calls, _ctx, *, on_result=None):
        results = []
        for tool_call in tool_calls:
            result = ToolResult(
                status="error",
                content="Node unavailable; do not retry in this run.",
                meta={
                    "error_code": "BOHRIUM_NODE_UNAVAILABLE",
                    "retryable": False,
                    "failure_scope": "run",
                    "terminal_on_repeat": True,
                    "stop_message": "Node work stopped for this run.",
                },
            )
            results.append((tool_call, result))
            if on_result:
                await on_result(tool_call, result)
        return results


@pytest.mark.asyncio
async def test_repeated_run_terminal_tool_error_stops_without_third_llm_call() -> None:
    provider = ToolCallingProvider(
        [ToolCallData(id="node-call", name="test_tool", arguments={})],
        max_tool_turns=99,
    )
    runtime = make_kernel_runtime(
        provider=provider,
        tool_runner=_RunTerminalToolRunner(),
        max_turns=10,
    )

    events = [
        event
        async for event in AgentKernel().run_stream(
            runtime,
            make_kernel_turn("use the node"),
        )
    ]

    assert provider._call_count == 2
    assert isinstance(events[-1], RunResultEvent)
    assert events[-1].reason == "natural"
    assert events[-1].num_turns == 2
    assert events[-1].final_content == "Node work stopped for this run."
    assert any(
        isinstance(event, ResponseEvent)
        and event.content == "Node work stopped for this run."
        for event in events
    )


@pytest.mark.asyncio
async def test_first_run_terminal_tool_error_allows_one_fallback_llm_turn() -> None:
    provider = ToolCallingProvider(
        [ToolCallData(id="node-call", name="test_tool", arguments={})],
        max_tool_turns=1,
        final_content="I will continue without the Node.",
    )
    runtime = make_kernel_runtime(
        provider=provider,
        tool_runner=_RunTerminalToolRunner(),
        max_turns=10,
    )

    events = [
        event
        async for event in AgentKernel().run_stream(
            runtime,
            make_kernel_turn("use the node"),
        )
    ]

    assert provider._call_count == 2
    assert isinstance(events[-1], RunResultEvent)
    assert events[-1].final_content == "I will continue without the Node."
