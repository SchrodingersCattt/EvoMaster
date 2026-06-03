"""LLM Provider factory: config-driven provider construction.

Thin factory layer: resolve_route -> OpenAIProvider or BedrockProvider.
All semantic resolution (family, temperature, reasoning) lives on
LLMProfileConfig methods. This module only does the final mapping.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from matmaster.config.llm import LLMConfig, LLMProfileConfig
from matmaster.providers.bedrock_provider import BedrockProvider
from matmaster.providers.openai_provider import (
    AnthropicPromptCacheOptions,
    OpenAIProvider,
)

BYOK_PROFILE_KEY = "byok"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMProviderBundle:
    """Provider plus the resolved model identity used for run persistence."""

    provider: OpenAIProvider | BedrockProvider
    model: str
    model_profile: str
    model_route: str | None
    provider_name: str
    model_family: str | None


def build_provider(
    llm_config: LLMConfig,
    *,
    model_override: str | None = None,
    llm_override: str | None = None,
    default_profile_key: str | None = None,
) -> OpenAIProvider | BedrockProvider:
    """Resolve route and build an LLM provider backend.

    Args:
        llm_config: Loaded LLMConfig instance.
        model_override: External route key from frontend.
        llm_override: Legacy profile key (compat layer).
        default_profile_key: Agent-level default profile key.
    """
    return build_provider_bundle(
        llm_config,
        model_override=model_override,
        llm_override=llm_override,
        default_profile_key=default_profile_key,
    ).provider


def _build_anthropic_prompt_cache_options(
    profile,
) -> AnthropicPromptCacheOptions | None:
    prompt_cache = profile.prompt_cache
    if prompt_cache is None or not prompt_cache.system_prompt_breakpoint:
        return None
    return AnthropicPromptCacheOptions(
        system_prompt_breakpoint=prompt_cache.system_prompt_breakpoint,
        cache_control=prompt_cache.cache_control(),
        automatic=prompt_cache.automatic,
        latest_user_breakpoint=prompt_cache.latest_user_breakpoint,
        tool_result_breakpoint=prompt_cache.tool_result_breakpoint,
        flexible_breakpoint=prompt_cache.flexible_breakpoint,
        max_breakpoints=prompt_cache.max_breakpoints,
        min_flexible_chars=prompt_cache.min_flexible_chars,
    )


def _merge_byok_extra_kwargs(base: dict | None, extra_body: dict | None) -> dict | None:
    """把凭证侧的黑盒 extra_body 叠加到族默认 extra_kwargs 上（同名 key 用户优先）。

    SDK 侧 extra_body 与请求体浅合并、且 extra_body 覆盖同名 key，故这里直接覆盖即可。
    """
    if not extra_body:
        return base
    out = dict(base or {})
    merged_body = {**(out.get("extra_body") or {}), **extra_body}
    out["extra_body"] = merged_body
    return out


def build_byok_provider_bundle(
    *,
    model: str,
    api_key: str,
    base_url: str,
    credential_id: str | None = None,
    extra_body: dict | None = None,
) -> LLMProviderBundle:
    """用用户自带 Key（BYOK）构造 OpenAI 兼容 Provider。

    不读 llm_config / routes：model/api_key/base_url 全部来自 tools-server 下发的凭证。
    extra_body 为凭证侧的黑盒透传参数（用户自填 JSON，如 enable_thinking/reasoning_effort/
    thinking_budget 等），原样合并进请求体；与族默认同名 key 时用户优先。内容正确性由用户负责。
    """
    profile = LLMProfileConfig(
        provider="openai",
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    extra_kwargs = _merge_byok_extra_kwargs(profile.build_extra_kwargs(), extra_body)
    logger.info(
        "build_byok_provider: model=%s family=%s base_url_host=%s extra_body_keys=%s",
        model,
        profile.effective_family(),
        (base_url.split("//", 1)[-1].split("/", 1)[0] if base_url else ""),
        sorted((extra_body or {}).keys()),
    )
    provider = OpenAIProvider(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=profile.effective_temperature(),
        max_tokens=profile.max_tokens,
        timeout=profile.timeout,
        stream_timeout=profile.stream_timeout,
        stream_idle_timeout=profile.stream_idle_timeout,
        max_retries=profile.max_retries,
        retry_delay=profile.retry_delay,
        prompt_cache_options=_build_anthropic_prompt_cache_options(profile),
        extra_kwargs=extra_kwargs,
    )
    return LLMProviderBundle(
        provider=provider,
        model=model,
        model_profile=BYOK_PROFILE_KEY,
        model_route=f"byok:{credential_id}" if credential_id else BYOK_PROFILE_KEY,
        provider_name="openai",
        model_family=profile.effective_family(),
    )


def build_provider_bundle(
    llm_config: LLMConfig,
    *,
    model_override: str | None = None,
    llm_override: str | None = None,
    default_profile_key: str | None = None,
) -> LLMProviderBundle:
    """Resolve one LLM route and build both provider and persistence identity."""
    resolved = llm_config.resolve_route(
        model_override=model_override,
        llm_override=llm_override,
        default_key=default_profile_key,
    )
    profile = llm_config.get_profile(resolved.profile_key)

    logger.info(
        "build_provider: route=%s profile=%s model=%s family=%s provider=%s",
        resolved.route_key,
        resolved.profile_key,
        resolved.model,
        profile.effective_family(),
        profile.provider,
    )

    if profile.provider == "bedrock":
        region = (
            (profile.bedrock_region or "").strip()
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        )
        provider = BedrockProvider(
            model_id=resolved.model,
            region=region,
            temperature=profile.effective_temperature(),
            max_tokens=profile.max_tokens,
            timeout=profile.timeout,
            stream_timeout=profile.stream_timeout,
            stream_idle_timeout=profile.stream_idle_timeout,
            max_retries=profile.max_retries,
            retry_delay=profile.retry_delay,
        )
        return LLMProviderBundle(
            provider=provider,
            model=resolved.model,
            model_profile=resolved.profile_key,
            model_route=resolved.route_key,
            provider_name=profile.provider,
            model_family=profile.effective_family(),
        )

    provider = OpenAIProvider(
        model=resolved.model,
        api_key=profile.api_key,
        base_url=profile.base_url,
        temperature=profile.effective_temperature(),
        max_tokens=profile.max_tokens,
        timeout=profile.timeout,
        stream_timeout=profile.stream_timeout,
        stream_idle_timeout=profile.stream_idle_timeout,
        max_retries=profile.max_retries,
        retry_delay=profile.retry_delay,
        prompt_cache_options=_build_anthropic_prompt_cache_options(profile),
        extra_kwargs=profile.build_extra_kwargs(),
    )
    return LLMProviderBundle(
        provider=provider,
        model=resolved.model,
        model_profile=resolved.profile_key,
        model_route=resolved.route_key,
        provider_name=profile.provider,
        model_family=profile.effective_family(),
    )
