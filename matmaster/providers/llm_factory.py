"""LLM provider factory：dispatch 表驱动构造。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeVar

from matmaster.config.llm import (
    LLMConfig,
    LLMProfileConfig,
    ProviderConfig,
    ResolvedModel,
)
from matmaster.providers.transports.anthropic_messages import (
    AnthropicMessagesTransport,
    AnthropicPromptCacheOptions,
    BedrockAnthropicTransport,
)
from matmaster.providers.transports.chat_completions import (
    ChatCompletionsTransport,
    DeepSeekChatCompletionsTransport,
    QwenChatCompletionsTransport,
)
from matmaster.providers.transports.responses import ResponsesTransport
from matmaster.types.llm_provider import LLMProvider

BYOK_PROFILE_KEY = "byok"
BYOK_DEFAULT_CONTEXT_LIMIT = 200_000

logger = logging.getLogger(__name__)

_TransportClassT = TypeVar("_TransportClassT")

_CHAT_COMPLETIONS_BY_VENDOR: dict[str | None, type[ChatCompletionsTransport]] = {
    None: ChatCompletionsTransport,
    "qwen": QwenChatCompletionsTransport,
    "deepseek": DeepSeekChatCompletionsTransport,
}
_ANTHROPIC_BY_VENDOR: dict[str | None, type[AnthropicMessagesTransport]] = {
    None: AnthropicMessagesTransport,
    "bedrock": BedrockAnthropicTransport,
}
_RESPONSES_BY_VENDOR: dict[str | None, type[ResponsesTransport]] = {
    None: ResponsesTransport,
}


def _vendor_class(
    by_vendor: dict[str | None, type[_TransportClassT]],
    provider: ProviderConfig,
) -> type[_TransportClassT]:
    """(transport, vendor) -> transport 类；未知 vendor 装配期 fail-fast。"""
    try:
        return by_vendor[provider.vendor]
    except KeyError as exc:
        raise ValueError(
            f"unsupported vendor {provider.vendor!r} for transport "
            f"{provider.transport!r}, available: {list(by_vendor)}"
        ) from exc


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
    supports_vision: bool = False
    vision_detail: Literal["low", "high", "auto"] | None = None


def _build_chat_completions_transport(
    profile: LLMProfileConfig,
    provider: ProviderConfig,
    *,
    extra_body: dict | None = None,
) -> ChatCompletionsTransport:
    """profile 平铺字段 + provider 连接到 ChatCompletionsTransport（vendor 分发）。"""
    cls = _vendor_class(_CHAT_COMPLETIONS_BY_VENDOR, provider)
    return cls(
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


def _build_anthropic_prompt_cache_options(
    profile: LLMProfileConfig,
) -> AnthropicPromptCacheOptions | None:
    prompt_cache = profile.prompt_cache
    if prompt_cache is None:
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


def _build_anthropic_messages_transport(
    profile: LLMProfileConfig,
    provider: ProviderConfig,
    *,
    extra_body: dict | None = None,
) -> AnthropicMessagesTransport:
    """profile 平铺字段 + provider 连接到 Anthropic Messages transport（vendor 分发）。"""
    if extra_body is not None:
        raise ValueError("anthropic_messages transport does not support extra_body")
    cls = _vendor_class(_ANTHROPIC_BY_VENDOR, provider)
    return cls(
        model=profile.model,
        api_key=provider.api_key,
        base_url=provider.base_url,
        max_tokens=profile.max_tokens,
        reasoning_effort=profile.reasoning_effort,
        prompt_cache_options=_build_anthropic_prompt_cache_options(profile),
        timeout=profile.timeout,
        stream_timeout=profile.stream_timeout,
        stream_idle_timeout=profile.stream_idle_timeout,
        max_retries=profile.max_retries,
        retry_delay=profile.retry_delay,
    )


def _build_responses_transport(
    profile: LLMProfileConfig,
    provider: ProviderConfig,
    *,
    extra_body: dict | None = None,
) -> ResponsesTransport:
    """profile 平铺字段 + provider 连接到 Responses transport（vendor 分发）。"""
    if extra_body is not None:
        raise ValueError("responses transport does not support extra_body")
    cls = _vendor_class(_RESPONSES_BY_VENDOR, provider)
    return cls(
        model=profile.model,
        api_key=provider.api_key,
        base_url=provider.base_url,
        max_tokens=profile.max_tokens,
        reasoning_effort=profile.reasoning_effort,
        reasoning_summary=profile.reasoning_summary,
        timeout=profile.timeout,
        stream_timeout=profile.stream_timeout,
        stream_idle_timeout=profile.stream_idle_timeout,
        max_retries=profile.max_retries,
        retry_delay=profile.retry_delay,
    )


_TRANSPORT_BUILDERS: dict[str, Callable[..., LLMProvider]] = {
    "chat_completions": _build_chat_completions_transport,
    "anthropic_messages": _build_anthropic_messages_transport,
    "responses": _build_responses_transport,
}


def _dispatch(
    profile: LLMProfileConfig,
    provider: ProviderConfig,
    *,
    extra_body: dict | None = None,
) -> LLMProvider:
    try:
        builder = _TRANSPORT_BUILDERS[provider.transport]
    except KeyError as exc:
        raise ValueError(
            f"unsupported transport: {provider.transport!r}, "
            f"available: {list(_TRANSPORT_BUILDERS)}"
        ) from exc
    return builder(profile, provider, extra_body=extra_body)


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
        supports_vision=resolved.profile.supports_vision,
        vision_detail=resolved.profile.vision_detail,
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
    provider = _dispatch(profile, provider_conn, extra_body=extra_body)
    return LLMProviderBundle(
        provider=provider,
        model=model,
        model_profile=BYOK_PROFILE_KEY,
        model_route=f"byok:{credential_id}" if credential_id else BYOK_PROFILE_KEY,
        provider_name="byok",
        context_limit=effective_context_limit,
        context_limit_source=context_limit_source,
        supports_vision=profile.supports_vision,
        vision_detail=profile.vision_detail,
    )
