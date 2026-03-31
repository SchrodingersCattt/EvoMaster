"""Tests for llm_factory.build_provider -- config-driven provider construction.

build_provider() is sync and returns an uninitialized provider (no client).
Tests verify route resolution and provider attribute mapping.
Note: api_key is stored as _api_key since the async refactor.
"""

from __future__ import annotations

import pytest

from matmaster.config.llm import LLMConfig, LLMProfileConfig, LLMRouteConfig
from matmaster.providers.llm_factory import build_provider


@pytest.fixture()
def llm_config() -> LLMConfig:
    """LLMConfig with 2 profiles and 2 routes for testing."""
    return LLMConfig(
        profiles={
            "opus": LLMProfileConfig(
                provider="openai",
                model="claude-opus-4-6",
                model_family="claude-4.6",
                api_key="sk-test-opus",
                base_url="http://litellm-proxy",
                thinking_effort="high",
                reasoning_protocol="anthropic_adaptive_thinking",
                temperature_policy="force_one_when_reasoning",
                temperature=0.7,
            ),
            "sonnet": LLMProfileConfig(
                provider="openai",
                model="claude-sonnet-4-6",
                model_family="claude-4.6",
                api_key="sk-test-sonnet",
                base_url="http://litellm-proxy",
                thinking_effort="high",
                reasoning_protocol="anthropic_adaptive_thinking",
                temperature_policy="force_one_when_reasoning",
                temperature=0.7,
            ),
        },
        routes={
            "claude-opus-4-6": LLMRouteConfig(profile="opus"),
            "claude-sonnet-4-6": LLMRouteConfig(profile="sonnet"),
        },
        default="opus",
    )


class TestBuildProvider:
    """build_provider resolves routes and constructs OpenAIProvider."""

    def test_default_path(self, llm_config: LLMConfig) -> None:
        """No overrides -> default profile, force_one temp, extra_body present."""
        provider = build_provider(llm_config)
        assert provider._model == "claude-opus-4-6"
        assert provider._temperature == 1.0  # force_one_when_reasoning
        assert "extra_body" in provider._extra_kwargs
        assert provider._client is None  # lazy init

    def test_route_hit(self, llm_config: LLMConfig) -> None:
        """model_override exact match -> sonnet profile."""
        provider = build_provider(llm_config, model_override="claude-sonnet-4-6")
        assert provider._model == "claude-sonnet-4-6"
        assert provider._client is None

    def test_unknown_route_raises(self, llm_config: LLMConfig) -> None:
        """Unknown model_override -> KeyError."""
        with pytest.raises(KeyError, match="Unknown LLM route key"):
            build_provider(llm_config, model_override="nonexistent-model")

    def test_llm_override_compat(self, llm_config: LLMConfig) -> None:
        """llm_override (legacy) -> direct profile key lookup."""
        provider = build_provider(llm_config, llm_override="sonnet")
        assert provider._model == "claude-sonnet-4-6"

    def test_custom_default_key(self, llm_config: LLMConfig) -> None:
        """default_profile_key overrides config default."""
        provider = build_provider(llm_config, default_profile_key="sonnet")
        assert provider._model == "claude-sonnet-4-6"

    def test_model_override_precedence(self, llm_config: LLMConfig) -> None:
        """model_override takes precedence over llm_override."""
        provider = build_provider(
            llm_config,
            model_override="claude-sonnet-4-6",
            llm_override="opus",
        )
        assert provider._model == "claude-sonnet-4-6"

    def test_stream_timeout_passed(self) -> None:
        """stream_timeout and stream_idle_timeout from profile are passed to provider."""
        config = LLMConfig(
            profiles={
                "opus": LLMProfileConfig(
                    provider="openai",
                    model="claude-opus-4-6",
                    model_family="claude-4.6",
                    api_key="sk-test-opus",
                    base_url="http://litellm-proxy",
                    thinking_effort="high",
                    reasoning_protocol="anthropic_adaptive_thinking",
                    temperature_policy="force_one_when_reasoning",
                    temperature=0.7,
                    stream_timeout=120.0,
                    stream_idle_timeout=60.0,
                ),
            },
            routes={"claude-opus-4-6": LLMRouteConfig(profile="opus")},
            default="opus",
        )

        provider = build_provider(config)

        assert provider.stream_timeout == 120.0
        assert provider.stream_idle_timeout == 60.0
        assert provider._client is None  # lazy init
