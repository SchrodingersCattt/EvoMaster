"""tool_result events restore images into ToolMessage.images."""

from src.services.chat_history import ChatHistoryConverter

_IMG = {
    "url": "data:image/png;base64,aGVsbG8=",
    "mime_type": "image/png",
    "detail": None,
}


def _events_with_tool_image():
    return [
        {"type": "query", "source": "User", "content": {"content": "看图"}},
        {
            "type": "tool_call",
            "source": "MatMaster",
            "content": {"id": "tc1", "name": "Read", "args": {"file_path": "/a.png"}},
        },
        {
            "type": "tool_result",
            "source": "MatMaster",
            "content": {
                "id": "tc1",
                "name": "Read",
                "result": "Read image: /a.png",
                "status": "success",
                "images": [_IMG],
            },
        },
        {"type": "response", "source": "MatMaster", "content": "看到了"},
    ]


def test_events_to_messages_restores_tool_images() -> None:
    messages = ChatHistoryConverter.events_to_messages(_events_with_tool_image())
    tool_msgs = [m for m in messages if getattr(m, "tool_call_id", None) == "tc1"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].images[0].url == _IMG["url"]
    assert tool_msgs[0].images[0].mime_type == "image/png"


def test_events_without_images_restore_empty_list() -> None:
    events = _events_with_tool_image()
    del events[2]["content"]["images"]
    messages = ChatHistoryConverter.events_to_messages(events)
    tool_msgs = [m for m in messages if getattr(m, "tool_call_id", None) == "tc1"]
    assert tool_msgs[0].images == []
