from __future__ import annotations

import pytest

from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
from src.services.history_checkpoint_codec import (
    deserialize_base_messages,
    serialize_base_messages,
    validate_base_messages,
)


def _compact_user_message() -> UserMessage:
    return UserMessage(
        content=(
            "以下是先前对话的压缩摘要，作为历史背景。"
            "\n\n<previous_session_summary>\nsummary\n</previous_session_summary>"
        )
    )


def test_serialize_base_messages_uses_model_dump_json() -> None:
    messages = [
        _compact_user_message(),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="tc-1", name="bash", arguments={"cmd": "pwd"})],
            reasoning_content="reasoning",
        ),
        ToolMessage(tool_call_id="tc-1", tool_name="bash", content="ok"),
    ]

    payload = serialize_base_messages(messages)

    assert payload[0]["role"] == "user"
    assert payload[1]["role"] == "assistant"
    assert payload[1]["reasoning_content"] == "reasoning"
    assert payload[2]["tool_name"] == "bash"


def test_deserialize_base_messages_roundtrip() -> None:
    messages = [
        _compact_user_message(),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="tc-1", name="bash", arguments={"cmd": "pwd"})],
            reasoning_content="reasoning",
        ),
        ToolMessage(tool_call_id="tc-1", tool_name="bash", content="ok"),
    ]

    restored = deserialize_base_messages(serialize_base_messages(messages))

    assert isinstance(restored[0], UserMessage)
    assert isinstance(restored[1], AssistantMessage)
    assert isinstance(restored[2], ToolMessage)
    assert restored[1].tool_calls is not None
    assert restored[1].tool_calls[0].id == "tc-1"
    assert restored[2].tool_name == "bash"


def test_validate_base_messages_accepts_compact_user_bundle() -> None:
    validate_base_messages([_compact_user_message()])


def test_validate_base_messages_accepts_compacted_history_marker() -> None:
    validate_base_messages(
        [
            UserMessage(
                content=(
                    "<user_instructions>\nUse SI units.\n</user_instructions>"
                    "\n\n<compacted_history>\nsummary\n</compacted_history>"
                )
            )
        ]
    )


def test_validate_base_messages_rejects_system_message_anywhere() -> None:
    with pytest.raises(ValueError, match="must not contain SystemMessage"):
        validate_base_messages(
            [
                _compact_user_message(),
                SystemMessage(content="[Compacted Context]\nold"),
            ]
        )


def test_validate_base_messages_rejects_old_system_start_checkpoint() -> None:
    with pytest.raises(ValueError, match="must start with compact UserMessage"):
        validate_base_messages([SystemMessage(content="[Compacted Context]\nold")])


def test_validate_base_messages_rejects_empty() -> None:
    with pytest.raises(ValueError, match="base_messages must not be empty"):
        validate_base_messages([])


def test_validate_base_messages_rejects_invalid_tool_sequence() -> None:
    messages = [
        _compact_user_message(),
        ToolMessage(tool_call_id="tc-1", tool_name="bash", content="result"),
    ]

    with pytest.raises(ValueError, match="tool sequence"):
        validate_base_messages(messages)


def test_validate_base_messages_rejects_orphan_tool_after_assistant_text() -> None:
    messages = [
        _compact_user_message(),
        AssistantMessage(content="hello"),
        ToolMessage(tool_call_id="tc-1", tool_name="bash", content="result"),
    ]

    with pytest.raises(ValueError, match="tool sequence"):
        validate_base_messages(messages)


def test_validate_base_messages_rejects_tool_id_mismatch() -> None:
    messages = [
        _compact_user_message(),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="tc-1", name="bash", arguments={"cmd": "pwd"})],
        ),
        ToolMessage(tool_call_id="tc-2", tool_name="bash", content="result"),
    ]

    with pytest.raises(ValueError, match="tool sequence"):
        validate_base_messages(messages)


def test_validate_base_messages_rejects_duplicate_tool_results() -> None:
    messages = [
        _compact_user_message(),
        AssistantMessage(
            content=None,
            tool_calls=[
                ToolCallData(id="tc-1", name="bash", arguments={"cmd": "pwd"}),
                ToolCallData(id="tc-2", name="bash", arguments={"cmd": "ls"}),
            ],
        ),
        ToolMessage(tool_call_id="tc-1", tool_name="bash", content="result-1"),
        ToolMessage(tool_call_id="tc-1", tool_name="bash", content="result-2"),
    ]

    with pytest.raises(ValueError, match="tool sequence"):
        validate_base_messages(messages)


def test_validate_base_messages_rejects_unclosed_turn_before_new_assistant() -> None:
    messages = [
        _compact_user_message(),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="tc-1", name="bash", arguments={"cmd": "pwd"})],
        ),
        AssistantMessage(content="next turn"),
    ]

    with pytest.raises(ValueError, match="tool sequence"):
        validate_base_messages(messages)


def test_validate_base_messages_rejects_empty_tool_call_id() -> None:
    messages = [
        _compact_user_message(),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="", name="bash", arguments={"cmd": "pwd"})],
        ),
    ]

    with pytest.raises(ValueError, match="tool sequence"):
        validate_base_messages(messages)
