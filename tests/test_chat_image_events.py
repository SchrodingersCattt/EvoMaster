from unittest.mock import MagicMock

from src.models.chat import ChatSendRequest
from src.services.chat_history import ChatHistoryConverter
from src.services.events_service import ChatEventsService


def test_chat_send_request_accepts_images() -> None:
    req = ChatSendRequest(
        content="看图",
        images=["https://oss.example.com/chat/image.png"],
    )

    assert req.images == ["https://oss.example.com/chat/image.png"]


def test_events_service_persists_images_inside_user_query_content() -> None:
    table = MagicMock()
    sessions = MagicMock()
    service = ChatEventsService(events_table=table, sessions_service=sessions)

    service.add_history_event(
        "sess-1",
        {
            "source": "User",
            "type": "query",
            "content": "看图",
            "files": ["https://oss.example.com/chat/data.csv"],
            "images": ["https://oss.example.com/chat/image.png"],
            "workspace_paths": ["/share/a.cif"],
        },
        user_id="u1",
    )

    content = table.add_event.call_args.args[3]
    assert content == {
        "content": "看图",
        "files": ["https://oss.example.com/chat/data.csv"],
        "images": ["https://oss.example.com/chat/image.png"],
        "workspace_paths": ["/share/a.cif"],
    }


def test_chat_history_restores_user_message_images_from_top_level_event() -> None:
    messages = ChatHistoryConverter.events_to_messages(
        [
            {
                "source": "User",
                "type": "query",
                "content": "看图",
                "images": ["https://oss.example.com/chat/image.png"],
            }
        ]
    )

    assert messages[0].content == "看图"
    assert messages[0].images[0].url == "https://oss.example.com/chat/image.png"


def test_chat_history_restores_user_message_images_from_content_json() -> None:
    messages = ChatHistoryConverter.events_to_messages(
        [
            {
                "source": "User",
                "type": "query",
                "content": {
                    "content": "看图",
                    "images": ["https://oss.example.com/chat/image.png"],
                },
            }
        ]
    )

    assert messages[0].content == "看图"
    assert messages[0].images[0].url == "https://oss.example.com/chat/image.png"
