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
