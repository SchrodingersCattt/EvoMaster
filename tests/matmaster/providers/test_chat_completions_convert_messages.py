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


def _image(url="data:image/png;base64,aGVsbG8="):
    return ImageContentPart(url=url, mime_type="image/png", detail="high")


def _tool_turn(images_on=("tc1",)):
    return [
        UserMessage(content="看图"),
        AssistantMessage(
            content=None,
            tool_calls=[
                ToolCallData(id="tc1", name="Read", arguments={}),
                ToolCallData(id="tc2", name="Read", arguments={}),
            ],
        ),
        ToolMessage(
            tool_call_id="tc1",
            tool_name="Read",
            content="Read image: /a.png",
            images=[_image()] if "tc1" in images_on else [],
        ),
        ToolMessage(
            tool_call_id="tc2",
            tool_name="Read",
            content="plain text result",
            images=[_image()] if "tc2" in images_on else [],
        ),
    ]


def test_relay_inserted_after_tool_group():
    wire = _convert(_tool_turn(images_on=("tc1",)))
    roles = [m["role"] for m in wire]
    assert roles == ["user", "assistant", "tool", "tool", "user"]
    relay = wire[-1]["content"]
    assert relay[0] == {"type": "text", "text": "[Images from Read (tool_call tc1)]"}
    assert relay[1]["type"] == "image_url"
    assert relay[1]["image_url"] == {
        "url": "data:image/png;base64,aGVsbG8=",
        "detail": "high",
    }


def test_relay_merges_into_following_user_message():
    messages = _tool_turn(images_on=("tc2",)) + [UserMessage(content="继续")]
    wire = _convert(messages)
    roles = [m["role"] for m in wire]
    assert roles == ["user", "assistant", "tool", "tool", "user"]
    merged = wire[-1]["content"]
    assert merged[0]["text"] == "[Images from Read (tool_call tc2)]"
    assert merged[1]["type"] == "image_url"
    assert merged[-1] == {"type": "text", "text": "继续"}


def test_no_images_keeps_wire_unchanged():
    wire = _convert(_tool_turn(images_on=()))
    assert [m["role"] for m in wire] == ["user", "assistant", "tool", "tool"]
    assert all("image_url" not in str(m.get("content")) for m in wire)
