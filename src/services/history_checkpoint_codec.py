from __future__ import annotations

from typing import Any

from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import (
    normalize_and_validate_openai_messages,
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

MARKERS_V0 = {"<previous_session_summary>"}
MARKERS_V1 = {"<compacted_history>"}


def _has_acceptable_marker(content: str) -> bool:
    # COMPAT:v0-checkpoint-marker -- keep accepting v0 marker until Phase 4.
    return any(marker in content for marker in MARKERS_V0 | MARKERS_V1)


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

    try:
        normalize_and_validate_openai_messages(messages)
    except LLMError as exc:
        # checkpoint callers only contract on ValueError; translate every
        # outbound-validation LLMError so nothing leaks across the boundary.
        if exc.error_category == "bad_request":
            raise ValueError(
                "tool sequence in checkpoint base_messages is invalid"
            ) from exc
        raise ValueError(
            f"checkpoint base_messages failed outbound validation: {exc}"
        ) from exc

    ChatHistoryConverter.validate_dialog_messages_for_llm(
        serialize_base_messages(messages),
        context="history_checkpoint_base_messages",
    )

    if not isinstance(messages[0], UserMessage):
        raise ValueError("checkpoint base_messages must start with compact UserMessage")

    if any(isinstance(message, SystemMessage) for message in messages):
        raise ValueError("checkpoint base_messages must not contain SystemMessage")

    first_content = (messages[0].content or "").strip()
    if not _has_acceptable_marker(first_content):
        raise ValueError(
            "checkpoint base_messages[0] must contain compact context bundle marker"
        )
