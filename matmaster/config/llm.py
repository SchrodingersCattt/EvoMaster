"""LLM provider profile configurations.

Maps to the ``llm`` section in config.yaml. Each profile defines a single
LLM backend (provider, model, auth, reasoning, timeout, retry).

YAML example::

    llm:
      litellm:
        provider: "openai"
        model: "claude-opus-4-6"
        model_family: "claude-4.6"
        api_key: "${LITELLM_PROXY_API_KEY}"
        base_url: "${LITELLM_PROXY_API_BASE}"
        thinking_effort: "high"
        reasoning_protocol: "anthropic_adaptive_thinking"
        ...
      default: "litellm"
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class LLMProfileConfig(BaseModel):
    """Single LLM provider profile."""

    provider: str = "openai"
    model: str = ""
    model_family: str | None = None

    # Auth
    api_key: str = ""
    base_url: str | None = None
    api_version: str | None = None  # Azure only

    # Reasoning
    thinking_effort: str | None = None
    reasoning_protocol: str | None = None  # "anthropic_adaptive_thinking" | "openai_reasoning_effort"
    fallback_group: str | None = None

    # Temperature
    temperature_policy: str | None = None  # "force_one_when_reasoning" | "default"
    temperature: float = 0.7

    # Limits
    max_tokens: int | None = None

    # Timeout (seconds)
    timeout: float = 300
    stream_timeout: float | None = None
    stream_idle_timeout: float | None = None

    # Retry
    max_retries: int = 3
    retry_delay: float = 1.0


class LLMConfig(BaseModel):
    """Top-level LLM configuration: named profiles + default selection.

    The YAML ``llm`` block mixes profile dicts with a scalar ``default``
    key at the same level.  The ``model_validator`` separates them into
    ``profiles`` dict and ``default`` string so downstream code has
    typed access via ``config.llm.profiles["litellm"]``.
    """

    profiles: dict[str, LLMProfileConfig] = Field(default_factory=dict)
    default: str = "litellm"

    @model_validator(mode="before")
    @classmethod
    def _separate_profiles_from_default(cls, data: Any) -> Any:
        """Extract profile dicts and ``default`` from a flat YAML dict."""
        if not isinstance(data, dict):
            return data
        # Already in normalized form
        if "profiles" in data:
            return data
        default = data.pop("default", "litellm")
        profiles: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                profiles[key] = value
        return {"profiles": profiles, "default": default}

    def get_profile(self, key: str | None = None) -> LLMProfileConfig:
        """Return profile by key, falling back to ``self.default``."""
        k = key or self.default
        if k not in self.profiles:
            raise KeyError(f"LLM profile '{k}' not found, available: {list(self.profiles)}")
        return self.profiles[k]

    def resolve_profile(
        self,
        model_override: str | None = None,
        default_key: str | None = None,
    ) -> tuple[str, LLMProfileConfig]:
        """Three-level profile resolution chain.

        When *model_override* is ``None``, return the profile at *default_key*
        (falls back to ``self.default``).

        When *model_override* is set:
          1. Search profiles whose ``model`` field matches *model_override*.
          2. Check if *model_override* is itself a profile key.
          3. Fall back to the *default_key* profile.

        Args:
            model_override: Model name or profile key to resolve.
            default_key: Agent-level default (e.g. ``agents.general.llm``).
                Falls back to ``self.default`` when ``None``.

        Returns:
            ``(profile_key, LLMProfileConfig)`` tuple.

        Raises:
            KeyError: When the resolved key is not found in profiles.
        """
        effective_default = default_key or self.default
        if effective_default not in self.profiles:
            raise KeyError(
                f"LLM profile '{effective_default}' not found, "
                f"available: {list(self.profiles)}"
            )

        if not model_override:
            return effective_default, self.profiles[effective_default]

        # 1. Search by model name
        for key, profile in self.profiles.items():
            if profile.model == model_override:
                return key, profile

        # 2. Search by profile key
        if model_override in self.profiles:
            return model_override, self.profiles[model_override]

        # 3. Fallback
        return effective_default, self.profiles[effective_default]
