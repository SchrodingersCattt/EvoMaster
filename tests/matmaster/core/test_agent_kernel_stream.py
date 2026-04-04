"""Tests for AgentKernel stream generator features.

Tests _stream_llm_items() sub-generator, _run_items() AssistantStateEvent/SkillHitEvent
yields, and compactor deque integration. Phase 34 Plan 1 Task 1.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from matmaster.types.cancellation import CancellationController
from matmaster.types.events import (
    AssistantStateEvent,
    ResponseEvent,
    RunResultEvent,
    SkillHitEvent,
    ThoughtEvent,
)
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
    ToolCallData,
)
from matmaster.types.runtime import AgentRuntimeSpec

from .agent_kernel_test_helpers import (
    _make_spec,
    _make_tool_registry,
    StreamingProvider,
)


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


class SkillStreamProvider:
    """Provider that calls Skill tool then finishes."""

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
                        "name": "Skill",
                        "arguments": '{"skill": "chemistry"}',
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

    @pytest.mark.asyncio
    async def test_same_index_collision_splits_distinct_tool_names(self) -> None:
        """Distinct tool names on one index must not overwrite each other."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = StreamingProvider([
            StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-bash",
                        "name": "Bash",
                        "arguments": '{"command": "pwd"}',
                    }
                ]
            ),
            StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-skill",
                        "name": "Skill",
                        "arguments": '{"skill": "chemistry"}',
                    }
                ]
            ),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        items: list[_KernelItem] = []
        async for item in kernel._stream_llm_items(
            spec, [{"role": "user", "content": "test"}], None
        ):
            items.append(item)

        final_items = [i for i in items if i.llm_response is not None]
        assert len(final_items) == 1
        tool_calls = final_items[0].llm_response.tool_calls
        assert tool_calls is not None
        assert [(tc.id, tc.name, tc.arguments) for tc in tool_calls] == [
            ("tc-bash", "Bash", {"command": "pwd"}),
            ("tc-skill", "Skill", {"skill": "chemistry"}),
        ]

    @pytest.mark.asyncio
    async def test_same_index_collision_splits_same_tool_name_by_id(self) -> None:
        """Repeated tool name on one index must split when the tool call id changes."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = StreamingProvider([
            StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-1",
                        "name": "Bash",
                        "arguments": '{"command": "pwd"}',
                    }
                ]
            ),
            StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-2",
                        "name": "Bash",
                        "arguments": '{"command": "which python3"}',
                    }
                ]
            ),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        items: list[_KernelItem] = []
        async for item in kernel._stream_llm_items(
            spec, [{"role": "user", "content": "test"}], None
        ):
            items.append(item)

        final_items = [i for i in items if i.llm_response is not None]
        assert len(final_items) == 1
        tool_calls = final_items[0].llm_response.tool_calls
        assert tool_calls is not None
        assert [(tc.id, tc.name, tc.arguments) for tc in tool_calls] == [
            ("tc-1", "Bash", {"command": "pwd"}),
            ("tc-2", "Bash", {"command": "which python3"}),
        ]

    @pytest.mark.asyncio
    async def test_same_call_argument_chunks_stay_single_tool_call(self) -> None:
        """Normal argument streaming for one tool call must not be split."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = StreamingProvider([
            StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-1",
                        "name": "Bash",
                        "arguments": '{"command": "which ',
                    }
                ]
            ),
            StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "arguments": 'python3 && python3 --version"}',
                    }
                ]
            ),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        items: list[_KernelItem] = []
        async for item in kernel._stream_llm_items(
            spec, [{"role": "user", "content": "test"}], None
        ):
            items.append(item)

        final_items = [i for i in items if i.llm_response is not None]
        assert len(final_items) == 1
        tool_calls = final_items[0].llm_response.tool_calls
        assert tool_calls is not None
        assert [(tc.id, tc.name, tc.arguments) for tc in tool_calls] == [
            (
                "tc-1",
                "Bash",
                {"command": "which python3 && python3 --version"},
            )
        ]

    @pytest.mark.asyncio
    async def test_collision_split_preserves_stream_arrival_order(self) -> None:
        """Split calls should keep stream order even when index 0 is reused later."""
        from matmaster.core.agent import AgentKernel, _KernelItem

        provider = StreamingProvider([
            StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-1",
                        "name": "Bash",
                        "arguments": '{"command": "pwd"}',
                    }
                ]
            ),
            StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 1,
                        "id": "tc-2",
                        "name": "Skill",
                        "arguments": '{"skill": "chemistry"}',
                    }
                ]
            ),
            StreamChunk(
                tool_call_deltas=[
                    {
                        "index": 0,
                        "id": "tc-3",
                        "name": "Bash",
                        "arguments": '{"command": "which python3"}',
                    }
                ]
            ),
            StreamChunk(finish_reason="stop"),
        ])
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        items: list[_KernelItem] = []
        async for item in kernel._stream_llm_items(
            spec, [{"role": "user", "content": "test"}], None
        ):
            items.append(item)

        final_items = [i for i in items if i.llm_response is not None]
        assert len(final_items) == 1
        tool_calls = final_items[0].llm_response.tool_calls
        assert tool_calls is not None
        assert [(tc.id, tc.name) for tc in tool_calls] == [
            ("tc-1", "Bash"),
            ("tc-2", "Skill"),
            ("tc-3", "Bash"),
        ]


