"""Classify non-natural LLM finish states into structured diagnostics."""

from __future__ import annotations

import logging

from matmaster.response_text import normalize_visible_response_text
from matmaster.types.errors import LLMError
from matmaster.types.events import FinishDetail
from matmaster.types.messages import LLMResponse

logger = logging.getLogger(__name__)


def build_finish_detail(
    response: LLMResponse | None,
    *,
    attempts: int | None = None,
    last_error: LLMError | None = None,
) -> FinishDetail:
    """Classify invalid LLM finish state.

    The input finish reason is already normalized by provider adapters where
    possible. OpenAI-style ``content_filter`` maps to ``content_filtered``;
    Bedrock-specific stop reasons such as ``guardrail_intervened`` remain
    provider finish reasons and fall through to ``non_stop_finish``.
    """
    try:
        return _build_finish_detail_inner(
            response,
            attempts=attempts,
            last_error=last_error,
        )
    except Exception:
        logger.warning("finish detail classification failed", exc_info=True)
        return FinishDetail(
            kind="unknown",
            message="Model finish state could not be classified.",
        )


def is_valid_natural_finish(response: LLMResponse) -> bool:
    """Return True when the response can be committed as natural output."""
    return (
        not response.tool_calls
        and response.finish_reason == "stop"
        and _has_visible_content(response)
    )


def is_incomplete_response(response: LLMResponse) -> bool:
    """Return True for non-tool responses with no visible final output."""
    return not response.tool_calls and not _has_visible_content(response)


def _build_finish_detail_inner(
    response: LLMResponse | None,
    *,
    attempts: int | None = None,
    last_error: LLMError | None = None,
) -> FinishDetail:
    if response is None:
        return FinishDetail(
            kind="missing_llm_response",
            message="LLM stream ended without a final response object.",
            attempts=attempts,
            last_error_kind=getattr(last_error, "error_category", None),
        )

    finish_reason = response.finish_reason
    has_visible = _has_visible_content(response)
    has_reasoning = bool(response.reasoning_content)
    tool_calls = response.tool_calls or []
    base = {
        "provider_finish_reason": finish_reason,
        "content_chars": len(response.content or ""),
        "reasoning_chars": len(response.reasoning_content or ""),
        "has_visible_content": has_visible,
        "has_reasoning": has_reasoning,
        "has_tool_calls": bool(tool_calls),
        "tool_call_count": len(tool_calls),
        "last_turn_usage": dict(response.usage or {}),
        "last_turn_usage_vendor": dict(response.usage_vendor or {}),
    }

    if finish_reason == "length":
        return FinishDetail(
            kind="output_length_exceeded",
            message="Model output was truncated by the provider output-token limit.",
            truncation_risk=True,
            **base,
        )
    if finish_reason == "content_filter":
        return FinishDetail(
            kind="content_filtered",
            message="Model output was blocked or truncated by provider content policy.",
            **base,
        )
    if finish_reason == "stop" and not has_visible and has_reasoning:
        return FinishDetail(
            kind="reasoning_only",
            message="Model returned reasoning content without a visible final answer.",
            **base,
        )
    if finish_reason == "stop" and not has_visible:
        return FinishDetail(
            kind="empty_response",
            message="Model stopped without a visible final answer.",
            **base,
        )
    return FinishDetail(
        kind="non_stop_finish",
        message="Model returned a finish reason that cannot be committed as natural.",
        **base,
    )


def _has_visible_content(response: LLMResponse) -> bool:
    return normalize_visible_response_text(response.content) is not None
