"""Tests for AgentKernel stream generator features.

Tests _stream_llm_items() sub-generator, _run_items() AssistantStateEvent/SkillHitEvent
yields, and compactor deque integration. Phase 34 Plan 1 Task 1.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from matmaster.types.events import (
    AssistantStateEvent,
    ResponseEvent,
    SkillHitEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
    ToolCallData,
)
from matmaster.types.runtime import AgentRuntimeSpec

from .agent_kernel_test_helpers import (
    StreamingProvider,
    ToolCallingProvider,
    _make_spec,
    _make_tool_registry,
)
from .conftest import MockLLMProvider


# ── Providers for streaming tests ─────────────────────────


class ReasoningThenContentProvider:
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
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 5})


class ContentOnlyProvider:
    """Provider that only streams content, no reasoning."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="not used", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="hello ")
        yield StreamChunk(content="world")
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})


class ToolCallStreamProvider:
    """Provider that streams content then tool_calls, then finishes naturally."""

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
            yield StreamChunk(content="let me call a tool")
            yield StreamChunk(
                tool_call_deltas=[
                    {"index": 0, "id": "tc-1", "name": "test_tool", "arguments": '{"x": 1}'}
                ]
            )
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10})
        else:
            yield StreamChunk(content="done")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10})


class UseSkillStreamProvider:
    """Provider that calls use_skill tool then finishes."""

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
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-skill",
                        "name": "use_skill",
                        "arguments": '{"skill_name": "chemistry"}',
                    }
                ]
            )
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10})
        else:
            yield StreamChunk(content="done")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 10})


# ── _stream_llm_items() tests ─────────────────────────────


