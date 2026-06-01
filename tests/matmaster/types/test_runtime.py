"""Tests for the kernel runtime contracts and CompactionConfig.

Covers the frozen-dataclass runtime trio (AgentKernelSpec / AgentKernelResources
/ AgentKernelRuntime), the AgentRuntime bundle, KernelResult, the pydantic
CompactionConfig, and the matmaster.types re-export surface.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from pydantic import ValidationError

from matmaster.context.sources.turn_input import TurnInput
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.run_metadata import RunIdentity
from matmaster.types.runtime import (
    AgentKernelResources,
    AgentKernelRuntime,
    AgentKernelSpec,
    AgentRuntime,
    CompactionConfig,
    KernelResult,
)
from matmaster.types.runtime_ports import KernelRuntimePorts

# ── Test helpers ───────────────────────────────────────


class _MockLLMProvider:
    """LLMProvider Protocol-conforming mock for runtime resource tests."""

    async def __aenter__(self) -> _MockLLMProvider:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
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


def _make_kernel_spec(**overrides: Any) -> AgentKernelSpec:
    """Build an AgentKernelSpec with sane test defaults."""
    base: dict[str, Any] = {
        "system_prompt": "",
        "max_turns": 100,
        "compaction": CompactionConfig(),
        "run_identity": RunIdentity(),
    }
    base.update(overrides)
    return AgentKernelSpec(**base)


def _make_kernel_resources(**overrides: Any) -> AgentKernelResources:
    """Build an AgentKernelResources with simple stubs for live fields."""
    base: dict[str, Any] = {
        "llm_provider": _MockLLMProvider(),
        "runtime_ports": KernelRuntimePorts(),
        "tool_runner": object(),
        "tool_catalog": object(),
        "runtime_topology": object(),
    }
    base.update(overrides)
    return AgentKernelResources(**base)


# ── CompactionConfig ────────────────────────────────────


class TestCompactionConfig:
    def test_defaults(self) -> None:
        config = CompactionConfig()
        assert "enabled" not in CompactionConfig.model_fields
        assert config.context_limit == 200_000
        assert config.trigger_ratio == 0.9
        assert config.strategy == "summary"
        assert config.reserved_summary_tokens == 8_000
        assert config.summary_safety_margin_tokens == 2_000
        removed_field = "compaction" + "_llm"
        assert not hasattr(config, removed_field)

    def test_frozen(self) -> None:
        config = CompactionConfig()
        with pytest.raises(ValidationError):
            config.context_limit = 64_000

    def test_custom_values(self) -> None:
        config = CompactionConfig(
            context_limit=240_000,
            strategy="summary",
        )
        assert config.context_limit == 240_000
        assert config.strategy == "summary"


class TestCompactionConfigUpdate:
    def test_trigger_ratio_default_09(self) -> None:
        cfg = CompactionConfig()
        assert cfg.trigger_ratio == 0.9

    def test_strategy_default_summary(self) -> None:
        cfg = CompactionConfig()
        assert cfg.strategy == "summary"

    def test_frozen(self) -> None:
        cfg = CompactionConfig()
        with pytest.raises(Exception, match="frozen"):
            cfg.trigger_ratio = 0.8


# ── RunIdentity ─────────────────────────────────────────


def test_run_identity_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RunIdentity(task_id="task-1", run_dir="/tmp/run")


# ── AgentKernelSpec ─────────────────────────────────────


class TestAgentKernelSpec:
    """AgentKernelSpec: frozen config-only dataclass (no live resources)."""

    def test_minimal_instantiation(self) -> None:
        spec = _make_kernel_spec()
        assert spec.system_prompt == ""
        assert spec.max_turns == 100
        assert isinstance(spec.compaction, CompactionConfig)
        assert isinstance(spec.run_identity, RunIdentity)
        assert spec.turn_input is None

    def test_custom_config_values(self) -> None:
        spec = _make_kernel_spec(max_turns=50, system_prompt="You are a scientist.")
        assert spec.max_turns == 50
        assert spec.system_prompt == "You are a scientist."

    def test_typed_run_identity_and_turn_input(self) -> None:
        turn_input = TurnInput.from_values(user_text="hello")
        identity = RunIdentity(
            task_id="task-1", session_id="session-1", spawn_id="child"
        )

        spec = _make_kernel_spec(run_identity=identity, turn_input=turn_input)

        assert spec.run_identity is identity
        assert spec.turn_input is turn_input

    def test_run_identity_is_single_sourced(self) -> None:
        from matmaster.types.run_metadata import RunIdentity as CanonicalRunIdentity

        fields = {f.name: f for f in dataclasses.fields(AgentKernelSpec)}
        assert fields["run_identity"].type in (
            CanonicalRunIdentity,
            "RunIdentity",
        )

    def test_fields(self) -> None:
        names = {f.name for f in dataclasses.fields(AgentKernelSpec)}
        assert names == {
            "system_prompt",
            "max_turns",
            "compaction",
            "run_identity",
            "turn_input",
            "prompt_submit_rewrite_enabled",
            "llm_model",
            "llm_model_profile",
            "llm_model_route",
        }

    def test_frozen_rejects_mutation(self) -> None:
        spec = _make_kernel_spec()
        with pytest.raises(FrozenInstanceError):
            spec.max_turns = 50  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            spec.system_prompt = "changed"  # type: ignore[misc]


# ── AgentKernelResources ────────────────────────────────


class TestAgentKernelResources:
    """AgentKernelResources: frozen live-resources dataclass."""

    def test_minimal_instantiation(self) -> None:
        resources = _make_kernel_resources()
        assert resources.llm_provider is not None
        assert isinstance(resources.runtime_ports, KernelRuntimePorts)
        assert resources.tool_runner is not None
        assert resources.tool_catalog is not None
        assert resources.runtime_topology is not None

    def test_optional_fields_default_none(self) -> None:
        resources = _make_kernel_resources()
        assert resources.hook_executor is None
        assert resources.compactor is None
        assert resources.capability_policy is None
        assert resources.structural_validation is None

    def test_llm_provider_typed_as_protocol(self) -> None:
        provider = _MockLLMProvider()
        assert isinstance(provider, LLMProvider)

        resources = _make_kernel_resources(llm_provider=provider)
        assert resources.llm_provider is provider
        assert isinstance(resources.llm_provider, LLMProvider)

    def test_hook_executor_holds_instance(self) -> None:
        from matmaster.core.hooks import HookExecutor

        executor = HookExecutor()
        resources = _make_kernel_resources(hook_executor=executor)
        assert resources.hook_executor is executor

    def test_compactor_holds_object(self) -> None:
        class FakeCompactor:
            pass

        compactor = FakeCompactor()
        resources = _make_kernel_resources(compactor=compactor)
        assert resources.compactor is compactor

    def test_runtime_ports_holds_instance(self) -> None:
        async def checkpoint_sink(*, payload, base_messages):
            return 12

        ports = KernelRuntimePorts(checkpoint_sink=checkpoint_sink)
        resources = _make_kernel_resources(runtime_ports=ports)
        assert resources.runtime_ports is ports

    def test_runtime_ports_default_has_none_ports(self) -> None:
        resources = _make_kernel_resources()
        assert resources.runtime_ports.checkpoint_sink is None
        assert resources.runtime_ports.pre_compaction_barrier is None

    def test_fields(self) -> None:
        names = {f.name for f in dataclasses.fields(AgentKernelResources)}
        assert names == {
            "llm_provider",
            "runtime_ports",
            "tool_runner",
            "tool_catalog",
            "runtime_topology",
            "hook_executor",
            "compactor",
            "capability_policy",
            "structural_validation",
        }

    def test_frozen_rejects_mutation(self) -> None:
        resources = _make_kernel_resources()
        with pytest.raises(FrozenInstanceError):
            resources.compactor = "new"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            resources.llm_provider = object()  # type: ignore[misc]


# ── AgentKernelRuntime ──────────────────────────────────


class TestAgentKernelRuntime:
    """AgentKernelRuntime: frozen bundle of spec + resources."""

    def test_creation(self) -> None:
        spec = _make_kernel_spec()
        resources = _make_kernel_resources()
        runtime = AgentKernelRuntime(spec=spec, resources=resources)

        assert runtime.spec is spec
        assert runtime.resources is resources

    def test_fields(self) -> None:
        names = {f.name for f in dataclasses.fields(AgentKernelRuntime)}
        assert names == {"spec", "resources"}

    def test_frozen_rejects_mutation(self) -> None:
        runtime = AgentKernelRuntime(
            spec=_make_kernel_spec(), resources=_make_kernel_resources()
        )
        with pytest.raises(FrozenInstanceError):
            runtime.spec = _make_kernel_spec()  # type: ignore[misc]


# ── AgentRuntime ────────────────────────────────────────


class TestAgentRuntime:
    """AgentRuntime frozen dataclass — runtime bundle from Exp.build_runtime()."""

    def _make_kernel_runtime(self) -> AgentKernelRuntime:
        return AgentKernelRuntime(
            spec=_make_kernel_spec(), resources=_make_kernel_resources()
        )

    def test_agent_runtime_creation(self) -> None:
        """AgentRuntime holds kernel, kernel_runtime, and cleanup callable."""
        mock_kernel = object()
        kernel_runtime = self._make_kernel_runtime()
        cleanup_called: list[bool] = []

        def cleanup() -> None:
            cleanup_called.append(True)

        runtime = AgentRuntime(
            kernel=mock_kernel, kernel_runtime=kernel_runtime, cleanup=cleanup
        )

        assert runtime.kernel is mock_kernel
        assert runtime.kernel_runtime is kernel_runtime
        assert runtime.cleanup is cleanup
        # Verify cleanup callable works
        runtime.cleanup()
        assert cleanup_called == [True]

    def test_agent_runtime_fields(self) -> None:
        names = {f.name for f in dataclasses.fields(AgentRuntime)}
        assert names == {"kernel", "kernel_runtime", "cleanup", "context_runtime"}

    def test_agent_runtime_is_frozen(self) -> None:
        """AgentRuntime is frozen — reassignment raises FrozenInstanceError."""
        runtime = AgentRuntime(
            kernel=object(),
            kernel_runtime=self._make_kernel_runtime(),
            cleanup=lambda: None,
        )

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
        assert kr.usage_vendor_by_turn == ()

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


# ── Types re-export from matmaster.types (Phase 32) ───


class TestTypesReExport:
    """Phase 32 types importable from matmaster.types package."""

    def test_topology_types(self) -> None:
        from matmaster.types import RuntimeTopology, SessionCapabilities, ToolPlane

        assert hasattr(ToolPlane, "SESSION_SHELL")
        assert hasattr(SessionCapabilities, "model_fields")
        assert hasattr(RuntimeTopology, "model_fields")

    def test_tool_spec_types(self) -> None:
        from matmaster.types import ResourceClaim, ToolBinding, ToolSpec

        assert hasattr(ToolSpec, "model_fields")
        assert hasattr(ResourceClaim, "model_fields")
        assert hasattr(ToolBinding, "model_fields")

    def test_tool_decision_type(self) -> None:
        from matmaster.types import ToolDecision

        assert hasattr(ToolDecision, "model_fields")
