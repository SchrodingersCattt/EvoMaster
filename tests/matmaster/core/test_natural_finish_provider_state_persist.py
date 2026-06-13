import pytest

from matmaster.core.agent import AgentKernel
from matmaster.types.events import AssistantStateEvent
from matmaster.types.messages import ProviderState, StreamChunk
from tests.matmaster.core.agent_kernel_test_helpers import (
    StreamingProvider,
    make_kernel_runtime,
    make_kernel_turn,
)


async def _run_with_chunks(chunks: list[StreamChunk]) -> list:
    kernel = AgentKernel()
    runtime = make_kernel_runtime(provider=StreamingProvider(chunks))
    events = []
    async for item in kernel.run_stream(
        runtime,
        make_kernel_turn("question"),
    ):
        events.append(item)
    return events


@pytest.mark.asyncio
async def test_natural_finish_with_provider_state_emits_assistant_state():
    events = await _run_with_chunks(
        [
            StreamChunk(content="final answer"),
            StreamChunk(finish_reason="stop"),
            StreamChunk(
                provider_state=ProviderState(transport="fake", payload={"sig": "z"})
            ),
        ]
    )
    state_events = [e for e in events if isinstance(e, AssistantStateEvent)]
    assert len(state_events) == 1
    assert state_events[0].state["provider_state"] == {
        "transport": "fake",
        "payload": {"sig": "z"},
    }


@pytest.mark.asyncio
async def test_natural_finish_without_provider_state_emits_no_assistant_state():
    events = await _run_with_chunks(
        [
            StreamChunk(content="plain answer"),
            StreamChunk(finish_reason="stop"),
        ]
    )
    assert [e for e in events if isinstance(e, AssistantStateEvent)] == []
