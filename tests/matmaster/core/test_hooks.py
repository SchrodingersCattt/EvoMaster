"""Tests for matmaster.core.hooks -- Hook Protocol, BaseHook, HookAction, run_* helpers, EventEmitterHook."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from matmaster.core.bus import MessageBus
from matmaster.tools.tool_result import ToolResult
from matmaster.types.events import (
    ResponseEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from matmaster.core.hooks import (
    BaseHook,
    EventEmitterHook,
    Hook,
    HookAction,
    run_guard_blocked,
    run_on_segment_complete,
    run_on_stream_chunk,
    run_post_tool_call,
    run_pre_llm_call,
    run_pre_tool_call,
    run_should_continue,
)
from matmaster.types.guards import GuardResult
from matmaster.types.messages import (
    AssistantMessage,
    Message,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    UserMessage,
)


# ── Fixtures ──────────────────────────────────────────


@pytest.fixture
def sample_tool_call() -> ToolCallData:
    return ToolCallData(id="tc-1", name="test_tool", arguments={"key": "value"})


@pytest.fixture
def sample_messages() -> list[Message]:
    return [
        SystemMessage(content="You are a test agent"),
        UserMessage(content="hello"),
    ]


@pytest.fixture
def sample_chunk() -> StreamChunk:
    return StreamChunk(
        content="hello",
        stream_state="streaming",
        stream_id="s1",
        reasoning_content="thinking",
    )


# ── Hook Protocol conformance ────────────────────────


class TestHookProtocol:
    def test_base_hook_satisfies_protocol(self) -> None:
        hook = BaseHook()
        assert isinstance(hook, Hook)

    async def test_base_hook_pre_tool_call_default(
        self, sample_tool_call: ToolCallData
    ) -> None:
        hook = BaseHook()
        result = await hook.pre_tool_call(sample_tool_call)
        assert result == HookAction.CONTINUE

    async def test_base_hook_post_tool_call_default(
        self, sample_tool_call: ToolCallData
    ) -> None:
        hook = BaseHook()
        result = await hook.post_tool_call(sample_tool_call, ToolResult(content="result"))
        assert result is None

    async def test_base_hook_pre_llm_call_default(
        self, sample_messages: list[Message]
    ) -> None:
        hook = BaseHook()
        result = await hook.pre_llm_call(sample_messages, 1)
        assert result is None

    async def test_base_hook_should_continue_default(
        self, sample_messages: list[Message]
    ) -> None:
        hook = BaseHook()
        result = await hook.should_continue(sample_messages, 1)
        assert result is True

    async def test_base_hook_on_stream_chunk_default(
        self, sample_chunk: StreamChunk
    ) -> None:
        hook = BaseHook()
        result = await hook.on_stream_chunk(sample_chunk)
        assert result is None


# ── Custom hook overrides ─────────────────────────────


class SkipHook(BaseHook):
    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        return HookAction.SKIP


class StopHook(BaseHook):
    async def should_continue(self, messages: list[Message], turn: int) -> bool:
        return False


class TestCustomHookOverrides:
    async def test_pre_tool_call_skip(self, sample_tool_call: ToolCallData) -> None:
        hook = SkipHook()
        assert await hook.pre_tool_call(sample_tool_call) == HookAction.SKIP

    async def test_should_continue_false(self, sample_messages: list[Message]) -> None:
        hook = StopHook()
        assert await hook.should_continue(sample_messages, 1) is False


# ── Hook short-circuit helpers ────────────────────────


class TrackingHook(BaseHook):
    """Hook that tracks whether each method was called."""

    def __init__(self) -> None:
        self.pre_tool_call_called = False
        self.post_tool_call_called = False
        self.pre_llm_call_called = False
        self.should_continue_called = False
        self.on_stream_chunk_called = False

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        self.pre_tool_call_called = True
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        self.post_tool_call_called = True

    async def pre_llm_call(self, messages: list[Message], turn: int) -> None:
        self.pre_llm_call_called = True

    async def should_continue(self, messages: list[Message], turn: int) -> bool:
        self.should_continue_called = True
        return True

    async def on_stream_chunk(self, chunk: StreamChunk) -> None:
        self.on_stream_chunk_called = True


class TrackingSkipHook(TrackingHook):
    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        self.pre_tool_call_called = True
        return HookAction.SKIP


class TrackingStopHook(TrackingHook):
    async def should_continue(self, messages: list[Message], turn: int) -> bool:
        self.should_continue_called = True
        return False


class TestHookShortCircuit:
    async def test_pre_tool_call_skip_first(self, sample_tool_call: ToolCallData) -> None:
        """First hook skips -> second hook not called."""
        skip_hook = TrackingSkipHook()
        continue_hook = TrackingHook()
        result = await run_pre_tool_call([skip_hook, continue_hook], sample_tool_call)
        assert result == HookAction.SKIP
        assert skip_hook.pre_tool_call_called is True
        assert continue_hook.pre_tool_call_called is False

    async def test_pre_tool_call_skip_second(self, sample_tool_call: ToolCallData) -> None:
        """First continues, second skips -> returns SKIP."""
        continue_hook = TrackingHook()
        skip_hook = TrackingSkipHook()
        result = await run_pre_tool_call([continue_hook, skip_hook], sample_tool_call)
        assert result == HookAction.SKIP
        assert continue_hook.pre_tool_call_called is True
        assert skip_hook.pre_tool_call_called is True

    async def test_pre_tool_call_all_continue(self, sample_tool_call: ToolCallData) -> None:
        """All hooks continue -> returns CONTINUE."""
        h1 = TrackingHook()
        h2 = TrackingHook()
        result = await run_pre_tool_call([h1, h2], sample_tool_call)
        assert result == HookAction.CONTINUE

    async def test_should_continue_false_first(
        self, sample_messages: list[Message]
    ) -> None:
        """First hook returns False -> second not called."""
        stop_hook = TrackingStopHook()
        continue_hook = TrackingHook()
        result = await run_should_continue([stop_hook, continue_hook], sample_messages, 1)
        assert result is False
        assert stop_hook.should_continue_called is True
        assert continue_hook.should_continue_called is False

    async def test_post_tool_call_calls_all(self, sample_tool_call: ToolCallData) -> None:
        """Observation hook -- both hooks called (no short-circuit)."""
        h1 = TrackingHook()
        h2 = TrackingHook()
        await run_post_tool_call([h1, h2], sample_tool_call, ToolResult(content="result"))
        assert h1.post_tool_call_called is True
        assert h2.post_tool_call_called is True

    async def test_pre_llm_call_calls_all(self, sample_messages: list[Message]) -> None:
        """Observation hook -- both hooks called (no short-circuit)."""
        h1 = TrackingHook()
        h2 = TrackingHook()
        await run_pre_llm_call([h1, h2], sample_messages, 1)
        assert h1.pre_llm_call_called is True
        assert h2.pre_llm_call_called is True

    async def test_on_stream_chunk_calls_all(self, sample_chunk: StreamChunk) -> None:
        """Observation hook -- both hooks called (no short-circuit)."""
        h1 = TrackingHook()
        h2 = TrackingHook()
        await run_on_stream_chunk([h1, h2], sample_chunk)
        assert h1.on_stream_chunk_called is True
        assert h2.on_stream_chunk_called is True


# ── EventEmitterHook ──────────────────────────────────


class TestEventEmitterHook:
    async def test_pre_tool_call_emits_event(
        self, sample_tool_call: ToolCallData
    ) -> None:
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1")
        result = await hook.pre_tool_call(sample_tool_call)
        assert result == HookAction.CONTINUE
        assert not bus.empty
        event = bus.get_nowait()
        assert isinstance(event, ToolCallEvent)
        assert event.source == "agent-1"
        assert event.call_id == sample_tool_call.id
        assert event.tool_name == sample_tool_call.name
        assert event.arguments == sample_tool_call.arguments

    async def test_post_tool_call_emits_event(
        self, sample_tool_call: ToolCallData
    ) -> None:
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1")
        await hook.post_tool_call(
            sample_tool_call,
            ToolResult(
                status="error",
                content="result_data",
                info={"error": "x"},
            ),
        )
        assert not bus.empty
        event = bus.get_nowait()
        assert isinstance(event, ToolResultEvent)
        assert event.source == "agent-1"
        assert event.call_id == sample_tool_call.id
        assert event.tool_name == sample_tool_call.name
        assert event.result == "result_data"
        assert event.status == "error"
        assert event.info == {"error": "x"}

    async def test_on_stream_chunk_emits_thought_event(
        self, sample_chunk: StreamChunk
    ) -> None:
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1")
        await hook.on_stream_chunk(sample_chunk)
        assert bus.pending == 2

        thought = bus.get_nowait()
        assert isinstance(thought, ThoughtEvent)
        assert thought.source == "agent-1"
        assert thought.content == "thinking"
        assert thought.stream_state == "streaming"
        assert thought.stream_id == "s1"
        assert thought.reasoning_content == "thinking"

        response = bus.get_nowait()
        assert isinstance(response, ResponseEvent)
        assert response.source == "agent-1"
        assert response.content == "hello"
        assert response.stream_state == "streaming"
        assert response.stream_id == "s1"

    async def test_on_stream_chunk_emits_response_event_for_content(self) -> None:
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1")
        chunk = StreamChunk(content="answer", stream_state="streaming", stream_id="s1")
        await hook.on_stream_chunk(chunk)
        event = bus.get_nowait()
        assert isinstance(event, ResponseEvent)
        assert event.content == "answer"
        assert event.stream_state == "streaming"
        assert event.stream_id == "s1"

    async def test_on_stream_chunk_emits_thought_event_for_reasoning(self) -> None:
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1")
        chunk = StreamChunk(
            reasoning_content="thinking", stream_state="streaming", stream_id="s1"
        )
        await hook.on_stream_chunk(chunk)
        event = bus.get_nowait()
        assert isinstance(event, ThoughtEvent)
        assert event.content == "thinking"
        assert event.reasoning_content == "thinking"
        assert event.stream_state == "streaming"
        assert event.stream_id == "s1"

    async def test_on_stream_chunk_empty_chunk_emits_nothing(self) -> None:
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1")
        await hook.on_stream_chunk(StreamChunk())
        assert bus.empty


class TestEventEmitterHookSpawnId:
    """Task 2: EventEmitterHook stamps spawn_id on every emitted bus event."""

    _SPAWN = "a1b2c3d4e5f67890"

    async def test_pre_and_post_tool_call_events_carry_spawn_id(
        self, sample_tool_call: ToolCallData
    ) -> None:
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1", spawn_id=self._SPAWN)
        await hook.pre_tool_call(sample_tool_call)
        await hook.post_tool_call(sample_tool_call, ToolResult(content="ok"))
        e1 = bus.get_nowait()
        e2 = bus.get_nowait()
        assert isinstance(e1, ToolCallEvent)
        assert isinstance(e2, ToolResultEvent)
        assert e1.spawn_id == self._SPAWN
        assert e2.spawn_id == self._SPAWN

    async def test_on_stream_chunk_both_branches_carry_spawn_id(
        self, sample_chunk: StreamChunk
    ) -> None:
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1", spawn_id=self._SPAWN)
        await hook.on_stream_chunk(sample_chunk)
        thought = bus.get_nowait()
        response = bus.get_nowait()
        assert isinstance(thought, ThoughtEvent)
        assert isinstance(response, ResponseEvent)
        assert thought.spawn_id == self._SPAWN
        assert response.spawn_id == self._SPAWN

    async def test_on_segment_complete_thought_and_response_carry_spawn_id(self) -> None:
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1", spawn_id=self._SPAWN)
        await hook.on_segment_complete("thought", "t", "sid1")
        await hook.on_segment_complete("response", "r", "sid2")
        t_evt = bus.get_nowait()
        r_evt = bus.get_nowait()
        assert isinstance(t_evt, ThoughtEvent)
        assert isinstance(r_evt, ResponseEvent)
        assert t_evt.spawn_id == self._SPAWN
        assert r_evt.spawn_id == self._SPAWN

    async def test_run_on_segment_complete_propagates_to_emitter(self) -> None:
        bus = MessageBus()
        hook = EventEmitterHook(bus, "agent-1", spawn_id=self._SPAWN)
        await run_on_segment_complete([hook], "thought", "done", "z")
        evt = bus.get_nowait()
        assert isinstance(evt, ThoughtEvent)
        assert evt.spawn_id == self._SPAWN


# ── run_guard_blocked ────────────────────────────────


class TestRunGuardBlocked:
    async def test_calls_all_hooks(self) -> None:
        class RecordingGuardHook(BaseHook):
            def __init__(self) -> None:
                self.calls: list[tuple[str, str | None]] = []

            async def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None:
                self.calls.append((tool_call.name, result.reason))

        h1 = RecordingGuardHook()
        h2 = RecordingGuardHook()
        tc = ToolCallData(id="tc-1", name="dangerous", arguments={})
        gr = GuardResult(allowed=False, reason="forbidden")

        await run_guard_blocked([h1, h2], tc, gr)

        assert len(h1.calls) == 1
        assert h1.calls[0] == ("dangerous", "forbidden")
        assert len(h2.calls) == 1

    async def test_no_hooks_no_error(self) -> None:
        tc = ToolCallData(id="tc-1", name="tool", arguments={})
        gr = GuardResult(allowed=False, reason="blocked")
        await run_guard_blocked([], tc, gr)  # Should not raise

    async def test_base_hook_provides_on_guard_blocked(self) -> None:
        """BaseHook provides all 7 methods including on_guard_blocked.

        After Phase 15, getattr backward compat removed -- all hooks must
        provide on_guard_blocked (BaseHook gives the default no-op).
        """
        tc = ToolCallData(id="tc-1", name="tool", arguments={})
        gr = GuardResult(allowed=False, reason="blocked")
        await run_guard_blocked([BaseHook()], tc, gr)  # Should not raise
