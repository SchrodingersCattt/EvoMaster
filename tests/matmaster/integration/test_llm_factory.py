"""Integration tests for LLM factory: route resolution -> provider construction."""

from __future__ import annotations

import pytest

from matmaster.config.llm import (
    LLMConfig,
    LLMProfileConfig,
    _infer_model_family,
)
from matmaster.providers.llm_factory import build_provider


class TestInferModelFamily:
    def test_claude_opus(self) -> None:
        assert _infer_model_family("claude-opus-4-6") == "claude-4.6"

    def test_claude_sonnet(self) -> None:
        assert _infer_model_family("claude-sonnet-4-6") == "claude-4.6"

    def test_claude_haiku(self) -> None:
        assert _infer_model_family("claude-haiku-4-5") == "claude-haiku-4.5"

    def test_gpt5(self) -> None:
        assert _infer_model_family("gpt-5") == "gpt-5"

    def test_deepseek(self) -> None:
        assert _infer_model_family("deepseek-reasoner") == "deepseek-reasoner"

    def test_gemini(self) -> None:
        assert _infer_model_family("gemini-3-flash-preview") == "gemini-3-flash-preview"

    def test_unknown(self) -> None:
        assert _infer_model_family("custom-model") is None

    def test_empty(self) -> None:
        assert _infer_model_family("") is None


class TestProfileEffectiveTemperature:
    def test_force_one_for_claude(self) -> None:
        p = LLMProfileConfig(model_family="claude-4.6", temperature=0.7)
        assert p.effective_temperature() == 1.0

    def test_no_force_for_gpt5(self) -> None:
        p = LLMProfileConfig(model_family="gpt-5", temperature=0.5)
        assert p.effective_temperature() == 0.5


class TestProfileBuildExtraKwargs:
    def test_anthropic_adaptive(self) -> None:
        p = LLMProfileConfig(
            reasoning_protocol="anthropic_adaptive_thinking",
            thinking_effort="high",
        )
        result = p.build_extra_kwargs()
        assert result is not None
        assert result["extra_body"]["thinking"]["type"] == "adaptive"

    def test_openai_reasoning_effort(self) -> None:
        p = LLMProfileConfig(
            reasoning_protocol="openai_reasoning_effort",
            thinking_effort="medium",
        )
        assert p.build_extra_kwargs() == {"reasoning_effort": "medium"}

    def test_no_config_returns_none(self) -> None:
        p = LLMProfileConfig()
        assert p.build_extra_kwargs() is None


class TestEndToEndRouteToProvider:
    @pytest.fixture()
    def config(self) -> LLMConfig:
        return LLMConfig.model_validate(
            {
                "profiles": {
                    "opus": {
                        "provider": "openai",
                        "model": "claude-opus-4-6",
                        "model_family": "claude-4.6",
                        "api_key": "test-key",
                        "base_url": "https://test.example.com",
                        "thinking_effort": "high",
                        "reasoning_protocol": "anthropic_adaptive_thinking",
                        "temperature_policy": "force_one_when_reasoning",
                        "temperature": 0.7,
                        "timeout": 300,
                        "max_retries": 3,
                    },
                    "sonnet": {
                        "provider": "openai",
                        "model": "claude-sonnet-4-6",
                        "model_family": "claude-4.6",
                        "api_key": "test-key",
                        "base_url": "https://test.example.com",
                        "thinking_effort": "high",
                        "reasoning_protocol": "anthropic_adaptive_thinking",
                        "temperature_policy": "force_one_when_reasoning",
                        "temperature": 0.7,
                    },
                },
                "routes": {
                    "claude-opus-4-6": {"profile": "opus"},
                    "claude-sonnet-4-6": {"profile": "sonnet"},
                },
                "default": "opus",
            }
        )

    def test_route_sonnet(self, config: LLMConfig) -> None:
        provider = build_provider(config, model_override="claude-sonnet-4-6")
        assert provider._model == "claude-sonnet-4-6"
        assert provider._temperature == 1.0  # force_one_when_reasoning

    def test_route_claude(self, config: LLMConfig) -> None:
        provider = build_provider(config, model_override="claude-opus-4-6")
        assert provider._model == "claude-opus-4-6"
        assert provider._temperature == 1.0
        assert provider._extra_kwargs is not None

    def test_default_provider(self, config: LLMConfig) -> None:
        provider = build_provider(config)
        assert provider._model == "claude-opus-4-6"

    def test_unknown_route_errors(self, config: LLMConfig) -> None:
        with pytest.raises(KeyError):
            build_provider(config, model_override="nonexistent")

    def test_llm_override_compat(self, config: LLMConfig) -> None:
        provider = build_provider(config, llm_override="sonnet")
        assert provider._model == "claude-sonnet-4-6"

    def test_extra_kwargs_none_becomes_empty(self, config: LLMConfig) -> None:
        cfg = LLMConfig.model_validate(
            {
                "profiles": {
                    "minimal": {
                        "model": "custom",
                        "api_key": "k",
                        "provider": "openai",
                    },
                },
                "default": "minimal",
            }
        )
        provider = build_provider(cfg)
        assert provider._extra_kwargs == {}
