from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.messages import ImageContentPart, UserMessage


def _convert(messages):
    t = ChatCompletionsTransport.__new__(ChatCompletionsTransport)
    return t.convert_messages(messages)


def test_user_message_with_images_renders_openai_content_parts() -> None:
    message = UserMessage(
        content="请分析这张显微图",
        images=[
            ImageContentPart(
                url="https://oss.example.com/chat/sess/image.png",
                mime_type="image/png",
                detail="high",
            )
        ],
    )

    assert _convert([message]) == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请分析这张显微图"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://oss.example.com/chat/sess/image.png",
                        "detail": "high",
                    },
                },
            ],
        }
    ]


def test_user_content_parts_without_text_are_valid() -> None:
    message = UserMessage(
        content="",
        images=[ImageContentPart(url="https://oss.example.com/a.webp")],
    )

    converted = _convert([message])

    assert converted[0]["content"][0]["image_url"]["url"].endswith("a.webp")


def test_tool_message_images_roundtrip() -> None:
    from matmaster.types.messages import ToolMessage

    msg = ToolMessage(
        tool_call_id="tc1",
        tool_name="Read",
        content="Read image: a.png",
        images=[
            ImageContentPart(
                url="data:image/png;base64,aGVsbG8=", mime_type="image/png"
            )
        ],
    )
    restored = ToolMessage.model_validate(msg.model_dump(mode="json"))
    assert restored.images[0].url == msg.images[0].url
    assert ToolMessage(tool_call_id="tc2", tool_name="Read", content="x").images == []
