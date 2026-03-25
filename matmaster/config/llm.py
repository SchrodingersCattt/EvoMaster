"""LLM provider profile configurations.

Maps to the ``llm`` section in config.yaml. Each profile defines a single
LLM backend (provider, model, auth, reasoning, timeout, retry).

YAML example::

    llm:
      opus:
        provider: "openai"
        model: "claude-opus-4-6"
        api_key: "${LITELLM_PROXY_API_KEY}"
        base_url: "${LITELLM_PROXY_API_BASE}"
        thinking_effort: "high"
        reasoning_protocol: "anthropic_adaptive_thinking"
        ...
      default: "opus"
"""

from __future__ import annotations

from dataclasses import dataclass
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
    "gemini-3.1-pro": {
        "reasoning_protocol": "openai_reasoning_effort",
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
    if "gemini-3.1-pro" in name:
        return "gemini-3.1-pro"
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


# ── Route schema ───────────────────────────────────────────────────────────────


class LLMRouteConfig(BaseModel):
    """External route key -> internal profile mapping."""

    profile: str
    model: str | None = None


@dataclass(frozen=True)
class ResolvedLLMRoute:
    """Runtime-resolved LLM routing result."""

    route_key: str | None
    profile_key: str
    provider: str
    model: str


# ── Top-level LLM config ──────────────────────────────────────────────────────


class LLMConfig(BaseModel):
    """Top-level LLM configuration: profiles + routes + default."""

    profiles: dict[str, LLMProfileConfig] = Field(default_factory=dict)
    routes: dict[str, LLMRouteConfig] = Field(default_factory=dict)
    default: str = "opus"

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_or_explicit_schema(cls, data: Any) -> Any:
        """Support both normalized and legacy flat YAML formats."""
        if not isinstance(data, dict):
            return data
        if "profiles" in data:
            return data
        default = data.pop("default", "opus")
        profiles: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                profiles[key] = value
        return {"profiles": profiles, "default": default}

    @model_validator(mode="after")
    def _validate_internal_references(self) -> "LLMConfig":
        """Fail-fast: verify all internal references are valid."""
        if self.default not in self.profiles:
            raise ValueError(
                f"default profile '{self.default}' not found, "
                f"available: {list(self.profiles)}"
            )
        for route_key, route in self.routes.items():
            if route.profile not in self.profiles:
                raise ValueError(
                    f"route '{route_key}' references profile '{route.profile}' "
                    f"which does not exist, available: {list(self.profiles)}"
                )
        return self

    def get_profile(self, key: str | None = None) -> LLMProfileConfig:
        """Return profile by key, falling back to self.default."""
        k = key or self.default
        if k not in self.profiles:
            raise KeyError(f"LLM profile '{k}' not found, available: {list(self.profiles)}")
        return self.profiles[k]

    def resolve_route(
        self,
        *,
        model_override: str | None = None,
        llm_override: str | None = None,
        default_key: str | None = None,
    ) -> ResolvedLLMRoute:
        """Resolve external input to internal profile + model.
        Resolution order:
        1. model_override non-empty: exact route table lookup (fail on miss)
        2. llm_override non-empty: treat as profile key (compat layer)
        3. Both empty: use default_key or self.default
        """
        effective_default = default_key or self.default
        if model_override:
            route = self.routes.get(model_override)
            if route is None:
                raise KeyError(
                    f"Unknown LLM route key: {model_override!r}, "
                    f"available routes: {list(self.routes)}"
                )
            profile = self.get_profile(route.profile)
            return ResolvedLLMRoute(
                route_key=model_override,
                profile_key=route.profile,
                provider=profile.provider,
                model=route.model or profile.model,
            )
        profile_key = llm_override or effective_default
        profile = self.get_profile(profile_key)
        return ResolvedLLMRoute(
            route_key=None,
            profile_key=profile_key,
            provider=profile.provider,
            model=profile.model,
        )

    # Keep resolve_profile for backward compatibility during migration
    def resolve_profile(
        self,
        model_override: str | None = None,
        default_key: str | None = None,
    ) -> tuple[str, LLMProfileConfig]:
        """Legacy three-level profile resolution (kept for compat)."""
        effective_default = default_key or self.default
        if effective_default not in self.profiles:
            raise KeyError(
                f"LLM profile '{effective_default}' not found, "
                f"available: {list(self.profiles)}"
            )
        if not model_override:
            return effective_default, self.profiles[effective_default]
        for key, profile in self.profiles.items():
            if profile.model == model_override:
                return key, profile
        if model_override in self.profiles:
            return model_override, self.profiles[model_override]
        return effective_default, self.profiles[effective_default]