class TestStreamLlmItems:
    """Tests for _stream_llm_items() sub-generator."""

    @pytest.mark.asyncio
    async def test_yields_thought_and_response_events(self) -> None:
        """Reasoning chunks yield ThoughtEvent, content chunks yield ResponseEvent."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = ReasoningThenContentProvider()
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        api_messages = [{"role": "user", "content": "test"}]
        items: list[_KernelItem] = []
        async for item in kernel._stream_llm_items(spec, api_messages, None):
            items.append(item)

        # Should have: start event, reasoning events, thought-complete, content events,
        # response-complete, end event, final llm_response
        thought_events = [i for i in items if i.event and isinstance(i.event, ThoughtEvent)]
        response_events = [i for i in items if i.event and isinstance(i.event, ResponseEvent)]

        # At least one streaming thought and one streaming response
        streaming_thoughts = [e for e in thought_events if e.event.stream_state == "streaming"]
        streaming_responses = [e for e in response_events if e.event.stream_state == "streaming"]
        assert len(streaming_thoughts) >= 1, "Should yield streaming ThoughtEvents"
        assert len(streaming_responses) >= 1, "Should yield streaming ResponseEvents"

    @pytest.mark.asyncio
    async def test_segment_complete_on_reasoning_to_content(self) -> None:
        """ThoughtEvent(complete) emitted when transitioning from reasoning to content."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = ReasoningThenContentProvider()
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        api_messages = [{"role": "user", "content": "test"}]
        items: list[_KernelItem] = []
        async for item in kernel._stream_llm_items(spec, api_messages, None):
            items.append(item)

        # Find thought-complete event
        thought_completes = [
            i for i in items
            if i.event and isinstance(i.event, ThoughtEvent) and i.event.stream_state == "complete"
        ]
        assert len(thought_completes) >= 1, "Should yield ThoughtEvent(complete) on transition"
        # The complete event should contain the full reasoning
        assert "thinking part 1" in thought_completes[0].event.content

    @pytest.mark.asyncio
    async def test_response_complete_at_stream_end(self) -> None:
        """ResponseEvent(complete) emitted at end of content stream."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = ReasoningThenContentProvider()
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        api_messages = [{"role": "user", "content": "test"}]
        items: list[_KernelItem] = []
        async for item in kernel._stream_llm_items(spec, api_messages, None):
            items.append(item)

        response_completes = [
            i for i in items
            if i.event and isinstance(i.event, ResponseEvent) and i.event.stream_state == "complete"
        ]
        assert len(response_completes) >= 1, "Should yield ResponseEvent(complete)"
        assert "visible part 1" in response_completes[0].event.content

    @pytest.mark.asyncio
    async def test_final_yield_carries_llm_response(self) -> None:
        """Last yielded _KernelItem carries llm_response field."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = ReasoningThenContentProvider()
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        api_messages = [{"role": "user", "content": "test"}]
        items: list[_KernelItem] = []
        async for item in kernel._stream_llm_items(spec, api_messages, None):
            items.append(item)

        # Last item should have llm_response
        final_items = [i for i in items if i.llm_response is not None]
        assert len(final_items) == 1, "Exactly one item should carry llm_response"
        resp = final_items[0].llm_response
        assert resp.content is not None
        assert "visible part 1" in resp.content
        assert resp.reasoning_content is not None
        assert "thinking part 1" in resp.reasoning_content

    @pytest.mark.asyncio
    async def test_start_and_end_events(self) -> None:
        """Stream start and end marker events are yielded."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = ContentOnlyProvider()
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        api_messages = [{"role": "user", "content": "test"}]
        items: list[_KernelItem] = []
        async for item in kernel._stream_llm_items(spec, api_messages, None):
            items.append(item)

        # First event should be start marker (ThoughtEvent with start state)
        start_events = [
            i for i in items
            if i.event and isinstance(i.event, ThoughtEvent) and i.event.stream_state == "start"
        ]
        assert len(start_events) == 1, "Should yield a start marker event"

        # Last event (before llm_response) should be end marker
        end_events = [
            i for i in items
            if i.event and isinstance(i.event, ResponseEvent) and i.event.stream_state == "end"
        ]
        assert len(end_events) == 1, "Should yield an end marker event"


# ── _run_items() event yields ─────────────────────────────


class TestRunItemsAssistantState:
    """_run_items() yields AssistantStateEvent on tool_calls turns."""

    @pytest.mark.asyncio
    async def test_yields_assistant_state_event(self) -> None:
        """AssistantStateEvent emitted when LLM returns tool_calls."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = ToolCallStreamProvider()
        registry, _ = _make_tool_registry()
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()

        items: list[_KernelItem] = []
        async for item in kernel.run_stream(spec, "test task"):
            items.append(item)

        assistant_state_events = [
            i for i in items
            if i.event and isinstance(i.event, AssistantStateEvent)
        ]
        assert len(assistant_state_events) >= 1, "Should yield AssistantStateEvent"
        # State should contain tool_calls
        state = assistant_state_events[0].event.state
        assert state.get("tool_calls") is not None


class TestRunItemsSkillHit:
    """_run_items() yields SkillHitEvent when use_skill is called."""

    @pytest.mark.asyncio
    async def test_yields_skill_hit_event(self) -> None:
        """SkillHitEvent emitted when tool_name == 'use_skill'."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = UseSkillStreamProvider()
        registry, _ = _make_tool_registry(tool_names=["use_skill", "test_tool"])
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()

        items: list[_KernelItem] = []
        async for item in kernel.run_stream(spec, "test task"):
            items.append(item)

        skill_hit_events = [
            i for i in items
            if i.event and isinstance(i.event, SkillHitEvent)
        ]
        assert len(skill_hit_events) >= 1, "Should yield SkillHitEvent"
        assert skill_hit_events[0].event.skill_name == "chemistry"

    @pytest.mark.asyncio
    async def test_no_skill_hit_for_non_skill_tools(self) -> None:
        """No SkillHitEvent for regular tool calls."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = ToolCallStreamProvider()
        registry, _ = _make_tool_registry()
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()

        items: list[_KernelItem] = []
        async for item in kernel.run_stream(spec, "test task"):
            items.append(item)

        skill_hit_events = [
            i for i in items
            if i.event and isinstance(i.event, SkillHitEvent)
        ]
        assert len(skill_hit_events) == 0, "Should not yield SkillHitEvent for regular tools"


