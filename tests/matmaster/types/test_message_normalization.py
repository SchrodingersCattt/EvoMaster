from __future__ import annotations

import pytest

from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import (
    normalize_messages_for_openai,
    restore_persisted_assistant_state,
    validate_openai_messages,
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
