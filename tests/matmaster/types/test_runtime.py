"""Tests for AgentRuntimeSpec, CompactionConfig, and AgentRuntime frozen models."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from pydantic import ValidationError

from matmaster.core.hooks import HookExecutor
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.runtime import (
    AgentRuntime,
    AgentRuntimeSpec,
    CompactionConfig,
    KernelResult,
)

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
        with pytest.raises(Exception, match="frozen"):
            cfg.enabled = True


# ── AgentRuntimeSpec ────────────────────────────────────


class TestAgentRuntimeSpec:
    def test_minimal_instantiation(self) -> None:
        provider = _MockLLMProvider()
        spec = AgentRuntimeSpec(
            llm_provider=provider,

        )
        assert spec.llm_provider is not None

    def test_defaults(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),

        )
        assert spec.max_turns == 100
        assert spec.hook_executor is None
        assert spec.system_prompt == ""
        assert isinstance(spec.compaction, CompactionConfig)
        assert "guards" not in AgentRuntimeSpec.model_fields

    def test_frozen(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),

        )
        with pytest.raises(ValidationError):
            spec.max_turns = 50

    def test_max_turns_field_exists_and_defaults_to_100(self) -> None:
        """CONT-05: TerminationPolicy simplified to AgentRuntimeSpec.max_turns."""
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),

        )
        assert isinstance(spec.max_turns, int)
        assert spec.max_turns == 100

    def test_serialization(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),

            max_turns=50,
            system_prompt="You are a scientist.",
        )
        data = spec.model_dump()
        assert data["max_turns"] == 50
        assert data["system_prompt"] == "You are a scientist."
        assert "llm_provider" in data
        assert "guards" not in data
        assert "hook_executor" in data
        assert "compaction" in data

    # ── New typed field tests ──────────────────────────

    def test_llm_provider_typed_as_protocol(self) -> None:
        """llm_provider field accepts LLMProvider Protocol-conforming objects."""
        provider = _MockLLMProvider()
        assert isinstance(provider, LLMProvider)

        spec = AgentRuntimeSpec(
            llm_provider=provider,

        )
        assert isinstance(spec.llm_provider, LLMProvider)

    def test_hook_executor_accepts_executor_instance(self) -> None:
        """hook_executor field accepts HookExecutor instances."""
        executor = HookExecutor()
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
            hook_executor=executor,
        )
        assert isinstance(spec.llm_provider, LLMProvider)
        assert spec.hook_executor is executor


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
        with pytest.raises(Exception, match="frozen"):
            spec.compactor = "new"


# ── Edge case tests (QUAL-01) ─────────────────────────


class TestAgentRuntimeSpecFrozenRejectMutation:
    """QUAL-01: Attempt to modify frozen spec fields -> error."""

    def test_agent_runtime_spec_frozen_reject_mutation(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
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
        assert spec.hook_executor is None
        assert "guards" not in AgentRuntimeSpec.model_fields


class TestAgentRuntimeSpecArbitraryTypes:
    """QUAL-01: LLMProvider accepted as arbitrary type."""

    def test_agent_runtime_spec_arbitrary_types(self) -> None:
        provider = _MockLLMProvider()
        spec = AgentRuntimeSpec(
            llm_provider=provider,
        )
        assert spec.llm_provider is provider
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


# ── KernelResult ───────────────────────────────────────


class TestKernelResult:
    """KernelResult dataclass construction."""

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


# ── Tool Runtime v2 fields (Phase 32-02) ─────────────────


class TestAgentRuntimeSpecToolRuntimeV2Fields:
    """Phase 32-02: 5 new optional fields default to None for backward compat."""

    def test_new_fields_default_none(self) -> None:
        """All 5 new fields default to None when not provided."""
        spec = AgentRuntimeSpec()
        assert spec.tool_runner is None
        assert spec.tool_catalog is None
        assert spec.runtime_topology is None
        assert spec.capability_policy is None
        assert spec.structural_validation is None

    def test_backward_compat_with_existing_constructor(self) -> None:
        """Existing _make_spec() pattern (no new fields) still works."""
        spec = AgentRuntimeSpec(
            llm_provider=_MockLLMProvider(),
            hook_executor=None,
            max_turns=10,
            system_prompt="You are a test agent",
        )
        assert spec.llm_provider is not None
        assert spec.tool_runner is None
        assert spec.tool_catalog is None

    def test_tool_runner_field_accepts_protocol_implementation(self) -> None:
        """tool_runner field accepts a ToolRunner-compatible implementation."""

        class _StubToolRunner:
            async def execute_batch(
                self, tool_calls, ctx, *, on_result=None
            ) -> list[tuple[Any, Any]]:
                return []

        runner = _StubToolRunner()

        spec = AgentRuntimeSpec(tool_runner=runner)

        assert spec.tool_runner is runner

    def test_tool_catalog_field_accepts_catalog(self) -> None:
        """tool_catalog field accepts ToolCatalog instance."""
        from matmaster.tools.tool_catalog import ToolCatalog

        registry = ToolRegistry()
        catalog = ToolCatalog(registry)

        spec = AgentRuntimeSpec(

            tool_catalog=catalog,
        )
        assert spec.tool_catalog is catalog

    def test_runtime_topology_field_accepts_topology(self) -> None:
        """runtime_topology field accepts RuntimeTopology instance."""
        from matmaster.types.topology import RuntimeTopology

        topo = RuntimeTopology(
            session_kind="local",
            control_root="/tmp",
            workspace_root="/tmp/workspace",
        )
        spec = AgentRuntimeSpec(runtime_topology=topo)
        assert spec.runtime_topology is topo
        assert spec.runtime_topology.session_kind == "local"

    def test_model_dump_includes_new_fields(self) -> None:
        """model_dump() output includes the 5 new fields."""
        spec = AgentRuntimeSpec()
        data = spec.model_dump()
        assert "tool_runner" in data
        assert "tool_catalog" in data
        assert "runtime_topology" in data
        assert "capability_policy" in data
        assert "structural_validation" in data
        # All should be None
        assert data["tool_runner"] is None
        assert data["tool_catalog"] is None

    def test_tool_runner_rejects_invalid_type(self) -> None:
        """tool_runner field rejects non-ToolRunner objects at construction."""
        with pytest.raises(ValidationError, match="tool_runner must be ToolRunner"):
            AgentRuntimeSpec(tool_runner=object())

    def test_tool_catalog_rejects_invalid_type(self) -> None:
        """tool_catalog field rejects non-ToolCatalog objects at construction."""
        with pytest.raises(ValidationError, match="tool_catalog must be ToolCatalog"):
            AgentRuntimeSpec(tool_catalog="not a catalog")

    def test_runtime_topology_rejects_invalid_type(self) -> None:
        """runtime_topology field rejects non-RuntimeTopology objects."""
        with pytest.raises(ValidationError, match="runtime_topology must be RuntimeTopology"):
            AgentRuntimeSpec(runtime_topology=42)


# ── Types re-export from matmaster.types (Phase 32) ───


class TestTypesReExport:
    """Phase 32 types importable from matmaster.types package."""

    def test_topology_types(self) -> None:
        from matmaster.types import ToolPlane, SessionCapabilities, RuntimeTopology
        assert hasattr(ToolPlane, "SESSION_SHELL")
        assert hasattr(SessionCapabilities, "model_fields")
        assert hasattr(RuntimeTopology, "model_fields")

    def test_tool_spec_types(self) -> None:
        from matmaster.types import ToolSpec, ResourceClaim, ToolBinding, ToolInstance
        assert hasattr(ToolSpec, "model_fields")
        assert hasattr(ResourceClaim, "model_fields")
        assert hasattr(ToolBinding, "model_fields")

    def test_tool_decision_type(self) -> None:
        from matmaster.types import ToolDecision
        assert hasattr(ToolDecision, "model_fields")