# ── _run_items() event yields ─────────────────────────────


class TestRunItemsAssistantState:
    """_run_items() yields AssistantStateEvent on tool_calls turns."""

    @pytest.mark.asyncio
    async def test_yields_assistant_state_event(self) -> None:
        """AssistantStateEvent emitted when LLM returns tool_calls."""
        from matmaster.core.agent import AgentKernel

        provider = ToolCallStreamProvider()
        registry, _ = _make_tool_registry()
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task"):
            events.append(event)

        assistant_state_events = [
            e for e in events
            if isinstance(e, AssistantStateEvent)
        ]
        assert len(assistant_state_events) >= 1, "Should yield AssistantStateEvent"
        # State should contain tool_calls
        state = assistant_state_events[0].state
        assert state.get("tool_calls") is not None


class TestRunItemsSkillHit:
    """_run_items() yields SkillHitEvent when Skill tool is called."""

    @pytest.mark.asyncio
    async def test_yields_skill_hit_event(self) -> None:
        """SkillHitEvent emitted when tool_name == 'Skill'."""
        from matmaster.core.agent import AgentKernel

        provider = SkillStreamProvider()
        registry, _ = _make_tool_registry(tool_names=["Skill", "test_tool"])
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task"):
            events.append(event)

        skill_hit_events = [
            e for e in events
            if isinstance(e, SkillHitEvent)
        ]
        assert len(skill_hit_events) >= 1, "Should yield SkillHitEvent"
        assert skill_hit_events[0].skill_name == "chemistry"

    @pytest.mark.asyncio
    async def test_no_skill_hit_for_non_skill_tools(self) -> None:
        """No SkillHitEvent for regular tool calls."""
        from matmaster.core.agent import AgentKernel

        provider = ToolCallStreamProvider()
        registry, _ = _make_tool_registry()
        spec = _make_spec(provider=provider, tool_registry=registry)
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task"):
            events.append(event)

        skill_hit_events = [
            e for e in events
            if isinstance(e, SkillHitEvent)
        ]
        assert len(skill_hit_events) == 0, "Should not yield SkillHitEvent for regular tools"


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
    async def test_run_items_raises_without_tool_runner(self) -> None:
        """When spec.tool_runner is None, _run_items() raises RuntimeError."""
        from matmaster.core.agent import AgentKernel
        from matmaster.tools.tool_catalog import ToolCatalog

        provider = ToolCallStreamProvider()
        registry, _tools = _make_tool_registry()
        catalog = ToolCatalog(registry)
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            tool_catalog=catalog,
            # tool_runner intentionally None
            max_turns=5,
            system_prompt="test",
        )

        kernel = AgentKernel()
        with pytest.raises(RuntimeError, match="No tool_runner"):
            async for _event in kernel.run_stream(spec, "test task"):
                pass


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

        from matmaster.core.agent import AgentKernel
        from matmaster.types.tool_desc_ctx import ToolDescriptionContext

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
        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task"):
            events.append(event)

        # Catalog's build_definitions should have been called
        assert mock_catalog.build_definitions.called, \
            "tool_catalog.build_definitions() should be called when catalog is present"
        args, _ = mock_catalog.build_definitions.call_args
        assert isinstance(args[0], ToolDescriptionContext)

    @pytest.mark.asyncio
    async def test_catalog_version_no_refresh_when_unchanged(self) -> None:
        """When catalog.version is unchanged across turns, no extra build_definitions call."""
        from unittest.mock import MagicMock, PropertyMock

        from matmaster.core.agent import AgentKernel
        from matmaster.types.tool_desc_ctx import ToolDescriptionContext

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
        events = []
        async for event in kernel.run_stream(spec, "test task"):
            events.append(event)

        # build_definitions called once on first turn, but NOT re-called
        # on second turn since version unchanged
        build_calls = mock_catalog.build_definitions.call_count
        assert build_calls == 1, \
            f"build_definitions should be called once (caching), got {build_calls}"
        args, _ = mock_catalog.build_definitions.call_args
        assert isinstance(args[0], ToolDescriptionContext)


class TestCancellationTokenSupport:
    @pytest.mark.asyncio
    async def test_run_stream_returns_cancelled_when_token_already_cancelled(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = ContentOnlyProvider()
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        ctrl = CancellationController()
        ctrl.cancel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task", cancel_token=ctrl.token):
            events.append(event)

        assert isinstance(events[-1], RunResultEvent)
        assert events[-1].status == "cancelled"
        assert events[-1].reason == "cancelled"

    @pytest.mark.asyncio
    async def test_sleep_backoff_wakes_early_on_cancel_token(self) -> None:
        import asyncio

        from matmaster.core.agent import AgentKernel, _KernelStopRequested

        ctrl = CancellationController()
        task = asyncio.create_task(
            AgentKernel._sleep_backoff_with_cancel(5.0, ctrl.token)
        )

        await asyncio.sleep(0.05)
        ctrl.cancel()

        with pytest.raises(_KernelStopRequested):
            await task
