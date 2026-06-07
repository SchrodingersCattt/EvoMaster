import pytest

from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.errors import LLMError
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _convert(messages):
    t = ChatCompletionsTransport.__new__(ChatCompletionsTransport)
    return t.convert_messages(messages)


def test_system_and_user_text():
    out = _convert([SystemMessage(content="sys"), UserMessage(content="hi")])
    assert out == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]


def test_user_with_image_parts():
    msg = UserMessage(
        content="look",
        images=[ImageContentPart(url="http://x/y.png", detail="high")],
    )
    out = _convert([msg])
    assert out[0]["role"] == "user"
    assert out[0]["content"] == [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "http://x/y.png", "detail": "high"}},
    ]


def test_assistant_with_tool_calls():
    msg = AssistantMessage(
        content=None,
        tool_calls=[ToolCallData(id="c1", name="f", arguments={"a": 1})],
    )
    tool = ToolMessage(content="ok", tool_call_id="c1", tool_name="f")
    out = _convert([msg, tool])
    assert out[0]["content"] == ""
    assert out[0]["tool_calls"] == [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "f", "arguments": '{"a": 1}'},
        }
    ]
    assert out[1] == {"role": "tool", "content": "ok", "tool_call_id": "c1"}


def test_content_none_normalized_to_empty_string():
    out = _convert([AssistantMessage(content=None)])
    assert out[0]["content"] == ""


def test_invalid_tool_turn_raises():
    with pytest.raises(LLMError):
        _convert([ToolMessage(content="x", tool_call_id="c1", tool_name="f")])
