"""Tests for LLM config: profile defaults, semantic methods, and resolve_profile."""
from __future__ import annotations

import pytest

from matmaster.config.llm import LLMConfig, LLMProfileConfig


class TestLLMProfileConfig:
    """LLMProfileConfig default values match previously hardcoded constants."""

    def test_defaults(self) -> None:
        p = LLMProfileConfig()
        assert p.temperature == 0.7
        assert p.timeout == 300
        assert p.max_retries == 3
        assert p.retry_delay == 1.0
        assert p.provider == "openai"
        assert p.model == ""

    def test_override_from_dict(self) -> None:
        p = LLMProfileConfig(**{"model": "gpt-5", "temperature": 0.3})
        assert p.model == "gpt-5"
        assert p.temperature == 0.3


class TestLLMProfileConfigMethods:
    """Task 1: effective_family, effective_temperature, build_extra_kwargs."""

    # -- effective_family --

    def test_effective_family_explicit(self) -> None:
        p = LLMProfileConfig(model_family="custom-family")
        assert p.effective_family() == "custom-family"

    def test_effective_family_inferred_from_model_sonnet(self) -> None:
        p = LLMProfileConfig(model="claude-sonnet-4-6-20250514")
        assert p.effective_family() == "claude-4.6"

    def test_effective_family_inferred_from_model_opus(self) -> None:
        p = LLMProfileConfig(model="claude-opus-4-6-20250514")
        assert p.effective_family() == "claude-4.6"

    def test_effective_family_inferred_haiku(self) -> None:
        p = LLMProfileConfig(model="claude-haiku-4-5-20250401")
        assert p.effective_family() == "claude-haiku-4.5"

    def test_effective_family_inferred_gpt5(self) -> None:
        p = LLMProfileConfig(model="gpt-5-turbo")
        assert p.effective_family() == "gpt-5"

    def test_effective_family_inferred_deepseek(self) -> None:
        p = LLMProfileConfig(model="deepseek-reasoner-v2")
        assert p.effective_family() == "deepseek-reasoner"

    def test_effective_family_inferred_gemini(self) -> None:
        p = LLMProfileConfig(model="gemini-3-flash-preview-0501")
        assert p.effective_family() == "gemini-3-flash-preview"

    def test_effective_family_unknown_model(self) -> None:
        p = LLMProfileConfig(model="some-unknown-model")
        assert p.effective_family() is None

    def test_effective_family_explicit_overrides_inference(self) -> None:
        p = LLMProfileConfig(model="claude-opus-4-6", model_family="override")
        assert p.effective_family() == "override"

    # -- effective_temperature --

    def test_effective_temperature_default(self) -> None:
        p = LLMProfileConfig(temperature=0.5)
        assert p.effective_temperature() == 0.5

    def test_effective_temperature_force_one_explicit_policy(self) -> None:
        p = LLMProfileConfig(
            temperature=0.5, temperature_policy="force_one_when_reasoning"
        )
        assert p.effective_temperature() == 1.0

    def test_effective_temperature_force_one_from_family_default(self) -> None:
        p = LLMProfileConfig(model="claude-sonnet-4-6-20250514", temperature=0.3)
        assert p.effective_temperature() == 1.0

    def test_effective_temperature_no_force_for_gpt5(self) -> None:
        p = LLMProfileConfig(model="gpt-5-turbo", temperature=0.5)
        assert p.effective_temperature() == 0.5

    def test_effective_temperature_unknown_family(self) -> None:
        p = LLMProfileConfig(model="unknown-model", temperature=0.8)
        assert p.effective_temperature() == 0.8

    # -- build_extra_kwargs --

    def test_build_extra_kwargs_anthropic(self) -> None:
        p = LLMProfileConfig(
            reasoning_protocol="anthropic_adaptive_thinking",
            thinking_effort="high",
        )
        result = p.build_extra_kwargs()
        assert result == {
            "extra_body": {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
            },
        }

    def test_build_extra_kwargs_openai(self) -> None:
        p = LLMProfileConfig(
            reasoning_protocol="openai_reasoning_effort",
            thinking_effort="medium",
        )
        result = p.build_extra_kwargs()
        assert result == {"reasoning_effort": "medium"}

    def test_build_extra_kwargs_from_family_default(self) -> None:
        p = LLMProfileConfig(model="claude-opus-4-6", thinking_effort="low")
        result = p.build_extra_kwargs()
        assert result == {
            "extra_body": {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "low"},
            },
        }

    def test_build_extra_kwargs_no_effort(self) -> None:
        p = LLMProfileConfig(reasoning_protocol="anthropic_adaptive_thinking")
        assert p.build_extra_kwargs() is None

    def test_build_extra_kwargs_no_protocol_no_family(self) -> None:
        p = LLMProfileConfig(model="unknown-model", thinking_effort="high")
        assert p.build_extra_kwargs() is None

    def test_build_extra_kwargs_unknown_protocol(self) -> None:
        p = LLMProfileConfig(
            reasoning_protocol="some_future_protocol", thinking_effort="high"
        )
        assert p.build_extra_kwargs() is None


class TestLLMConfigModelValidator:
    """model_validator separates profile dicts from 'default' key."""

    def test_flat_yaml_dict(self) -> None:
        raw = {
            "litellm": {"provider": "openai", "model": "claude-opus-4-6"},
            "azure": {"provider": "openai", "model": "azure/gpt-5"},
            "default": "litellm",
        }
        cfg = LLMConfig.model_validate(raw)
        assert cfg.default == "litellm"
        assert "litellm" in cfg.profiles
        assert "azure" in cfg.profiles
        assert cfg.profiles["litellm"].model == "claude-opus-4-6"

    def test_already_normalized(self) -> None:
        raw = {
            "profiles": {"p1": {"model": "m1"}},
            "default": "p1",
        }
        cfg = LLMConfig.model_validate(raw)
        assert cfg.profiles["p1"].model == "m1"


class TestResolveProfile:
    """resolve_profile three-level resolution chain."""

    @pytest.fixture()
    def llm_config(self) -> LLMConfig:
        return LLMConfig.model_validate({
            "litellm": {"model": "claude-opus-4-6", "temperature": 0.7},
            "azure": {"model": "azure/gpt-5", "temperature": 0.5},
            "default": "litellm",
        })

    def test_no_override_uses_default(self, llm_config: LLMConfig) -> None:
        key, profile = llm_config.resolve_profile()
        assert key == "litellm"
        assert profile.model == "claude-opus-4-6"

    def test_no_override_with_custom_default_key(self, llm_config: LLMConfig) -> None:
        key, profile = llm_config.resolve_profile(default_key="azure")
        assert key == "azure"
        assert profile.model == "azure/gpt-5"

    def test_override_match_by_model_name(self, llm_config: LLMConfig) -> None:
        key, profile = llm_config.resolve_profile(model_override="azure/gpt-5")
        assert key == "azure"
        assert profile.temperature == 0.5

    def test_override_match_by_profile_key(self, llm_config: LLMConfig) -> None:
        key, profile = llm_config.resolve_profile(model_override="azure")
        assert key == "azure"

    def test_override_fallback_to_default(self, llm_config: LLMConfig) -> None:
        key, profile = llm_config.resolve_profile(model_override="unknown-model")
        assert key == "litellm"

    def test_invalid_default_key_raises(self, llm_config: LLMConfig) -> None:
        with pytest.raises(KeyError):
            llm_config.resolve_profile(default_key="nonexistent")
