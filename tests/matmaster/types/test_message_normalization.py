from __future__ import annotations

import pytest

from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import (
    normalize_and_validate_openai_messages,
    normalize_messages_for_openai,
    restore_persisted_assistant_state,
    validate_openai_messages,
    validate_openai_tool_turn_sequence,
)
from matmaster.types.messages import AssistantMessage, ToolCallData, UserMessage


class TestNormalizeMessagesForOpenAI:
    def test_assistant_tool_turn_none_content_becomes_empty_string(self) -> None:
        tc = ToolCallData(id="tc-1", name="bash", arguments={"cmd": "pwd"})
        messages = [
            UserMessage(content="run"),
            AssistantMessage(content=None, tool_calls=[tc]),
        ]

        normalized = normalize_messages_for_openai(messages)

        assert normalized[0] == {"role": "user", "content": "run"}
        assert normalized[1]["role"] == "assistant"
        assert normalized[1]["content"] == ""
        assert normalized[1]["tool_calls"][0]["id"] == "tc-1"

    def test_plain_assistant_text_survives_normalization(self) -> None:
        messages = [AssistantMessage(content="done")]

        normalized = normalize_messages_for_openai(messages)

        assert normalized == [{"role": "assistant", "content": "done"}]

    def test_dict_message_without_content_key_gets_empty_string(self) -> None:
        normalized = normalize_messages_for_openai(
            [{"role": "assistant", "tool_calls": []}]
        )

        assert normalized == [{"role": "assistant", "tool_calls": [], "content": ""}]

    def test_validation_rejects_non_string_content(self) -> None:
        messages = [{"role": "assistant", "content": {"bad": "shape"}}]

        with pytest.raises(LLMError) as exc_info:
            validate_openai_messages(messages)

        assert exc_info.value.retryable is False
        assert exc_info.value.error_category == "payload_validation"


class TestNormalizeAndValidateOpenAIMessages:
    def test_valid_messages_are_normalized_before_return(self) -> None:
        tc = ToolCallData(id="tc-1", name="bash", arguments={"cmd": "pwd"})
        messages = [
            UserMessage(content="run"),
            AssistantMessage(content=None, tool_calls=[tc]),
            {"role": "tool", "tool_call_id": "tc-1", "content": "ok"},
        ]

        normalized = normalize_and_validate_openai_messages(messages)

        assert normalized[0] == {"role": "user", "content": "run"}
        assert normalized[1]["role"] == "assistant"
        assert normalized[1]["content"] == ""
        assert normalized[1]["tool_calls"][0]["id"] == "tc-1"
        assert normalized[2] == {
            "role": "tool",
            "tool_call_id": "tc-1",
            "content": "ok",
        }

    def test_invalid_messages_raise_during_combined_validation(self) -> None:
        with pytest.raises(LLMError, match="Outbound message content must be string"):
            normalize_and_validate_openai_messages(
                [{"role": "assistant", "content": {"bad": "shape"}}]
            )

    def test_missing_tool_result_raises_during_combined_validation(self) -> None:
        with pytest.raises(LLMError, match="missing tool_result ids"):
            normalize_and_validate_openai_messages(
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "tc-1",
                                "type": "function",
                                "function": {
                                    "name": "test_tool",
                                    "arguments": '{"x": 1}',
                                },
                            },
                            {
                                "id": "tc-2",
                                "type": "function",
                                "function": {
                                    "name": "test_tool",
                                    "arguments": '{"x": 2}',
                                },
                            },
                        ],
                    },
                    {"role": "tool", "tool_call_id": "tc-1", "content": "ok-1"},
                ]
            )


class TestValidateOpenAIToolTurnSequence:
    def test_rejects_orphan_tool_message(self) -> None:
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "plain text"},
            {"role": "tool", "tool_call_id": "tc-orphan", "content": "oops"},
        ]

        with pytest.raises(LLMError, match="orphan tool message"):
            validate_openai_tool_turn_sequence(messages)

    def test_rejects_duplicate_tool_results(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "type": "function",
                        "function": {"name": "test_tool", "arguments": '{"x": 1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tc-1", "content": "ok"},
            {"role": "tool", "tool_call_id": "tc-1", "content": "duplicate"},
        ]

        with pytest.raises(LLMError, match="duplicate tool_result ids"):
            validate_openai_tool_turn_sequence(messages)

    def test_accepts_matching_tool_messages(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "type": "function",
                        "function": {"name": "test_tool", "arguments": '{"x": 1}'},
                    },
                    {
                        "id": "tc-2",
                        "type": "function",
                        "function": {"name": "test_tool", "arguments": '{"x": 2}'},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "tc-1", "content": "ok-1"},
            {"role": "tool", "tool_call_id": "tc-2", "content": "ok-2"},
        ]

        validate_openai_tool_turn_sequence(messages)


class TestRestorePersistedAssistantState:
    def test_restore_wrapped_state_preserves_internal_none(self) -> None:
        restored = restore_persisted_assistant_state(
            {
                "state": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "tc-1",
                            "name": "bash",
                            "arguments": {"cmd": "pwd"},
                        }
                    ],
                }
            }
        )

        assert restored.content is None
        assert restored.tool_calls is not None
        assert restored.tool_calls[0].id == "tc-1"

    def test_restore_trivial_preamble_becomes_internal_none(self) -> None:
        restored = restore_persisted_assistant_state(
            {
                "state": {
                    "role": "assistant",
                    "content": "...",
                    "tool_calls": [
                        {
                            "id": "tc-ellipsis",
                            "name": "bash",
                            "arguments": {"cmd": "pwd"},
                        }
                    ],
                }
            }
        )

        assert restored.content is None

    def test_restore_rejects_non_assistant_wrapper(self) -> None:
        with pytest.raises(ValueError, match="assistant_state payload"):
            restore_persisted_assistant_state(
                {"state": {"role": "tool", "content": "bad"}}
            )
