"""llm_factory：dispatch 表驱动 + providers 段解析 + bundle 身份。"""

from __future__ import annotations

import pytest

from matmaster.config.llm import LLMConfig, LLMProfileConfig, ProviderConfig
from matmaster.providers.llm_factory import (
    _TRANSPORT_BUILDERS,
    build_provider,
    build_provider_bundle,
)
from matmaster.providers.transports.anthropic_messages import (
    AnthropicMessagesTransport,
    AnthropicPromptCacheOptions,
)
from matmaster.providers.transports.chat_completions import (
    ChatCompletionsTransport,
    DeepSeekChatCompletionsTransport,
    QwenChatCompletionsTransport,
)
from matmaster.providers.transports.responses import ResponsesTransport


@pytest.fixture()
def llm_config() -> LLMConfig:
    return LLMConfig(
        providers={
            "litellm": ProviderConfig(
                transport="chat_completions",
                api_key="sk-test",
                base_url="http://litellm-proxy",
            )
        },
        profiles={
            "matmaster/qwen3.7-max": LLMProfileConfig(
                provider="litellm",
                model="matmaster/qwen3.7-max",
                reasoning_effort="high",
                context_limit=1_000_000,
                stream_timeout=120.0,
                stream_idle_timeout=60.0,
            ),
            "matmaster/dsk-v4p": LLMProfileConfig(
                provider="litellm",
                model="aliyun/deepseek-v4-pro",
                reasoning_effort="max",
                context_limit=200_000,
            ),
        },
        default="matmaster/qwen3.7-max",
    )


class TestBuildProvider:
    def test_default_path(self, llm_config: LLMConfig) -> None:
        p = build_provider(llm_config)
        assert isinstance(p, ChatCompletionsTransport)
        assert p._model == "matmaster/qwen3.7-max"
        assert p._reasoning_effort == "high"
        assert p._client is None

    def test_model_override_is_profile_key(self, llm_config: LLMConfig) -> None:
        p = build_provider(llm_config, model_override="matmaster/dsk-v4p")
        assert p._model == "aliyun/deepseek-v4-pro"

    def test_unknown_key_raises(self, llm_config: LLMConfig) -> None:
        with pytest.raises(KeyError, match="not found"):
            build_provider(llm_config, model_override="nonexistent")

    def test_custom_default_key(self, llm_config: LLMConfig) -> None:
        p = build_provider(llm_config, default_profile_key="matmaster/dsk-v4p")
        assert p._model == "aliyun/deepseek-v4-pro"

    def test_stream_timeout_passed(self, llm_config: LLMConfig) -> None:
        p = build_provider(llm_config)
        assert p.stream_timeout == 120.0
        assert p.stream_idle_timeout == 60.0

    def test_bundle_identity(self, llm_config: LLMConfig) -> None:
        b = build_provider_bundle(llm_config, model_override="matmaster/dsk-v4p")
        assert b.provider._model == "aliyun/deepseek-v4-pro"
        assert b.model == "aliyun/deepseek-v4-pro"
        assert b.model_profile == "matmaster/dsk-v4p"
        assert b.model_route == "matmaster/dsk-v4p"
        assert b.provider_name == "litellm"
        assert b.context_limit == 200_000
        assert b.context_limit_source == "profile"


