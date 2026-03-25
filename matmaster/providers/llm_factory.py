"""LLM Provider factory: config-driven provider construction.

Thin factory layer: resolve_route -> OpenAIProvider.
All semantic resolution (family, temperature, reasoning) lives on
LLMProfileConfig methods. This module only does the final mapping.
"""

from __future__ import annotations

import logging

from matmaster.config.llm import LLMConfig
from matmaster.providers.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


def build_provider(
    llm_config: LLMConfig,
    *,
    model_override: str | None = None,
    llm_override: str | None = None,
    default_profile_key: str | None = None,
) -> OpenAIProvider:
    """Resolve route and build OpenAIProvider.

    Args:
        llm_config: Loaded LLMConfig instance.
        model_override: External route key from frontend.
        llm_override: Legacy profile key (compat layer).
        default_profile_key: Agent-level default profile key.
    """
    resolved = llm_config.resolve_route(
        model_override=model_override,
        llm_override=llm_override,
        default_key=default_profile_key,
    )
    profile = llm_config.get_profile(resolved.profile_key)

    logger.info(
        "build_provider: route=%s profile=%s model=%s family=%s",
        resolved.route_key,
        resolved.profile_key,
        resolved.model,
        profile.effective_family(),
    )

    return OpenAIProvider(
        model=resolved.model,
        api_key=profile.api_key,
        base_url=profile.base_url,
        temperature=profile.effective_temperature(),
        max_tokens=profile.max_tokens,
        timeout=profile.timeout,
        max_retries=profile.max_retries,
        retry_delay=profile.retry_delay,
        extra_kwargs=profile.build_extra_kwargs(),
    )
