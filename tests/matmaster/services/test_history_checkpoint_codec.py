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


def test_serialize_base_messages_uses_model_dump_json() -> None:
    messages = [
        SystemMessage(content="[Compacted Context]\nsummary"),
        UserMessage(content="task"),
        AssistantMessage(
            content=None,
            tool_calls=[
                ToolCallData(id="tc-1", name="bash", arguments={"cmd": "pwd"})
            ],
            reasoning_content="reasoning",
        ),
        ToolMessage(tool_call_id="tc-1", tool_name="bash", content="ok"),
    ]

    payload = serialize_base_messages(messages)

    assert payload[0]["role"] == "system"
    assert payload[2]["role"] == "assistant"
    assert payload[2]["reasoning_content"] == "reasoning"
    assert payload[3]["tool_name"] == "bash"


def test_deserialize_base_messages_roundtrip() -> None:
    messages = [
        SystemMessage(content="[Compacted Context]\nsummary"),
        UserMessage(content="task"),
        AssistantMessage(
            content=None,
            tool_calls=[
                ToolCallData(id="tc-1", name="bash", arguments={"cmd": "pwd"})
            ],
            reasoning_content="reasoning",
        ),
        ToolMessage(tool_call_id="tc-1", tool_name="bash", content="ok"),
    ]

    restored = deserialize_base_messages(serialize_base_messages(messages))

    assert isinstance(restored[0], SystemMessage)
    assert isinstance(restored[1], UserMessage)
    assert isinstance(restored[2], AssistantMessage)
    assert isinstance(restored[3], ToolMessage)
    assert restored[2].tool_calls is not None
    assert restored[2].tool_calls[0].id == "tc-1"
    assert restored[3].tool_name == "bash"


def test_validate_base_messages_rejects_empty() -> None:
    with pytest.raises(ValueError, match="base_messages must not be empty"):
        validate_base_messages([])


def test_validate_base_messages_rejects_invalid_tool_sequence() -> None:
    messages = [
        SystemMessage(content="sys"),
        ToolMessage(tool_call_id="tc-1", tool_name="bash", content="result"),
    ]

    with pytest.raises(ValueError, match="tool sequence"):
        validate_base_messages(messages)
