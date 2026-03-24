"""Tests for LLMConfig.resolve_profile -- three-level profile resolution."""
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
