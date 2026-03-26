"""Tests for AgentRuntimeSpec, CompactionConfig, and AgentRuntime frozen models."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, AsyncIterator, Iterator

import pytest
from pydantic import ValidationError

from matmaster.types.guards import Guard, GuardContext, GuardResult
from matmaster.types.runtime import (
    AgentRuntime,
    AgentRuntimeSpec,
    CompactionConfig,
    KernelResult,
    KernelRunResult,
)
from matmaster.types.events import RunResultEvent
from matmaster.core.hooks import BaseHook, Hook
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.tools.tool_registry import ToolRegistry


# ── Test helpers ───────────────────────────────────────


class _MockLLMProvider:
    """LLMProvider Protocol-conforming mock for runtime spec tests."""

    async def __aenter__(self) -> _MockLLMProvider:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content="mock", finish_reason="stop")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="mock", finish_reason="stop")


class _MockGuard:
    """A Guard implementation for testing."""

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)


# ── CompactionConfig ────────────────────────────────────


class TestCompactionConfig:
    def test_defaults(self) -> None:
        config = CompactionConfig()
        assert config.enabled is False
        assert config.context_window_tokens == 128_000
        assert config.trigger_ratio == 0.9
        assert config.strategy == "summary"
        assert config.compaction_llm is None

    def test_frozen(self) -> None:
        config = CompactionConfig()
        with pytest.raises(ValidationError):
            config.enabled = True

    def test_custom_values(self) -> None:
        config = CompactionConfig(
            enabled=True,
            context_window_tokens=200_000,
            strategy="summary",
        )
        assert config.enabled is True
        assert config.context_window_tokens == 200_000
        assert config.strategy == "summary"


class TestCompactionConfigUpdate:
    def test_trigger_ratio_default_09(self) -> None:
        cfg = CompactionConfig()
        assert cfg.trigger_ratio == 0.9

    def test_strategy_default_summary(self) -> None:
        cfg = CompactionConfig()
        assert cfg.strategy == "summary"

    def test_compaction_llm_from_config(self) -> None:
        cfg = CompactionConfig(compaction_llm="compaction")
        assert cfg.compaction_llm == "compaction"

    def test_frozen(self) -> None:
        cfg = CompactionConfig()
        with pytest.raises(Exception):
            cfg.enabled = True


# ── AgentRuntimeSpec ────────────────────────────────────


class TestAgentRuntimeSpec:
    def test_minimal_instantiation(self) -> None:
        provider = _MockLLMProvider()
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            tool_registry=ToolRegistry(),
        )
        assert spec.llm_provider is not None
        assert spec.tool_registry is not None

    def test_defaults(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
            tool_registry=ToolRegistry(),
        )
        assert spec.guards == []
        assert spec.max_turns == 100
        assert spec.hooks == []
        assert spec.system_prompt == ""
        assert isinstance(spec.compaction, CompactionConfig)

    def test_frozen(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
            tool_registry=ToolRegistry(),
        )
        with pytest.raises(ValidationError):
            spec.max_turns = 50

    def test_max_turns_field_exists_and_defaults_to_100(self) -> None:
        """CONT-05: TerminationPolicy simplified to AgentRuntimeSpec.max_turns."""
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
            tool_registry=ToolRegistry(),
        )
        assert isinstance(spec.max_turns, int)
        assert spec.max_turns == 100

    def test_with_guard(self) -> None:
        guard = _MockGuard()
        assert isinstance(guard, Guard)  # sanity: verify it satisfies Protocol

        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
            tool_registry=ToolRegistry(),
            guards=[guard],
        )
        assert len(spec.guards) == 1
        assert spec.guards[0] is guard

    def test_serialization(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
            tool_registry=ToolRegistry(),
            max_turns=50,
            system_prompt="You are a scientist.",
        )
        data = spec.model_dump()
        assert data["max_turns"] == 50
        assert data["system_prompt"] == "You are a scientist."
        assert "llm_provider" in data
        assert "tool_registry" in data
        assert "guards" in data
        assert "hooks" in data
        assert "compaction" in data

    # ── New typed field tests ──────────────────────────

    def test_llm_provider_typed_as_protocol(self) -> None:
        """llm_provider field accepts LLMProvider Protocol-conforming objects."""
        provider = _MockLLMProvider()
        assert isinstance(provider, LLMProvider)

        spec = AgentRuntimeSpec(
            llm_provider=provider,
            tool_registry=ToolRegistry(),
        )
        assert isinstance(spec.llm_provider, LLMProvider)

    def test_hooks_typed_as_hook_protocol(self) -> None:
        """hooks field accepts list of Hook Protocol-conforming objects."""
        hook = BaseHook()
        assert isinstance(hook, Hook)

        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
            tool_registry=ToolRegistry(),
            hooks=[hook],
        )
        assert len(spec.hooks) == 1
        assert all(isinstance(h, Hook) for h in spec.hooks)

    def test_with_mock_provider_and_hooks(self) -> None:
        """AgentRuntimeSpec with MockLLMProvider and BaseHook constructs successfully."""
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
            tool_registry=ToolRegistry(),
            hooks=[BaseHook(), BaseHook()],
            guards=[_MockGuard()],
        )
        assert isinstance(spec.llm_provider, LLMProvider)
        assert len(spec.hooks) == 2
        assert len(spec.guards) == 1


class TestAgentRuntimeSpecCompactor:
    def test_compactor_default_none(self) -> None:
        spec = AgentRuntimeSpec()
        assert spec.compactor is None

    def test_compactor_accepts_object(self) -> None:
        class FakeCompactor:
            pass

        spec = AgentRuntimeSpec(compactor=FakeCompactor())
        assert spec.compactor is not None

    def test_compactor_frozen_reference(self) -> None:
        spec = AgentRuntimeSpec()
        with pytest.raises(Exception):
            spec.compactor = "new"


# ── Edge case tests (QUAL-01) ─────────────────────────


class TestAgentRuntimeSpecFrozenRejectMutation:
    """QUAL-01: Attempt to modify frozen spec fields -> error."""

    def test_agent_runtime_spec_frozen_reject_mutation(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
            tool_registry=ToolRegistry(),
            guards=[_MockGuard()],
        )
        with pytest.raises(ValidationError):
            spec.max_turns = 50
        with pytest.raises(ValidationError):
            spec.system_prompt = "changed"


class TestAgentRuntimeSpecDefaults:
    """QUAL-01: Default values for all optional fields."""

    def test_agent_runtime_spec_defaults(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
        )
        assert spec.max_turns == 100
        assert spec.system_prompt == ""
        assert spec.guards == []
        assert spec.hooks == []
        assert spec.tool_registry is None


class TestAgentRuntimeSpecArbitraryTypes:
    """QUAL-01: LLMProvider and ToolRegistry accepted as arbitrary types."""

    def test_agent_runtime_spec_arbitrary_types(self) -> None:
        provider = _MockLLMProvider()
        registry = ToolRegistry()
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            tool_registry=registry,
        )
        assert spec.llm_provider is provider
        assert spec.tool_registry is registry
        assert isinstance(spec.llm_provider, LLMProvider)


# ── AgentRuntime ────────────────────────────────────────


class TestAgentRuntime:
    """AgentRuntime frozen dataclass — runtime bundle from Exp.build_runtime()."""

    def _make_spec(self) -> AgentRuntimeSpec:
        return AgentRuntimeSpec(llm_provider=_MockLLMProvider())

    def test_agent_runtime_creation(self) -> None:
        """AgentRuntime holds kernel, spec, and cleanup callable."""
        mock_kernel = object()
        spec = self._make_spec()
        cleanup_called: list[bool] = []

        def cleanup() -> None:
            cleanup_called.append(True)

        runtime = AgentRuntime(kernel=mock_kernel, spec=spec, cleanup=cleanup)

        assert runtime.kernel is mock_kernel
        assert runtime.spec is spec
        assert runtime.cleanup is cleanup
        # Verify cleanup callable works
        runtime.cleanup()
        assert cleanup_called == [True]

    def test_agent_runtime_is_frozen(self) -> None:
        """AgentRuntime is frozen — reassignment raises FrozenInstanceError."""
        mock_kernel = object()
        spec = self._make_spec()
        runtime = AgentRuntime(kernel=mock_kernel, spec=spec, cleanup=lambda: None)

        with pytest.raises(FrozenInstanceError):
            runtime.kernel = object()  # type: ignore[misc]


# ── KernelRunResult ────────────────────────────────────


class TestKernelRunResult:
    """KernelRunResult frozen dataclass — return value of AgentKernel.run()."""

    def test_frozen_construction(self) -> None:
        kr = KernelResult(status="completed", reason="natural")
        result = KernelRunResult(result=kr, messages=[])
        assert result.result is kr
        assert result.messages == []

    def test_messages_preserved(self) -> None:
        from matmaster.types.messages import UserMessage, AssistantMessage

        kr = KernelResult(status="completed", reason="natural")
        msgs = [UserMessage(content="hi"), AssistantMessage(content="hello")]
        result = KernelRunResult(result=kr, messages=msgs)
        assert len(result.messages) == 2
        assert result.messages[0].content == "hi"

    def test_frozen_rejects_mutation(self) -> None:
        kr = KernelResult(status="completed", reason="natural")
        result = KernelRunResult(result=kr, messages=[])
        with pytest.raises(FrozenInstanceError):
            result.result = kr  # type: ignore[misc]


# ── KernelResult ───────────────────────────────────────


class TestKernelResult:
    """KernelResult dataclass construction and to_run_result_event()."""

    def test_construction_with_defaults(self) -> None:
        kr = KernelResult(status="completed", reason="natural")
        assert kr.status == "completed"
        assert kr.reason == "natural"
        assert kr.final_content is None
        assert kr.num_turns == 0
        assert kr.stop_reason is None
        assert kr.usage == {}

    def test_construction_with_all_fields(self) -> None:
        kr = KernelResult(
            status="completed",
            reason="natural",
            final_content="hello",
            num_turns=3,
            stop_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )
        assert kr.final_content == "hello"
        assert kr.num_turns == 3
        assert kr.stop_reason == "stop"
        assert kr.usage == {"prompt_tokens": 100, "completion_tokens": 50}

    def test_frozen(self) -> None:
        kr = KernelResult(status="completed", reason="natural")
        with pytest.raises(AttributeError):
            kr.status = "failed"  # type: ignore[misc]

    def test_to_run_result_event(self) -> None:
        kr = KernelResult(
            status="completed",
            reason="natural",
            final_content="done",
            num_turns=5,
            stop_reason="stop",
            usage={"prompt_tokens": 200},
        )
        event = kr.to_run_result_event()
        assert event.source == "agent"
        assert event.status == "completed"
        assert event.reason == "natural"
        assert event.final_content == "done"
        # usage/num_turns/stop_reason should NOT be in RunResultEvent's schema
        assert "num_turns" not in RunResultEvent.model_fields
        assert "usage" not in RunResultEvent.model_fields

    def test_to_run_result_event_custom_source(self) -> None:
        kr = KernelResult(status="cancelled", reason="cancelled")
        event = kr.to_run_result_event(source="worker")
        assert event.source == "worker"
