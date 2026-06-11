"""LLM provider 配置（纯数据 schema）。"""

from __future__ import annotations

from typing import Literal, NamedTuple

from pydantic import BaseModel, Field, model_validator


class ProviderConfig(BaseModel):
    """一个后端连接：怎么连到 provider。

    vendor 是协议内的请求方言判别（非厂商名）：chat_completions 认 qwen/deepseek，
    anthropic_messages 认 bedrock；None = 协议基本实现。
    """

    transport: str
    api_key: str
    base_url: str | None = None
    vendor: str | None = None


class PromptCacheConfig(BaseModel):
    """Profile-level prompt cache policy consumed by native Anthropic transport."""

    system_prompt_breakpoint: bool = False
    automatic: bool = False
    latest_user_breakpoint: bool = True
    tool_result_breakpoint: bool = False
    flexible_breakpoint: bool = False
    max_breakpoints: int = Field(default=4, ge=1, le=4)
    min_flexible_chars: int = Field(default=1000, ge=1)
    ttl: Literal["5m", "1h"] = "5m"

    def cache_control(self) -> dict[str, str]:
        cc = {"type": "ephemeral"}
        if self.ttl == "1h":
            cc["ttl"] = "1h"
        return cc


class LLMProfileConfig(BaseModel):
    """一个对外可选模型（profile key = 对外标识）。纯数据。"""

    provider: str
    model: str
    reasoning_effort: str | None = None
    reasoning_summary: Literal["auto", "concise", "detailed"] | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    context_limit: int = Field(..., gt=0)
    supports_vision: bool = False
    vision_detail: Literal["low", "high", "auto"] | None = "high"
    timeout: float = 300
    stream_timeout: float | None = None
    stream_idle_timeout: float | None = None
    max_retries: int = 3
    retry_delay: float = 1.0
    prompt_cache: PromptCacheConfig | None = None


class ResolvedModel(NamedTuple):
    """解析结果：键 + 两个源对象引用，零反规范化。"""

    profile_key: str
    profile: LLMProfileConfig
    provider: ProviderConfig


class LLMConfig(BaseModel):
    """顶层：连接池 + 模型表 + 默认。无 routes。"""

    providers: dict[str, ProviderConfig]
    profiles: dict[str, LLMProfileConfig]
    default: str

    @model_validator(mode="after")
    def _check_refs(self) -> LLMConfig:
        if self.default not in self.profiles:
            raise ValueError(
                f"default profile '{self.default}' not found, "
                f"available: {list(self.profiles)}"
            )
        for key, profile in self.profiles.items():
            if profile.provider not in self.providers:
                raise ValueError(
                    f"profile '{key}' references provider '{profile.provider}' "
                    f"which is not declared in providers, "
                    f"available: {list(self.providers)}"
                )
        return self

    def resolve(
        self,
        *,
        model_override: str | None = None,
        default_key: str | None = None,
    ) -> ResolvedModel:
        """对外标识（profile key）到 ResolvedModel。miss 时 fail-fast。"""
        key = model_override or default_key or self.default
        try:
            profile = self.profiles[key]
        except KeyError as exc:
            raise KeyError(
                f"LLM profile '{key}' not found, available: {list(self.profiles)}"
            ) from exc
        return ResolvedModel(
            profile_key=key,
            profile=profile,
            provider=self.providers[profile.provider],
        )
