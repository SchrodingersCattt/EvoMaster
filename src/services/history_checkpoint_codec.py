from __future__ import annotations

from typing import Any

from matmaster.types.message_normalization import (
    normalize_messages_for_openai,
    validate_openai_messages,
)
from matmaster.types.messages import (
    AssistantMessage,
    Message,
    Role,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from src.services.chat_history import ChatHistoryConverter

_ROLE_TO_MESSAGE_MODEL: dict[str, type[Message]] = {
    Role.SYSTEM.value: SystemMessage,
    Role.USER.value: UserMessage,
    Role.ASSISTANT.value: AssistantMessage,
    Role.TOOL.value: ToolMessage,
}


def _message_role_name(raw: dict[str, Any]) -> str:
    role = raw.get("role")
    if isinstance(role, str):
        return role.strip().lower()
    if role is not None and hasattr(role, "value"):
        return str(role.value).strip().lower()
    return str(role or "").strip().lower()


def serialize_base_messages(messages: list[Message]) -> list[dict[str, Any]]:
    return [message.model_dump(mode="json") for message in messages]


def deserialize_base_messages(raw_messages: list[dict[str, Any]]) -> list[Message]:
    messages: list[Message] = []

    for idx, raw in enumerate(raw_messages):
        if not isinstance(raw, dict):
            raise ValueError(
                f"base message at index {idx} must be a dict, got {type(raw).__name__}"
            )

        role_name = _message_role_name(raw)
        model_cls = _ROLE_TO_MESSAGE_MODEL.get(role_name)
        if model_cls is None:
            raise ValueError(
                f"unsupported base message role at index {idx}: {role_name!r}"
            )

        messages.append(model_cls.model_validate(raw))

    return messages


def validate_base_messages(messages: list[Message]) -> None:
    if not messages:
        raise ValueError("base_messages must not be empty")

    normalized_messages = normalize_messages_for_openai(messages)
    validate_openai_messages(normalized_messages)

    ChatHistoryConverter.validate_dialog_messages_for_llm(
        serialize_base_messages(messages),
        context="history_checkpoint_base_messages",
    )

    if not isinstance(messages[0], SystemMessage):
        raise ValueError(
            "checkpoint base_messages must start with compacted SystemMessage"
        )

    if _has_invalid_tool_sequence(messages):
        raise ValueError("tool sequence in checkpoint base_messages is invalid")


def _has_invalid_tool_sequence(messages: list[Message]) -> bool:
    i = 0
    while i < len(messages):
        message = messages[i]

        if not isinstance(message, AssistantMessage):
            if isinstance(message, ToolMessage):
                return True
            i += 1
            continue

        if any(not str(tool_call.id) for tool_call in (message.tool_calls or [])):
            return True

        declared_ids = [
            str(tool_call.id)
            for tool_call in (message.tool_calls or [])
            if str(tool_call.id)
        ]
        if not declared_ids:
            if i + 1 < len(messages) and isinstance(messages[i + 1], ToolMessage):
                return True
            i += 1
            continue

        seen_tool_ids: set[str] = set()
        j = i + 1
        while j < len(messages) and isinstance(messages[j], ToolMessage):
            tool_id = str(messages[j].tool_call_id or "")
            if tool_id in seen_tool_ids:
                return True
            if tool_id not in declared_ids:
                return True
            seen_tool_ids.add(tool_id)
            j += 1

        if len(seen_tool_ids) != len(declared_ids):
            return True

        i = j

    return False
