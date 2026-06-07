"""LLM provider factory：dispatch 表驱动构造。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Literal

from matmaster.config.llm import (
    LLMConfig,
    LLMProfileConfig,
    ProviderConfig,
    ResolvedModel,
)
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.llm_provider import LLMProvider

BYOK_PROFILE_KEY = "byok"
BYOK_DEFAULT_CONTEXT_LIMIT = 200_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMProviderBundle:
    """Provider 加上用于 run 持久化的解析模型身份。"""

    provider: LLMProvider
    model: str
    model_profile: str
    model_route: str | None
    provider_name: str
    context_limit: int
    context_limit_source: Literal["profile", "byok_credential", "byok_default"]


def _build_chat_completions_transport(
    profile: LLMProfileConfig,
    provider: ProviderConfig,
    *,
    extra_body: dict | None = None,
) -> ChatCompletionsTransport:
    """profile 平铺字段 + provider 连接到 ChatCompletionsTransport。"""
    return ChatCompletionsTransport(
        model=profile.model,
        api_key=provider.api_key,
        base_url=provider.base_url,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        reasoning_effort=profile.reasoning_effort,
        reasoning_summary=profile.reasoning_summary,
        extra_body=extra_body,
        timeout=profile.timeout,
        stream_timeout=profile.stream_timeout,
        stream_idle_timeout=profile.stream_idle_timeout,
        max_retries=profile.max_retries,
        retry_delay=profile.retry_delay,
    )


_TRANSPORT_BUILDERS: dict[
    str, Callable[[LLMProfileConfig, ProviderConfig], LLMProvider]
] = {
    "chat_completions": _build_chat_completions_transport,
}


def _dispatch(profile: LLMProfileConfig, provider: ProviderConfig) -> LLMProvider:
    try:
        builder = _TRANSPORT_BUILDERS[provider.transport]
    except KeyError as exc:
        raise ValueError(
            f"unsupported transport: {provider.transport!r}, "
            f"available: {list(_TRANSPORT_BUILDERS)}"
        ) from exc
    return builder(profile, provider)


def build_provider(
    llm_config: LLMConfig,
    *,
    model_override: str | None = None,
    default_profile_key: str | None = None,
) -> LLMProvider:
    """解析并构造 LLM provider 后端。"""
    return build_provider_bundle(
        llm_config,
        model_override=model_override,
        default_profile_key=default_profile_key,
    ).provider


def build_provider_bundle(
    llm_config: LLMConfig,
    *,
    model_override: str | None = None,
    default_profile_key: str | None = None,
) -> LLMProviderBundle:
    """解析一个 profile 并同时构造 provider 与持久化身份。"""
    resolved: ResolvedModel = llm_config.resolve(
        model_override=model_override,
        default_key=default_profile_key,
    )
    logger.info(
        "build_provider: profile=%s model=%s transport=%s provider=%s",
        resolved.profile_key,
        resolved.profile.model,
        resolved.provider.transport,
        resolved.profile.provider,
    )
    provider = _dispatch(resolved.profile, resolved.provider)
    return LLMProviderBundle(
        provider=provider,
        model=resolved.profile.model,
        model_profile=resolved.profile_key,
        model_route=resolved.profile_key,
        provider_name=resolved.profile.provider,
        context_limit=resolved.profile.context_limit,
        context_limit_source="profile",
    )


def build_byok_provider_bundle(
    *,
    model: str,
    api_key: str,
    base_url: str,
    credential_id: str | None = None,
    context_limit: int | None = None,
    extra_body: dict | None = None,
) -> LLMProviderBundle:
    """用用户自带 Key（BYOK）构造 OpenAI 兼容 transport。"""
    if context_limit is not None and context_limit <= 0:
        raise ValueError("BYOK context_limit must be a positive integer")
    effective_context_limit = context_limit or BYOK_DEFAULT_CONTEXT_LIMIT
    context_limit_source = (
        "byok_credential" if context_limit is not None else "byok_default"
    )
    profile = LLMProfileConfig(
        provider="byok",
        model=model,
        context_limit=effective_context_limit,
    )
    provider_conn = ProviderConfig(
        transport="chat_completions",
        api_key=api_key,
        base_url=base_url,
    )
    logger.info(
        "build_byok_provider: model=%s base_url_host=%s extra_body_keys=%s",
        model,
        (base_url.split("//", 1)[-1].split("/", 1)[0] if base_url else ""),
        sorted((extra_body or {}).keys()),
    )
    provider = _build_chat_completions_transport(
        profile,
        provider_conn,
        extra_body=extra_body,
    )
    return LLMProviderBundle(
        provider=provider,
        model=model,
        model_profile=BYOK_PROFILE_KEY,
        model_route=f"byok:{credential_id}" if credential_id else BYOK_PROFILE_KEY,
        provider_name="byok",
        context_limit=effective_context_limit,
        context_limit_source=context_limit_source,
    )