class TestDispatch:
    def test_chat_completions_tag_hits_builder(self) -> None:
        assert "chat_completions" in _TRANSPORT_BUILDERS

    def test_unknown_transport_fail_fast(self) -> None:
        cfg = LLMConfig(
            providers={"x": ProviderConfig(transport="ghost_transport", api_key="k")},
            profiles={"p": LLMProfileConfig(provider="x", model="m", context_limit=1)},
            default="p",
        )
        with pytest.raises(ValueError, match="unsupported transport"):
            build_provider(cfg)

    def test_anthropic_messages_tag_hits_builder(self) -> None:
        assert "anthropic_messages" in _TRANSPORT_BUILDERS

    def test_anthropic_messages_builder_receives_profile_and_cache(self) -> None:
        cfg = LLMConfig(
            providers={
                "litellm-anthropic": ProviderConfig(
                    transport="anthropic_messages",
                    api_key="sk-proxy",
                    base_url="https://proxy.example/anthropic",
                    prompt_cache_compat="bedrock_blocks",
                )
            },
            profiles={
                "global.anthropic.claude-opus-4-6-v1": LLMProfileConfig(
                    provider="litellm-anthropic",
                    model="claude-opus-4-6",
                    reasoning_effort="max",
                    context_limit=200_000,
                    supports_vision=True,
                    max_tokens=32_000,
                    timeout=1200,
                    stream_timeout=120,
                    stream_idle_timeout=60,
                    max_retries=3,
                    retry_delay=1.0,
                    prompt_cache={
                        "system_prompt_breakpoint": True,
                        "automatic": True,
                        "latest_user_breakpoint": True,
                        "tool_result_breakpoint": True,
                        "flexible_breakpoint": True,
                        "max_breakpoints": 4,
                        "min_flexible_chars": 1000,
                        "ttl": "5m",
                    },
                )
            },
            default="global.anthropic.claude-opus-4-6-v1",
        )

        provider = build_provider(cfg)

        assert isinstance(provider, AnthropicMessagesTransport)
        assert provider._model == "claude-opus-4-6"
        assert provider._api_key == "sk-proxy"
        assert provider._base_url == "https://proxy.example/anthropic"
        assert provider._reasoning_effort == "max"
        assert provider._max_tokens == 32_000
        assert provider._prompt_cache_compat == "bedrock_blocks"
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

    def test_responses_tag_hits_builder(self) -> None:
        assert "responses" in _TRANSPORT_BUILDERS

    def test_responses_builder_receives_profile(self) -> None:
        cfg = LLMConfig(
            providers={
                "litellm-responses": ProviderConfig(
                    transport="responses",
                    api_key="sk-proxy",
                    base_url="https://proxy.example/v1",
                )
            },
            profiles={
                "matmaster/gpt-5.5": LLMProfileConfig(
                    provider="litellm-responses",
                    model="matmaster/gpt-5.5",
                    reasoning_effort="xhigh",
                    reasoning_summary="detailed",
                    context_limit=256_000,
                    supports_vision=True,
                    timeout=1200,
                    stream_timeout=120,
                    stream_idle_timeout=60,
                    max_retries=3,
                    retry_delay=1.0,
                )
            },
            default="matmaster/gpt-5.5",
        )

        provider = build_provider(cfg)

        assert isinstance(provider, ResponsesTransport)
        assert provider._model == "matmaster/gpt-5.5"
        assert provider._api_key == "sk-proxy"
        assert provider._base_url == "https://proxy.example/v1"
        assert provider._reasoning_effort == "xhigh"
        assert provider._reasoning_summary == "detailed"
        assert provider._max_tokens is None

    def test_responses_builder_rejects_extra_body(self) -> None:
        from matmaster.config.llm import LLMProfileConfig, ProviderConfig
        from matmaster.providers.llm_factory import _build_responses_transport

        with pytest.raises(ValueError, match="does not support extra_body"):
            _build_responses_transport(
                LLMProfileConfig(
                    provider="litellm-responses",
                    model="matmaster/gpt-5.5",
                    context_limit=256_000,
                ),
                ProviderConfig(transport="responses", api_key="k"),
                extra_body={"x": 1},
            )


class TestVendorDispatch:
    def _cfg(self, provider: ProviderConfig) -> LLMConfig:
        return LLMConfig(
            providers={"p": provider},
            profiles={"m": LLMProfileConfig(provider="p", model="m", context_limit=1)},
            default="m",
        )

    def test_qwen_vendor_builds_qwen_transport(self) -> None:
        provider = build_provider(
            self._cfg(
                ProviderConfig(transport="chat_completions", api_key="k", vendor="qwen")
            )
        )
        assert isinstance(provider, QwenChatCompletionsTransport)

    def test_deepseek_vendor_builds_deepseek_transport(self) -> None:
        provider = build_provider(
            self._cfg(
                ProviderConfig(
                    transport="chat_completions", api_key="k", vendor="deepseek"
                )
            )
        )
        assert isinstance(provider, DeepSeekChatCompletionsTransport)

    def test_no_vendor_builds_protocol_base(self) -> None:
        provider = build_provider(
            self._cfg(ProviderConfig(transport="chat_completions", api_key="k"))
        )
        assert type(provider) is ChatCompletionsTransport

    def test_unknown_vendor_fail_fast(self) -> None:
        with pytest.raises(ValueError, match="unsupported vendor"):
            build_provider(
                self._cfg(
                    ProviderConfig(
                        transport="chat_completions", api_key="k", vendor="ghost"
                    )
                )
            )

    def test_vendor_transport_mismatch_fail_fast(self) -> None:
        with pytest.raises(ValueError, match="unsupported vendor"):
            build_provider(
                self._cfg(
                    ProviderConfig(
                        transport="chat_completions", api_key="k", vendor="bedrock"
                    )
                )
            )

    def test_anthropic_vendor_namespace_isolated(self) -> None:
        with pytest.raises(ValueError, match="unsupported vendor"):
            build_provider(
                self._cfg(
                    ProviderConfig(
                        transport="anthropic_messages", api_key="k", vendor="qwen"
                    )
                )
            )

    def test_responses_vendor_namespace_isolated(self) -> None:
        with pytest.raises(ValueError, match="unsupported vendor"):
            build_provider(
                self._cfg(
                    ProviderConfig(transport="responses", api_key="k", vendor="qwen")
                )
            )
