"""Tests for AgentRuntimeSpec and CompactionConfig frozen models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from matmaster.contracts.guards import Guard, GuardContext, GuardResult
from matmaster.contracts.runtime import AgentRuntimeSpec, CompactionConfig


# ── CompactionConfig ────────────────────────────────────


class TestCompactionConfig:
    def test_defaults(self) -> None:
        config = CompactionConfig()
        assert config.enabled is False
        assert config.context_window_tokens == 128_000
        assert config.trigger_ratio == 0.7
        assert config.strategy == "sliding_window"
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


# ── AgentRuntimeSpec ────────────────────────────────────


class _MockGuard:
    """A Guard implementation for testing."""

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)


class TestAgentRuntimeSpec:
    def test_minimal_instantiation(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=object(),
            tool_registry=object(),
        )
        assert spec.llm_provider is not None
        assert spec.tool_registry is not None

    def test_defaults(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=object(),
            tool_registry=object(),
        )
        assert spec.guards == []
        assert spec.max_turns == 100
        assert spec.hooks == []
        assert spec.system_prompt == ""
        assert spec.mode == "direct"
        assert isinstance(spec.compaction, CompactionConfig)

    def test_frozen(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider=object(),
            tool_registry=object(),
        )
        with pytest.raises(ValidationError):
            spec.max_turns = 50

    def test_max_turns_field_exists_and_defaults_to_100(self) -> None:
        """CONT-05: TerminationPolicy simplified to AgentRuntimeSpec.max_turns."""
        spec = AgentRuntimeSpec(
            llm_provider=object(),
            tool_registry=object(),
        )
        assert isinstance(spec.max_turns, int)
        assert spec.max_turns == 100

    def test_with_guard(self) -> None:
        guard = _MockGuard()
        assert isinstance(guard, Guard)  # sanity: verify it satisfies Protocol

        spec = AgentRuntimeSpec(
            llm_provider=object(),
            tool_registry=object(),
            guards=[guard],
        )
        assert len(spec.guards) == 1
        assert spec.guards[0] is guard

    def test_serialization(self) -> None:
        spec = AgentRuntimeSpec(
            llm_provider="mock_llm",
            tool_registry="mock_tools",
            max_turns=50,
            system_prompt="You are a scientist.",
            mode="planner",
        )
        data = spec.model_dump()
        assert data["max_turns"] == 50
        assert data["system_prompt"] == "You are a scientist."
        assert data["mode"] == "planner"
        assert "llm_provider" in data
        assert "tool_registry" in data
        assert "guards" in data
        assert "hooks" in data
        assert "compaction" in data