# ── Regression: run() still works ─────────────────────────


class TestRunBackwardCompat:
    """Ensure existing kernel.run() still works through _run_items()."""

    @pytest.mark.asyncio
    async def test_natural_finish_via_run(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider([
            StreamChunk(content="Hello"),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test task")
        assert result.result.reason == "natural"
        assert result.result.final_content == "Hello"

    @pytest.mark.asyncio
    async def test_tool_calls_via_run(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="test_tool", arguments={"x": 1})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=1)
        registry, _ = _make_tool_registry()
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test")
        assert result.result.reason == "natural"


# ── Gap Closure tests (Phase 34 Plan 4) ────────────────────


class TestGap1FullToolRunnerActivation:
    """Gap 1: _run_items() calls spec.tool_runner.execute_batch() when present."""

    @pytest.mark.asyncio
    async def test_run_items_uses_tool_runner_when_present(self) -> None:
        """When spec.tool_runner is not None, _run_items() delegates to execute_batch()."""
        from unittest.mock import AsyncMock, MagicMock

        from matmaster.core.agent import AgentKernel, _KernelItem
        from matmaster.core.tool_runner import ToolExecutionContext
        from matmaster.tools.tool_result import ToolResult as TR

        provider = ToolCallStreamProvider()
        registry, _ = _make_tool_registry()

        # Mock ToolRunner that records calls
        mock_runner = MagicMock()
        mock_runner.execute_batch = AsyncMock(return_value=[
            (ToolCallData(id="tc-1", name="test_tool", arguments={"x": 1}),
             TR(status="success", content="runner result"))
        ])

        spec = _make_spec(provider=provider, tool_registry=registry)
        # Inject tool_runner via model_copy (frozen model)
        spec = spec.model_copy(update={"tool_runner": mock_runner})

        kernel = AgentKernel()
        items: list[_KernelItem] = []
        async for item in kernel.run_stream(spec, "test task"):
            items.append(item)

        # FullToolRunner.execute_batch should have been called
        assert mock_runner.execute_batch.called, \
            "spec.tool_runner.execute_batch() should be called when tool_runner is present"
        # Verify ToolExecutionContext was passed
        call_args = mock_runner.execute_batch.call_args
        ctx_arg = call_args[0][1]  # second positional arg
        assert isinstance(ctx_arg, ToolExecutionContext), \
            "Second arg to execute_batch should be ToolExecutionContext"

    @pytest.mark.asyncio
    async def test_run_items_falls_back_to_registry_without_tool_runner(self) -> None:
        """When spec.tool_runner is None, _run_items() uses spec.tool_registry.execute()."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = ToolCallStreamProvider()
        registry, tools = _make_tool_registry()
        spec = _make_spec(provider=provider, tool_registry=registry)
        # tool_runner is None by default

        kernel = AgentKernel()
        items: list[_KernelItem] = []
        async for item in kernel.run_stream(spec, "test task"):
            items.append(item)

        # Tool should have been executed via registry (tools record calls)
        tool_result_events = [
            i for i in items
            if i.event and isinstance(i.event, ToolResultEvent)
        ]
        assert len(tool_result_events) >= 1, \
            "Should yield ToolResultEvent from registry fallback path"


class TestGap2RunStreamYieldsBusEvent:
    """Gap 2: run_stream() yields BusEvent objects, not _KernelItem."""

    @pytest.mark.asyncio
    async def test_run_stream_yields_bus_event_not_kernel_item(self) -> None:
        """run_stream() must yield BusEvent objects, with RunResultEvent as terminal."""
        from matmaster.core.agent import AgentKernel, _KernelItem
        from matmaster.types.events import RunResultEvent

        provider = ContentOnlyProvider()
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task"):
            events.append(event)

        # No event should be a _KernelItem
        for event in events:
            assert not isinstance(event, _KernelItem), \
                f"run_stream() yielded _KernelItem: {event!r}. Must yield BusEvent."

        # All events should have 'type' attribute (BusEvent signature)
        for event in events:
            assert hasattr(event, 'type'), \
                f"Yielded object missing 'type' attribute: {type(event).__name__}"

        # Last event should be RunResultEvent
        assert isinstance(events[-1], RunResultEvent), \
            f"Last event should be RunResultEvent, got {type(events[-1]).__name__}"
        assert events[-1].status == "completed"

    @pytest.mark.asyncio
    async def test_run_stream_with_tool_calls_yields_bus_events(self) -> None:
        """run_stream() with tool calls also yields only BusEvent objects."""
        from matmaster.core.agent import AgentKernel, _KernelItem
        from matmaster.types.events import RunResultEvent

        provider = ToolCallStreamProvider()
        registry, _ = _make_tool_registry()
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task"):
            events.append(event)

        for event in events:
            assert not isinstance(event, _KernelItem), \
                f"run_stream() yielded _KernelItem: {event!r}"
            assert hasattr(event, 'type'), \
                f"Missing 'type' attribute: {type(event).__name__}"

        assert isinstance(events[-1], RunResultEvent), \
            f"Last event should be RunResultEvent, got {type(events[-1]).__name__}"


class TestGap3CatalogVersionInvalidation:
    """Gap 3: catalog.version change invalidates cached tool_definitions."""

    @pytest.mark.asyncio
    async def test_catalog_version_invalidates_tool_definitions(self) -> None:
        """When catalog.version changes, _run_items rebuilds tool_definitions."""
        from unittest.mock import MagicMock, PropertyMock

        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = ContentOnlyProvider()
        registry, _ = _make_tool_registry()

        # Mock ToolCatalog with controllable version
        mock_catalog = MagicMock()
        type(mock_catalog).version = PropertyMock(return_value=1)
        mock_catalog.build_definitions = MagicMock(return_value=[
            {"type": "function", "function": {"name": "test", "parameters": {}}}
        ])

        spec = _make_spec(provider=provider, tool_registry=registry)
        spec = spec.model_copy(update={"tool_catalog": mock_catalog})

        kernel = AgentKernel()
        items: list[_KernelItem] = []
        async for item in kernel.run_stream(spec, "test task"):
            items.append(item)

        # Catalog's build_definitions should have been called
        assert mock_catalog.build_definitions.called, \
            "tool_catalog.build_definitions() should be called when catalog is present"

    @pytest.mark.asyncio
    async def test_catalog_version_no_refresh_when_unchanged(self) -> None:
        """When catalog.version is unchanged across turns, no extra build_definitions call."""
        from unittest.mock import MagicMock, PropertyMock, call

        from matmaster.core.agent import AgentKernel, _KernelItem

        # Provider that makes 2 turns (tool call then natural finish)
        provider = ToolCallStreamProvider()
        registry, _ = _make_tool_registry()

        mock_catalog = MagicMock()
        type(mock_catalog).version = PropertyMock(return_value=1)
        mock_catalog.build_definitions = MagicMock(return_value=[
            {"type": "function", "function": {"name": "test_tool", "parameters": {}}}
        ])

        spec = _make_spec(provider=provider, tool_registry=registry)
        spec = spec.model_copy(update={"tool_catalog": mock_catalog})

        kernel = AgentKernel()
        items = []
        async for item in kernel.run_stream(spec, "test task"):
            items.append(item)

        # build_definitions called once on first turn, but NOT re-called
        # on second turn since version unchanged
        build_calls = mock_catalog.build_definitions.call_count
        assert build_calls == 1, \
            f"build_definitions should be called once (caching), got {build_calls}"
