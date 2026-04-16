import pytest

from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import (
    normalize_and_validate_openai_messages,
)
from matmaster.types.messages import ImageContentPart, UserMessage


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

    assert message.to_api_dict() == {
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


def test_user_content_parts_pass_normalization() -> None:
    normalized = normalize_and_validate_openai_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://oss.example.com/a.webp"},
                    },
                ],
            }
        ]
    )

    assert normalized[0]["content"][1]["image_url"]["url"].endswith("a.webp")


def test_assistant_content_parts_are_still_rejected() -> None:
    with pytest.raises(LLMError, match="assistant"):
        normalize_and_validate_openai_messages(
            [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "no"}],
                }
            ]
        )
