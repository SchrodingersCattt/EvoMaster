"""Tests for llm_factory.build_provider -- config-driven provider construction.

build_provider() is sync and returns an uninitialized provider (no client).
Tests verify route resolution and provider attribute mapping.
Note: api_key is stored as _api_key since the async refactor.
"""

from __future__ import annotations

import pytest

from matmaster.config.llm import LLMConfig, LLMProfileConfig, LLMRouteConfig
from matmaster.providers.llm_factory import build_provider, build_provider_bundle
from matmaster.providers.openai_provider import AnthropicPromptCacheOptions


@pytest.fixture()
def llm_config() -> LLMConfig:
    """LLMConfig with 2 profiles and 2 routes for testing."""
    return LLMConfig(
        profiles={
            "opus": LLMProfileConfig(
                provider="litellm",
                model="claude-opus-4-6",
                context_limit=200_000,
                model_family="claude-4.6",
                api_key="sk-test-opus",
                base_url="http://litellm-proxy",
                thinking_effort="high",
                reasoning_protocol="anthropic_adaptive_thinking",
                temperature_policy="force_one_when_reasoning",
                temperature=0.7,
                prompt_cache={
                    "provider": "anthropic",
                    "system_prompt_breakpoint": True,
                    "automatic": True,
                    "latest_user_breakpoint": True,
                    "tool_result_breakpoint": True,
                    "flexible_breakpoint": True,
                    "max_breakpoints": 4,
                    "min_flexible_chars": 1000,
                    "ttl": "5m",
                },
            ),
            "sonnet": LLMProfileConfig(
                provider="litellm",
                model="claude-sonnet-4-6",
                context_limit=128_000,
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

    def test_prompt_cache_options_passed_for_opus(self, llm_config: LLMConfig) -> None:
        provider = build_provider(llm_config)

        assert provider._prompt_cache_options == AnthropicPromptCacheOptions(
            system_prompt_breakpoint=True,
            cache_control={"type": "ephemeral"},
            automatic=True,
            latest_user_breakpoint=True,
            tool_result_breakpoint=True,
            flexible_breakpoint=True,
            max_breakpoints=4,
            min_flexible_chars=1000,
        )

    def test_prompt_cache_options_absent_for_unconfigured_profile(
        self, llm_config: LLMConfig
    ) -> None:
        provider = build_provider(llm_config, model_override="claude-sonnet-4-6")

        assert provider._prompt_cache_options is None

    def test_prompt_cache_options_passed_for_opus_global(self) -> None:
        config = LLMConfig(
            profiles={
                "opus_global": LLMProfileConfig(
                    provider="litellm",
                    model="global.anthropic.claude-opus-4-6-v1",
                    context_limit=200_000,
                    api_key="sk-test-opus",
                    base_url="http://litellm-proxy",
                    thinking_effort="max",
                    reasoning_protocol="anthropic_adaptive_thinking",
                    temperature_policy="force_one_when_reasoning",
                    prompt_cache={
                        "provider": "anthropic",
                        "system_prompt_breakpoint": True,
                        "automatic": True,
                        "latest_user_breakpoint": True,
                        "tool_result_breakpoint": True,
                        "flexible_breakpoint": True,
                        "max_breakpoints": 4,
                        "min_flexible_chars": 1000,
                        "ttl": "5m",
                    },
                ),
            },
            routes={
                "global.anthropic.claude-opus-4-6-v1": LLMRouteConfig(
                    profile="opus_global"
                )
            },
            default="opus_global",
        )

        provider = build_provider(
            config, model_override="global.anthropic.claude-opus-4-6-v1"
        )

        assert provider._prompt_cache_options == AnthropicPromptCacheOptions(
            system_prompt_breakpoint=True,
            cache_control={"type": "ephemeral"},
            automatic=True,
            latest_user_breakpoint=True,
            tool_result_breakpoint=True,
            flexible_breakpoint=True,
            max_breakpoints=4,
            min_flexible_chars=1000,
        )

    def test_bedrock_provider_does_not_receive_prompt_cache_options(self) -> None:
        config = LLMConfig(
            profiles={
                "opus_bedrock": LLMProfileConfig(
                    provider="bedrock",
                    model="arn:aws:bedrock:us-east-1:123:inference-profile/global.anthropic.claude-opus-4-6-v1",
                    context_limit=200_000,
                    bedrock_region="us-east-1",
                    prompt_cache={
                        "provider": "anthropic",
                        "system_prompt_breakpoint": True,
                        "automatic": True,
                    },
                ),
            },
            routes={"bedrock-claude-opus": LLMRouteConfig(profile="opus_bedrock")},
            default="opus_bedrock",
        )

        provider = build_provider(config)

        assert not hasattr(provider, "_prompt_cache_options")

    def test_route_hit(self, llm_config: LLMConfig) -> None:
        """model_override exact match -> sonnet profile."""
        provider = build_provider(llm_config, model_override="claude-sonnet-4-6")
        assert provider._model == "claude-sonnet-4-6"
        assert provider._client is None

    def test_unknown_route_raises(self, llm_config: LLMConfig) -> None:
        """Unknown model_override -> KeyError."""
        with pytest.raises(KeyError, match="Unknown LLM route key"):
            build_provider(llm_config, model_override="nonexistent-model")

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

    def test_build_provider_bundle_exposes_resolved_model_identity(
        self, llm_config: LLMConfig
    ) -> None:
        """Provider construction and persisted model identity share one route resolution."""
        bundle = build_provider_bundle(
            llm_config,
            model_override="claude-sonnet-4-6",
            llm_override="opus",
        )

        assert bundle.provider._model == "claude-sonnet-4-6"
        assert bundle.model == "claude-sonnet-4-6"
        assert bundle.model_profile == "sonnet"
        assert bundle.model_route == "claude-sonnet-4-6"
        assert bundle.provider_name == "litellm"
        assert bundle.model_family == "claude-4.6"
        assert bundle.context_limit == 128_000
        assert bundle.context_limit_source == "profile"

    def test_stream_timeout_passed(self) -> None:
        """stream_timeout and stream_idle_timeout from profile are passed to provider."""
        config = LLMConfig(
            profiles={
                "opus": LLMProfileConfig(
                    provider="litellm",
                    model="claude-opus-4-6",
                    context_limit=200_000,
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
