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

# ── Model family defaults ─────────────────────────────────────────────────────

MODEL_FAMILY_DEFAULTS: dict[str, dict[str, str]] = {
    "claude-4.6": {
        "reasoning_protocol": "anthropic_adaptive_thinking",
        "temperature_policy": "force_one_when_reasoning",
    },
    "gpt-5": {
        "reasoning_protocol": "openai_reasoning_effort",
        "temperature_policy": "default",
    },
    "deepseek-reasoner": {
        "reasoning_protocol": "openai_reasoning_effort",
        "temperature_policy": "default",
    },
    "gemini-3-flash-preview": {
        "temperature_policy": "default",
    },
}


def _infer_model_family(model: str) -> str | None:
    """Infer model family from model name string."""
    name = (model or "").strip().lower()
    if "claude-sonnet-4-6" in name or "claude-opus-4-6" in name:
        return "claude-4.6"
    if "claude-haiku-4-5" in name:
        return "claude-haiku-4.5"
    if "gpt-5" in name:
        return "gpt-5"
    if "deepseek-reasoner" in name:
        return "deepseek-reasoner"
    if "gemini-3-flash-preview" in name:
        return "gemini-3-flash-preview"
    return None


# ── Profile config ─────────────────────────────────────────────────────────────


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

    # ── Semantic methods ───────────────────────────────────────────────────

    def effective_family(self) -> str | None:
        """Explicit model_family > infer from model name."""
        return self.model_family or _infer_model_family(self.model)

    def effective_temperature(self) -> float:
        """Apply temperature_policy. claude-4.6 forces temperature=1.0."""
        family = self.effective_family()
        policy = self.temperature_policy or MODEL_FAMILY_DEFAULTS.get(
            family or "", {}
        ).get("temperature_policy")
        if policy == "force_one_when_reasoning":
            return 1.0
        return self.temperature

    def build_extra_kwargs(self) -> dict[str, Any] | None:
        """Build vendor-specific reasoning parameters.
        Returns None when no reasoning parameters are configured.
        OpenAIProvider.__init__ converts None to {} via ``extra_kwargs or {}``.
        """
        family = self.effective_family()
        protocol = self.reasoning_protocol or MODEL_FAMILY_DEFAULTS.get(
            family or "", {}
        ).get("reasoning_protocol")
        effort = (self.thinking_effort or "").strip().lower()
        if not protocol or not effort:
            return None
        if protocol == "anthropic_adaptive_thinking":
            return {
                "extra_body": {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": effort},
                },
            }
        if protocol == "openai_reasoning_effort":
            return {"reasoning_effort": effort}
        return None


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
