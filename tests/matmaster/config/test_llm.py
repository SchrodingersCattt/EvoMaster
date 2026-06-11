"""LLMConfig 新 schema：providers/profiles/default + resolve（无 routes）。"""

from __future__ import annotations

import pytest

from matmaster.config.llm import (
    LLMConfig,
    LLMProfileConfig,
    PromptCacheConfig,
    ProviderConfig,
    ResolvedModel,
)


def _cfg() -> LLMConfig:
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
                supports_vision=True,
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


class TestResolve:
    def test_default_path(self) -> None:
        r = _cfg().resolve()
        assert isinstance(r, ResolvedModel)
        assert r.profile_key == "matmaster/qwen3.7-max"
        assert r.profile.model == "matmaster/qwen3.7-max"
        assert r.provider.transport == "chat_completions"

    def test_model_override_is_profile_key(self) -> None:
        r = _cfg().resolve(model_override="matmaster/dsk-v4p")
        assert r.profile_key == "matmaster/dsk-v4p"
        assert r.profile.model == "aliyun/deepseek-v4-pro"

    def test_default_key_used_when_no_override(self) -> None:
        r = _cfg().resolve(default_key="matmaster/dsk-v4p")
        assert r.profile_key == "matmaster/dsk-v4p"

    def test_override_beats_default_key(self) -> None:
        r = _cfg().resolve(
            model_override="matmaster/qwen3.7-max",
            default_key="matmaster/dsk-v4p",
        )
        assert r.profile_key == "matmaster/qwen3.7-max"

    def test_unknown_key_fail_fast(self) -> None:
        with pytest.raises(KeyError, match="not found"):
            _cfg().resolve(model_override="nope")


class TestValidation:
    def test_default_must_exist(self) -> None:
        with pytest.raises(ValueError, match="default profile"):
            LLMConfig(
                providers={
                    "litellm": ProviderConfig(
                        transport="chat_completions",
                        api_key="k",
                    )
                },
                profiles={
                    "a": LLMProfileConfig(
                        provider="litellm",
                        model="m",
                        context_limit=1,
                    )
                },
                default="missing",
            )

    def test_profile_provider_must_be_declared(self) -> None:
        with pytest.raises(ValueError, match="not declared in providers"):
            LLMConfig(
                providers={
                    "litellm": ProviderConfig(
                        transport="chat_completions",
                        api_key="k",
                    )
                },
                profiles={
                    "a": LLMProfileConfig(
                        provider="ghost",
                        model="m",
                        context_limit=1,
                    )
                },
                default="a",
            )


class TestPromptCacheConfig:
    def test_defaults_match_anthropic_native_policy(self) -> None:
        cfg = PromptCacheConfig()

        assert cfg.system_prompt_breakpoint is False
        assert cfg.automatic is False
        assert cfg.latest_user_breakpoint is True
        assert cfg.tool_result_breakpoint is False
        assert cfg.flexible_breakpoint is False
        assert cfg.max_breakpoints == 4
        assert cfg.min_flexible_chars == 1000
        assert cfg.ttl == "5m"
        assert cfg.cache_control() == {"type": "ephemeral"}

    def test_cache_control_includes_one_hour_ttl_only_when_requested(self) -> None:
        cfg = PromptCacheConfig(ttl="1h")

        assert cfg.cache_control() == {"type": "ephemeral", "ttl": "1h"}

    def test_profile_accepts_prompt_cache(self) -> None:
        profile = LLMProfileConfig(
            provider="litellm-anthropic",
            model="claude-opus-4-6",
            context_limit=200_000,
            prompt_cache={
                "system_prompt_breakpoint": True,
                "automatic": True,
                "latest_user_breakpoint": True,
                "tool_result_breakpoint": True,
                "flexible_breakpoint": True,
                "max_breakpoints": 4,
                "min_flexible_chars": 1000,
                "ttl": "1h",
            },
        )

        assert isinstance(profile.prompt_cache, PromptCacheConfig)
        assert profile.prompt_cache.cache_control() == {
            "type": "ephemeral",
            "ttl": "1h",
        }
