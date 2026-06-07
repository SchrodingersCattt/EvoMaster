"""Integration tests for LLM factory: providers/profiles -> transport."""

from __future__ import annotations

import pytest

from matmaster.config.llm import LLMConfig
from matmaster.providers.llm_factory import build_provider, build_provider_bundle
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.messages import UserMessage


@pytest.fixture()
def config() -> LLMConfig:
    return LLMConfig.model_validate(
        {
            "providers": {
                "litellm": {
                    "transport": "chat_completions",
                    "api_key": "test-key",
                    "base_url": "https://test.example.com",
                }
            },
            "profiles": {
                "opus": {
                    "provider": "litellm",
                    "model": "claude-opus-4-6",
                    "reasoning_effort": "high",
                    "reasoning_summary": "auto",
                    "temperature": 0.7,
                    "context_limit": 200_000,
                    "timeout": 300,
                    "max_retries": 3,
                },
                "sonnet": {
                    "provider": "litellm",
                    "model": "claude-sonnet-4-6",
                    "temperature": 0.5,
                    "context_limit": 128_000,
                },
            },
            "default": "opus",
        }
    )


def test_default_profile_builds_chat_completions_transport(
    config: LLMConfig,
) -> None:
    provider = build_provider(config)
    assert isinstance(provider, ChatCompletionsTransport)
    assert provider._model == "claude-opus-4-6"
    assert provider._base_url == "https://test.example.com"
    assert provider._temperature == 0.7
    assert provider._reasoning_effort == "high"
    assert provider._reasoning_summary == "auto"


def test_model_override_is_profile_key(config: LLMConfig) -> None:
    provider = build_provider(config, model_override="sonnet")
    assert isinstance(provider, ChatCompletionsTransport)
    assert provider._model == "claude-sonnet-4-6"
    assert provider._temperature == 0.5
    assert provider._reasoning_effort is None


def test_bundle_identity_uses_profile_key(config: LLMConfig) -> None:
    bundle = build_provider_bundle(config, model_override="sonnet")
    assert bundle.provider._model == "claude-sonnet-4-6"
    assert bundle.model == "claude-sonnet-4-6"
    assert bundle.model_profile == "sonnet"
    assert bundle.model_route == "sonnet"
    assert bundle.provider_name == "litellm"
    assert bundle.context_limit == 128_000
    assert bundle.context_limit_source == "profile"


def test_unknown_profile_errors(config: LLMConfig) -> None:
    with pytest.raises(KeyError, match="not found"):
        build_provider(config, model_override="nonexistent")


def test_reasoning_fields_are_transport_request_concerns(
    config: LLMConfig,
) -> None:
    provider = build_provider(config)
    assert isinstance(provider, ChatCompletionsTransport)

    kwargs = provider.build_kwargs(
        [UserMessage(content="hi")],
        tools=None,
    )
    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["extra_body"]["reasoning"] == {
        "summary": "auto",
        "effort": "high",
    }
