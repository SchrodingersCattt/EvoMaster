import pytest

from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import validate_tool_turn_sequence
from matmaster.types.messages import (
    AssistantMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _assistant_with_calls(*ids):
    return AssistantMessage(
        content="",
        tool_calls=[ToolCallData(id=i, name="t", arguments={}) for i in ids],
    )


def test_valid_paired_sequence_passes():
    msgs = [
        UserMessage(content="hi"),
        _assistant_with_calls("c1"),
        ToolMessage(content="ok", tool_call_id="c1", tool_name="t"),
    ]
    validate_tool_turn_sequence(msgs)  # no raise


def test_orphan_tool_raises():
    msgs = [
        UserMessage(content="hi"),
        ToolMessage(content="x", tool_call_id="c1", tool_name="t"),
    ]
    with pytest.raises(LLMError) as exc:
        validate_tool_turn_sequence(msgs)
    assert exc.value.error_category == "bad_request"


def test_duplicate_tool_call_id_raises():
    msgs = [_assistant_with_calls("c1", "c1")]
    with pytest.raises(LLMError):
        validate_tool_turn_sequence(msgs)


def test_missing_tool_result_raises():
    msgs = [_assistant_with_calls("c1"), UserMessage(content="next")]
    with pytest.raises(LLMError):
        validate_tool_turn_sequence(msgs)


def test_tool_result_without_matching_call_raises():
    msgs = [
        _assistant_with_calls("c1"),
        ToolMessage(content="x", tool_call_id="c2", tool_name="t"),
    ]
    with pytest.raises(LLMError):
        validate_tool_turn_sequence(msgs)
