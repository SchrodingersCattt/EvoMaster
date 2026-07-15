"""Child exp 的 LLM profile 解析。"""

from __future__ import annotations

import logging

from matmaster.config.exp import ExpConfig
from matmaster.core.run_context import AgentRunContext

logger = logging.getLogger(__name__)


def _resolve_child_run_ctx(
    ctx: AgentRunContext,
    child_cfg: ExpConfig,
) -> AgentRunContext:
    """按 child exp 的 llm 字段换出 provider；否则原样继承父 ctx。"""
    factory = ctx.request.ports.subagent_provider_factory
    if not child_cfg.llm or factory is None:
        return ctx
    try:
        bundle = factory(profile_key=child_cfg.llm)
    except KeyError:
        logger.warning(
            "subagent llm profile %r unresolvable, inheriting parent profile",
            child_cfg.llm,
        )
        return ctx
    return ctx.model_copy(
        update={
            "request": ctx.request.model_copy(
                update={
                    "llm_provider": bundle.provider,
                    "llm_model": bundle.model,
                    "llm_model_profile": bundle.model_profile,
                    "llm_model_route": bundle.model_route,
                    "context_limit": bundle.context_limit,
                    "supports_vision": bundle.supports_vision,
                    "vision_detail": bundle.vision_detail,
                }
            )
        }
    )
