from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from matmaster.response_text import is_trivial_response_text
from matmaster.types.errors import LLMError
from matmaster.types.messages import AssistantMessage, Message, ToolMessage, UserMessage


def _merge_user_messages(left: UserMessage, right: UserMessage) -> UserMessage:
    content_parts = [
        part.strip()
        for part in (left.content or "", right.content or "")
        if part and part.strip()
    ]
    return UserMessage(
        content="\n\n".join(content_parts),
        images=[*left.images, *right.images],
    )


def canonicalize_messages_for_provider(messages: Iterable[Message]) -> list[Message]:
    canonical: list[Message] = []
    for message in messages:
        if (
            canonical
            and isinstance(canonical[-1], UserMessage)
            and isinstance(message, UserMessage)
        ):
            canonical[-1] = _merge_user_messages(canonical[-1], message)
            continue
        canonical.append(message)
    return canonical


def _is_assistant_like_payload(raw: Any) -> bool:
    return (
        isinstance(raw, dict)
        and raw.get("role") == "assistant"
        and any(key in raw for key in ("content", "tool_calls", "reasoning_content"))
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


def validate_tool_turn_sequence(messages: list[Message]) -> None:
    """Protocol-neutral tool_call <-> tool_result pairing validation.

    Reads Message fields (AssistantMessage.tool_calls[].id / ToolMessage.tool_call_id)
    instead of OpenAI wire dicts. Shared by kernel, checkpoint codec, and transports.
    """
    pending_tool_ids: set[str] = set()
    seen_tool_ids: set[str] = set()

    for message in messages:
        if isinstance(message, ToolMessage):
            tool_id = str(message.tool_call_id or "")
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

        if not isinstance(message, AssistantMessage):
            continue

        declared_ids = [str(tc.id or "") for tc in (message.tool_calls or [])]
        for tool_id in declared_ids:
            if not tool_id:
                raise LLMError(
                    "assistant tool_call missing id",
                    retryable=False,
                    error_category="bad_request",
                )
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
