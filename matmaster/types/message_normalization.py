from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from matmaster.response_text import is_trivial_response_text
from matmaster.types.errors import LLMError
from matmaster.types.messages import AssistantMessage, Message

logger = logging.getLogger(__name__)

_OPENAI_COMPATIBLE_ROLES = {"system", "user", "assistant", "tool"}


def _message_to_api_dict(message: Message | dict[str, Any]) -> dict[str, Any]:
    if isinstance(message, Message):
        return message.to_api_dict()
    return dict(message)


def _is_assistant_like_payload(raw: Any) -> bool:
    return (
        isinstance(raw, dict)
        and raw.get("role") == "assistant"
        and any(key in raw for key in ("content", "tool_calls", "reasoning_content"))
    )


def normalize_messages_for_openai(
    messages: Iterable[Message | dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    changed_indices: list[int] = []

    for idx, message in enumerate(messages):
        payload = _message_to_api_dict(message)
        if "content" not in payload or payload.get("content") is None:
            payload["content"] = ""
            changed_indices.append(idx)
        normalized.append(payload)

    if changed_indices:
        logger.debug(
            "Normalized outbound OpenAI-compatible messages with empty-string content at indices=%s",
            changed_indices,
        )

    return normalized


def normalize_and_validate_openai_messages(
    messages: Iterable[Message | dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized = normalize_messages_for_openai(messages)
    validate_openai_messages(normalized)
    validate_openai_tool_turn_sequence(normalized)
    return normalized


def validate_openai_messages(messages: list[dict[str, Any]]) -> None:
    for idx, message in enumerate(messages):
        role = message.get("role")
        if role not in _OPENAI_COMPATIBLE_ROLES:
            raise LLMError(
                f"Unsupported outbound message role at index {idx}: {role!r}",
                retryable=False,
                error_category="payload_validation",
            )

        content = message.get("content")
        if not isinstance(content, str):
            raise LLMError(
                f"Outbound message content must be string at index {idx}, got {type(content).__name__}",
                retryable=False,
                error_category="payload_validation",
            )


def validate_openai_tool_turn_sequence(messages: list[dict[str, Any]]) -> None:
    pending_tool_ids: set[str] = set()
    seen_tool_ids: set[str] = set()

    for message in messages:
        role = message.get("role")

        if role == "tool":
            tool_id = str(message.get("tool_call_id") or "")
            if tool_id in seen_tool_ids:
                raise LLMError(
                    f"duplicate tool_result ids for assistant turn: {tool_id}",
                    retryable=False,
                    error_category="bad_request",
                )
            if not pending_tool_ids and not seen_tool_ids:
                raise LLMError(
                    "orphan tool message after assistant without tool_calls",
                    retryable=False,
                    error_category="bad_request",
                )
            if not tool_id or tool_id not in pending_tool_ids:
                raise LLMError(
                    f"tool_result without matching previous assistant tool_call: {tool_id}",
                    retryable=False,
                    error_category="bad_request",
                )
            seen_tool_ids.add(tool_id)
            pending_tool_ids.remove(tool_id)
            continue

        if pending_tool_ids:
            raise LLMError(
                f"missing tool_result ids for assistant turn: {sorted(pending_tool_ids)}",
                retryable=False,
                error_category="bad_request",
            )

        seen_tool_ids.clear()

        if role != "assistant":
            continue

        raw_tool_calls = message.get("tool_calls") or []
        declared_ids: list[str] = []
        for tool_call in raw_tool_calls:
            if not isinstance(tool_call, dict):
                raise LLMError(
                    "assistant tool_call payload must be a dict",
                    retryable=False,
                    error_category="bad_request",
                )
            tool_id = str(tool_call.get("id") or "")
            if not tool_id:
                raise LLMError(
                    "assistant tool_call missing id",
                    retryable=False,
                    error_category="bad_request",
                )
            declared_ids.append(tool_id)

        if len(declared_ids) != len(set(declared_ids)):
            duplicates = sorted(
                {tool_id for tool_id in declared_ids if declared_ids.count(tool_id) > 1}
            )
            raise LLMError(
                f"duplicate tool_call ids in outbound assistant turn: {duplicates}",
                retryable=False,
                error_category="bad_request",
            )

        seen_tool_ids = set()
        pending_tool_ids = set(declared_ids)

    if pending_tool_ids:
        raise LLMError(
            f"missing tool_result ids for assistant turn: {sorted(pending_tool_ids)}",
            retryable=False,
            error_category="bad_request",
        )


def restore_persisted_assistant_state(raw: Any) -> AssistantMessage:
    if isinstance(raw, dict) and isinstance(raw.get("state"), dict):
        candidate = dict(raw["state"])
    elif _is_assistant_like_payload(raw):
        candidate = dict(raw)
    else:
        raise ValueError(
            f"assistant_state payload is not restorable: {type(raw).__name__}"
        )

    if (
        isinstance(candidate.get("tool_calls"), list)
        and candidate["tool_calls"]
        and is_trivial_response_text(candidate.get("content"))
    ):
        candidate["content"] = None

    if not _is_assistant_like_payload(candidate):
        raise ValueError("assistant_state payload must describe an assistant message")

    return AssistantMessage.model_validate(candidate)
