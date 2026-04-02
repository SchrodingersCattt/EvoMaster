"""Tests for AgentKernel _run_items() / run_stream() / _resolve_tool_definitions().

Phase 32 Plan 03: generator-first kernel architecture.
Tests the three-layer interface: _run_items() -> run_stream() -> run().
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from typing import Any

import pytest

from matmaster.types.events import (
    ResponseEvent,
    RunResultEvent,
    ThoughtEvent,
)
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
    ToolCallData,
)
from matmaster.types.runtime import AgentRuntimeSpec, KernelResult

from .agent_kernel_test_helpers import (
    StreamingProvider,
    ToolCallingProvider,
    _make_spec,
    _make_tool_registry,
)
from .conftest import MockLLMProvider


# ---------------------------------------------------------------------------
# run_stream() tests
# ---------------------------------------------------------------------------


class TestRunStreamNaturalFinish:
    """run_stream() yields ResponseEvent + RunResultEvent on natural finish."""

    @pytest.mark.asyncio
    async def test_run_stream_natural_finish(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider(
            [
                StreamChunk(content="Hello world"),
                StreamChunk(finish_reason="stop"),
            ]
        )
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task"):
            events.append(event)

        # Should have at least a ResponseEvent and RunResultEvent
        assert len(events) >= 2
        # Last event must be RunResultEvent
        assert isinstance(events[-1], RunResultEvent)
        assert events[-1].status == "completed"
        assert events[-1].reason == "natural"
        # Should have ResponseEvent with content
        response_events = [e for e in events if isinstance(e, ResponseEvent)]
        assert len(response_events) >= 1
        assert response_events[0].content == "Hello world"
        assert response_events[0].stream_state == "complete"


class TestRunStreamWithTools:
    """run_stream() yields correct event sequence for tool-calling turns."""

    @pytest.mark.asyncio
    async def test_run_stream_with_tools(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="test_tool", arguments={"x": 1})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content="done"
        )
        spec = _make_spec(provider=provider, max_turns=5)
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task"):
            events.append(event)

        # Last event must be RunResultEvent
        assert isinstance(events[-1], RunResultEvent)
        assert events[-1].status == "completed"
        assert events[-1].reason == "natural"
        # Should have ResponseEvent for final content
        response_events = [e for e in events if isinstance(e, ResponseEvent)]
        assert len(response_events) >= 1
        assert any(e.content == "done" for e in response_events)


class TestRunStreamMaxTurns:
    """run_stream() yields RunResultEvent(reason='max_turns') on max_turns."""

    @pytest.mark.asyncio
    async def test_run_stream_max_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="some_tool", arguments={"x": 1})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=999)
        spec = _make_spec(provider=provider, max_turns=2)
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task"):
            events.append(event)

        assert isinstance(events[-1], RunResultEvent)
        assert events[-1].reason == "max_turns"


class TestRunStreamCancelled:
    """run_stream() yields RunResultEvent(reason='cancelled') on stop_event."""

    @pytest.mark.asyncio
    async def test_run_stream_cancelled(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()
        stop_event.set()
        spec = _make_spec()
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "test task", stop_event=stop_event):
            events.append(event)

        assert isinstance(events[-1], RunResultEvent)
        assert events[-1].reason == "cancelled"


class TestRunStreamEndsWithRunResult:
    """The last yield of run_stream() is always RunResultEvent."""

    @pytest.mark.asyncio
    async def test_run_stream_ends_with_run_result(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider(
            [
                StreamChunk(content="final"),
                StreamChunk(finish_reason="stop"),
            ]
        )
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "task"):
            events.append(event)

        assert len(events) > 0
        assert isinstance(events[-1], RunResultEvent)


class TestRunStreamThoughtEvent:
    """run_stream() yields ThoughtEvent when LLM has reasoning_content."""

    @pytest.mark.asyncio
    async def test_run_stream_thought_event(self) -> None:
        from matmaster.core.agent import AgentKernel

        # Provider that produces reasoning_content then content
        provider = StreamingProvider(
            [
                StreamChunk(reasoning_content="Let me think..."),
                StreamChunk(content="The answer is 42"),
                StreamChunk(finish_reason="stop"),
            ]
        )
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()

        events: list[Any] = []
        async for event in kernel.run_stream(spec, "think task"):
            events.append(event)

        # Should have ThoughtEvent
        thought_events = [e for e in events if isinstance(e, ThoughtEvent)]
        assert len(thought_events) >= 1
        assert thought_events[0].content == "Let me think..."
        assert thought_events[0].stream_state == "complete"
        # Should also have ResponseEvent and RunResultEvent
        assert isinstance(events[-1], RunResultEvent)


# ---------------------------------------------------------------------------
# run() delegation test
# ---------------------------------------------------------------------------


class TestRunDelegatesToRunItems:
    """run() returns the same result as the old _run_loop()."""

    @pytest.mark.asyncio
    async def test_run_delegates_to_run_items(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider(
            [
                StreamChunk(content="Hello"),
                StreamChunk(finish_reason="stop"),
            ]
        )
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = await kernel.run(spec, "test task")

        assert isinstance(result.result, KernelResult)
        assert result.result.reason == "natural"
        assert result.result.final_content == "Hello"
        # Messages should include system, user, and assistant
        assert len(result.messages) >= 3


# ---------------------------------------------------------------------------
# _resolve_tool_definitions() tests
# ---------------------------------------------------------------------------


class TestResolveToolDefinitionsRegistryPath:
    """Phase 1 fallback: uses registry.get_tool_definitions()."""

    @pytest.mark.asyncio
    async def test_resolve_tool_definitions_registry_path(self) -> None:
        from matmaster.core.agent import _KernelState, _resolve_tool_definitions

        registry, _ = _make_tool_registry(tool_names=["test_tool"])
        spec = _make_spec(tool_registry=registry)
        state = _KernelState(messages=[])

        defs = _resolve_tool_definitions(spec, state)
        assert defs is not None
        assert len(defs) >= 1
        assert any(d["function"]["name"] == "test_tool" for d in defs)


class TestResolveToolDefinitionsCatalogPath:
    """Phase 2 path: uses tool_catalog with version caching."""

    @pytest.mark.asyncio
    async def test_resolve_tool_definitions_catalog_path(self) -> None:
        from matmaster.core.agent import _KernelState, _resolve_tool_definitions
        from matmaster.tools.tool_catalog import ToolCatalog

        registry, _ = _make_tool_registry(tool_names=["my_tool"])
        catalog = ToolCatalog(registry)
        spec = _make_spec(tool_registry=registry)
        # Create a new spec with tool_catalog set
        spec_with_catalog = spec.model_copy(update={"tool_catalog": catalog})
        state = _KernelState(messages=[])

        # First call should populate cache
        defs1 = _resolve_tool_definitions(spec_with_catalog, state)
        assert defs1 is not None
        assert state.last_catalog_version == 0
        assert state.cached_tool_definitions is defs1

        # Second call with same version should return cached
        defs2 = _resolve_tool_definitions(spec_with_catalog, state)
        assert defs2 is defs1  # same object = cached


# ---------------------------------------------------------------------------
# _KernelState locality test
# ---------------------------------------------------------------------------


class TestKernelStateIsLocal:
    """_KernelState is not stored on self -- kernel has no state attribute."""

    @pytest.mark.asyncio
    async def test_kernel_state_is_local(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider(
            [
                StreamChunk(content="hello"),
                StreamChunk(finish_reason="stop"),
            ]
        )
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        await kernel.run(spec, "task")

        # Kernel should not hold any _state, state, _kernel_state attribute
        assert not hasattr(kernel, "_state")
        assert not hasattr(kernel, "state")
        assert not hasattr(kernel, "_kernel_state")


# ---------------------------------------------------------------------------
# Tool runner fallback test
# ---------------------------------------------------------------------------


class TestToolRunnerFallback:
    """spec.tool_runner=None falls back to InlineToolRunner."""

    @pytest.mark.asyncio
    async def test_tool_runner_fallback(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id="tc-1", name="test_tool", arguments={"x": 1})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content="done"
        )
        spec = _make_spec(provider=provider, max_turns=5)
        assert spec.tool_runner is None  # No tool_runner set
        kernel = AgentKernel()
        result = await kernel.run(spec, "task")

        # Should complete successfully using InlineToolRunner fallback
        assert result.result.status == "completed"
        assert result.result.reason == "natural"
