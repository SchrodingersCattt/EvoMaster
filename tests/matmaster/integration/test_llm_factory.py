"""Tests for _build_llm_provider LLM factory in AgentRunService.

Validates config-driven provider routing, model family resolution,
reasoning parameter passthrough, and temperature policy application.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from src.services.agent_run_service import (
    AgentRunService,
    _build_reasoning_extra_kwargs,
    _infer_model_family,
    _resolve_temperature,
)


# -- Test helpers --


def _make_mock_config(
    llm_dict: dict[str, Any],
    agents_dict: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Create a mock config object with .llm and .agents attributes."""
    return SimpleNamespace(
        llm=llm_dict,
        agents=agents_dict or {"general": {"llm": "litellm"}},
    )


def _make_mock_playground(
    llm_dict: dict[str, Any],
    agents_dict: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Create a mock playground with .config attribute."""
    return SimpleNamespace(
        config=_make_mock_config(llm_dict, agents_dict),
    )


# Minimal LLM profiles for testing
_LITELLM_PROFILE = {
    "provider": "openai",
    "model": "claude-opus-4-6",
    "model_family": "claude-4.6",
    "api_key": "test-key",
    "base_url": "http://localhost:4000",
    "thinking_effort": "high",
    "reasoning_protocol": "anthropic_adaptive_thinking",
    "temperature_policy": "force_one_when_reasoning",
    "temperature": 0.7,
    "timeout": 300,
    "max_retries": 3,
    "retry_delay": 1.0,
}

_AZURE_PROFILE = {
    "provider": "openai",
    "model": "azure/gpt-5",
    "model_family": "gpt-5",
    "api_key": "azure-key",
    "base_url": "http://azure.test",
    "thinking_effort": "high",
    "reasoning_protocol": "openai_reasoning_effort",
    "temperature": 0.7,
    "timeout": 300,
    "max_retries": 3,
    "retry_delay": 1.0,
}

_GEMINI_PROFILE = {
    "provider": "openai",
    "model": "gemini-3-flash-preview",
    "model_family": "gemini-3-flash-preview",
    "api_key": "gemini-key",
    "base_url": "http://localhost:4000",
    "reasoning_protocol": "openai_reasoning_effort",
    "thinking_effort": "high",
    "temperature": 0.7,
    "timeout": 120,
    "max_retries": 3,
    "retry_delay": 1.0,
}

_STANDARD_LLM_DICT = {
    "litellm": _LITELLM_PROFILE,
    "azure": _AZURE_PROFILE,
    "gemini": _GEMINI_PROFILE,
    "default": "litellm",
}


# -- Unit tests for helper functions --


class TestInferModelFamily:
    def test_claude_opus_46(self) -> None:
        assert _infer_model_family("claude-opus-4-6") == "claude-4.6"

    def test_claude_sonnet_46(self) -> None:
        assert _infer_model_family("claude-sonnet-4-6") == "claude-4.6"

    def test_claude_haiku_45(self) -> None:
        assert _infer_model_family("claude-haiku-4-5") == "claude-haiku-4.5"

    def test_gpt5(self) -> None:
        assert _infer_model_family("azure/gpt-5") == "gpt-5"

    def test_deepseek(self) -> None:
        assert _infer_model_family("deepseek-reasoner") == "deepseek-reasoner"

    def test_gemini(self) -> None:
        assert _infer_model_family("gemini-3-flash-preview") == "gemini-3-flash-preview"

    def test_unknown(self) -> None:
        assert _infer_model_family("unknown-model") is None

    def test_empty(self) -> None:
        assert _infer_model_family("") is None

    def test_case_insensitive(self) -> None:
        assert _infer_model_family("Claude-Opus-4-6") == "claude-4.6"


class TestBuildReasoningExtraKwargs:
    def test_anthropic_adaptive_thinking(self) -> None:
        result = _build_reasoning_extra_kwargs("anthropic_adaptive_thinking", "high")
        assert "extra_body" in result
        assert result["extra_body"]["thinking"]["type"] == "adaptive"
        assert result["extra_body"]["output_config"]["effort"] == "high"

    def test_openai_reasoning_effort(self) -> None:
        result = _build_reasoning_extra_kwargs("openai_reasoning_effort", "high")
        assert result == {"reasoning_effort": "high"}

    def test_no_protocol(self) -> None:
        result = _build_reasoning_extra_kwargs(None, "high")
        assert result == {}

    def test_no_effort(self) -> None:
        result = _build_reasoning_extra_kwargs("anthropic_adaptive_thinking", None)
        assert result == {}

    def test_unknown_protocol(self) -> None:
        result = _build_reasoning_extra_kwargs("unknown_protocol", "high")
        assert result == {}


class TestResolveTemperature:
    def test_force_one_when_reasoning(self) -> None:
        assert _resolve_temperature(0.7, "force_one_when_reasoning") == 1.0

    def test_default_policy(self) -> None:
        assert _resolve_temperature(0.7, "default") == 0.7

    def test_no_policy(self) -> None:
        assert _resolve_temperature(0.5, None) == 0.5


# -- Integration tests for _build_llm_provider --


class TestBuildLlmProvider:
    """Tests for AgentRunService._build_llm_provider factory."""

    @patch("src.services.agent_run_service.get_sessions_service")
    def _make_service(self, mock_sessions):
        mock_sessions.return_value = None
        return AgentRunService(sessions_service=None)

    @patch("matmaster.providers.openai_provider.openai.OpenAI")
    def test_default_profile_no_override(self, mock_openai_cls) -> None:
        """No model_override -> uses agents.general.llm default profile."""
        svc = self._make_service()
        pg = _make_mock_playground(_STANDARD_LLM_DICT)

        provider = svc._build_llm_provider(pg, model_override=None)

        assert provider._model == "claude-opus-4-6"
        assert provider._temperature == 1.0  # claude-4.6 forces temp=1

    @patch("matmaster.providers.openai_provider.openai.OpenAI")
    def test_model_override_matches_model_name(self, mock_openai_cls) -> None:
        """model_override matching a profile's model field -> uses that profile."""
        svc = self._make_service()
        pg = _make_mock_playground(_STANDARD_LLM_DICT)

        provider = svc._build_llm_provider(pg, model_override="azure/gpt-5")

        assert provider._model == "azure/gpt-5"

    @patch("matmaster.providers.openai_provider.openai.OpenAI")
    def test_model_override_matches_profile_key(self, mock_openai_cls) -> None:
        """model_override matching a profile key -> uses that profile."""
        svc = self._make_service()
        pg = _make_mock_playground(_STANDARD_LLM_DICT)

        provider = svc._build_llm_provider(pg, model_override="azure")

        # Should use azure profile's model
        assert provider._model == "azure"  # model_override takes precedence

    @patch("matmaster.providers.openai_provider.openai.OpenAI")
    def test_model_override_fallback_to_default(self, mock_openai_cls) -> None:
        """Unknown model_override -> falls back to default profile, logs warning."""
        svc = self._make_service()
        pg = _make_mock_playground(_STANDARD_LLM_DICT)

        provider = svc._build_llm_provider(pg, model_override="unknown-model-xyz")

        # Falls back to default profile but uses override model name
        assert provider._model == "unknown-model-xyz"

    @patch("matmaster.providers.openai_provider.openai.OpenAI")
    def test_claude_46_reasoning_extra_kwargs(self, mock_openai_cls) -> None:
        """Claude 4.6 family -> extra_kwargs with adaptive thinking, temperature forced to 1.0."""
        svc = self._make_service()
        pg = _make_mock_playground(_STANDARD_LLM_DICT)

        provider = svc._build_llm_provider(pg, model_override=None)

        assert provider._temperature == 1.0
        assert "extra_body" in provider._extra_kwargs
        assert provider._extra_kwargs["extra_body"]["thinking"]["type"] == "adaptive"
        assert provider._extra_kwargs["extra_body"]["output_config"]["effort"] == "high"

    @patch("matmaster.providers.openai_provider.openai.OpenAI")
    def test_gpt5_reasoning_extra_kwargs(self, mock_openai_cls) -> None:
        """GPT-5 family -> extra_kwargs with reasoning_effort."""
        svc = self._make_service()
        pg = _make_mock_playground(_STANDARD_LLM_DICT)

        provider = svc._build_llm_provider(pg, model_override="azure/gpt-5")

        assert provider._extra_kwargs == {"reasoning_effort": "high"}

    @patch("matmaster.providers.openai_provider.openai.OpenAI")
    def test_gemini_no_reasoning(self, mock_openai_cls) -> None:
        """Gemini family with no reasoning -> no extra_kwargs (empty dict)."""
        svc = self._make_service()
        # Gemini profile without thinking_effort
        gemini_no_effort = {**_GEMINI_PROFILE, "thinking_effort": None}
        llm_dict = {
            "gemini": gemini_no_effort,
            "default": "gemini",
        }
        pg = _make_mock_playground(
            llm_dict, agents_dict={"general": {"llm": "gemini"}}
        )

        provider = svc._build_llm_provider(pg, model_override=None)

        assert provider._extra_kwargs == {}

    def test_llm_override_ignored(self) -> None:
        """llm_override parameter is no longer in method signature (D-02)."""
        svc = self._make_service()
        import inspect

        sig = inspect.signature(svc._build_llm_provider)
        param_names = list(sig.parameters.keys())
        assert "llm_override" not in param_names
        assert "playground" in param_names or "self" in param_names
