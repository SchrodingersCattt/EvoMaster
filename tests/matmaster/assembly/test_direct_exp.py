"""Tests for DirectExp -- direct execution mode assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from matmaster.assembly.direct_exp import DirectExp
from matmaster.assembly.tool_registry import ToolRegistry
from matmaster.bus.queue import MessageBus
from matmaster.engine.hooks import EventEmitterHook
from matmaster.types.context import PlaygroundContext
from matmaster.types.guards import Guard
from matmaster.types.runtime import AgentRuntimeSpec

from .conftest import MockTool


class MockLLMProvider:
    """Mock LLM provider satisfying the LLMProvider Protocol."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        return None

    def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> Any:
        return self.chat(messages, tools)

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[Any]:
        yield None


def _make_ctx() -> PlaygroundContext:
    return PlaygroundContext(
        workdir=Path("/tmp/test"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
    )


class TestDirectExpAssemble:
    def test_assemble_returns_spec(self) -> None:
        """DirectExp.assemble(ctx) returns AgentRuntimeSpec instance."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        spec = exp.assemble(_make_ctx())
        assert isinstance(spec, AgentRuntimeSpec)

    def test_assemble_spec_has_llm_provider(self) -> None:
        """Returned spec.llm_provider is the provider passed to DirectExp constructor."""
        provider = MockLLMProvider()
        exp = DirectExp(llm_provider=provider)
        spec = exp.assemble(_make_ctx())
        assert spec.llm_provider is provider

    def test_assemble_spec_has_tools(self) -> None:
        """Returned spec.tool_registry contains builtin tools passed to constructor."""
        tool = MockTool(name="my_tool")
        exp = DirectExp(llm_provider=MockLLMProvider(), builtin_tools=[tool])
        spec = exp.assemble(_make_ctx())
        assert spec.tool_registry is not None
        assert isinstance(spec.tool_registry, ToolRegistry)
        assert "my_tool" in spec.tool_registry

    def test_assemble_spec_has_guards(self) -> None:
        """Returned spec.guards contains guards passed to constructor."""
        from matmaster.assembly.guards import ManuscriptGateGuard

        guard = ManuscriptGateGuard()
        exp = DirectExp(llm_provider=MockLLMProvider(), guards=[guard])
        spec = exp.assemble(_make_ctx())
        assert guard in spec.guards

    def test_assemble_spec_has_system_prompt(self) -> None:
        """Returned spec.system_prompt is non-empty string."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        spec = exp.assemble(_make_ctx())
        assert isinstance(spec.system_prompt, str)
        assert len(spec.system_prompt) > 0

    def test_assemble_spec_has_hooks(self) -> None:
        """Returned spec.hooks contains at least one hook (EventEmitterHook)."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        spec = exp.assemble(_make_ctx())
        assert len(spec.hooks) >= 1
        assert any(isinstance(h, EventEmitterHook) for h in spec.hooks)

    def test_assemble_spec_mode_is_direct(self) -> None:
        """Returned spec.mode == 'direct'."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        spec = exp.assemble(_make_ctx())
        assert spec.mode == "direct"

    def test_assemble_max_turns(self) -> None:
        """DirectExp(max_turns=50), assemble() returns spec with max_turns=50."""
        exp = DirectExp(llm_provider=MockLLMProvider(), max_turns=50)
        spec = exp.assemble(_make_ctx())
        assert spec.max_turns == 50

    def test_assemble_repeatable(self) -> None:
        """Call assemble() twice, both return valid specs, they are different objects."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        ctx = _make_ctx()
        spec1 = exp.assemble(ctx)
        spec2 = exp.assemble(ctx)
        assert isinstance(spec1, AgentRuntimeSpec)
        assert isinstance(spec2, AgentRuntimeSpec)
        assert spec1 is not spec2

    def test_assemble_with_message_bus(self) -> None:
        """DirectExp accepts optional bus parameter, assemble uses provided bus."""
        bus = MessageBus()
        exp = DirectExp(llm_provider=MockLLMProvider(), bus=bus)
        spec = exp.assemble(_make_ctx())
        # Verify the EventEmitterHook was created with the provided bus
        emitter = [h for h in spec.hooks if isinstance(h, EventEmitterHook)]
        assert len(emitter) == 1
        assert emitter[0]._bus is bus
