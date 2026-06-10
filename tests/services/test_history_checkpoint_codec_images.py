"""checkpoint codec preserves ToolMessage.images."""

from matmaster.types.messages import ImageContentPart, ToolMessage
from src.services.history_checkpoint_codec import (
    deserialize_base_messages,
    serialize_base_messages,
)


def test_checkpoint_roundtrip_preserves_tool_images() -> None:
    messages = [
        ToolMessage(
            tool_call_id="tc1",
            tool_name="Read",
            content="Read image: /a.png",
            images=[
                ImageContentPart(
                    url="data:image/png;base64,aGVsbG8=",
                    mime_type="image/png",
                    detail="high",
                )
            ],
        )
    ]
    restored = deserialize_base_messages(serialize_base_messages(messages))
    assert restored[0].images == messages[0].images
